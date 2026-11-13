"""Atomic PATCH (edit) and CONFIRM machinery.

Every mutation runs in ONE short transaction that first takes the package row lock
(SELECT ... FOR UPDATE). Order inside the lock:
  1. idempotency replay (same key + same request hash -> stored response; different hash -> 409)
  2. state checks (BOOKED -> ALREADY_BOOKED / existing reservation)
  3. exact If-Match version check (PATCH)
  4. domain call on the locked, latest trip (change / validate)
  5. write package + reservation + idempotency record, then COMMIT (all or nothing)
Version conflicts and lock/availability errors are not stored, so a retry can still succeed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from datetime import date
from typing import Callable

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import ApiError
from app.contracts import (
    CITY_PLAN_OP,
    ALTERNATIVE_COMPONENTS, CHANGE_OPS, REFERENCE_RE, DomainPort, alternatives_shape_errors,
    change_result_shape_errors, trip_shape_errors, violation_shape_errors,
)
from app.models import AppIdempotencyRecord, AppPackage, AppReservation
from app.services.export import ExportError, booked_export
from app.services.packages import get_package, get_reservation_for, package_view, transaction

log = logging.getLogger(__name__)

LOCK_TIMEOUT = "5s"
MAX_REFERENCE_ATTEMPTS = 10
IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9._:-]{8,200}")
Outcome = tuple[int, dict]  # (HTTP status, JSON body)


def checkpoint(name: str) -> None:
    """No-op hook; tests monkeypatch it to inject crashes at named points inside a transaction."""


def request_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def parse_if_match(value: str | None) -> int:
    if value is None:
        raise ApiError(428, "PRECONDITION_REQUIRED", "If-Match header with the current version is required.")
    raw = value.strip()
    if raw.startswith("W/"):
        raw = raw[2:]
    raw = raw.strip('"')
    if not raw.isdigit() or len(raw) > 9:
        raise ApiError(422, "VALIDATION_ERROR", 'If-Match must be a version number, e.g. "3".')
    return int(raw)


def parse_idempotency_key(value: str | None) -> str:
    if value is None or not IDEMPOTENCY_KEY_RE.fullmatch(value):
        raise ApiError(422, "VALIDATION_ERROR", "Idempotency-Key header is required (8-200 chars: letters, digits, . _ : -).")
    return value


def _lock_timeout(session: Session) -> None:
    session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))


def _replay(session: Session, package_id: str, operation: str, key: str, req_hash: str) -> Outcome | None:
    rec = session.execute(select(AppIdempotencyRecord).where(
        AppIdempotencyRecord.package_id == package_id,
        AppIdempotencyRecord.operation == operation,
        AppIdempotencyRecord.idempotency_key == key,
    )).scalar_one_or_none()
    if rec is None:
        return None
    if rec.request_hash != req_hash:
        raise ApiError(409, "IDEMPOTENCY_CONFLICT", "This Idempotency-Key was already used with a different request.")
    return rec.response_status, rec.response_body


def _remember(session: Session, package_id: str, operation: str, key: str, req_hash: str, outcome: Outcome) -> Outcome:
    session.add(AppIdempotencyRecord(package_id=package_id, operation=operation, idempotency_key=key,
                                     request_hash=req_hash, response_status=outcome[0], response_body=outcome[1]))
    session.flush()
    return outcome


def _error_body(code: str, message: str, details=None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


# ---------------------------------------------------------------- PATCH

# G3 extension (contract v1.2.0): not a DomainPort.change op; routed to the optional domain method.


def _extension(domain, name: str):
    fn = getattr(domain, name, None)
    if not callable(fn):
        raise ApiError(503, "SERVICE_UNAVAILABLE", "Changing cities is not available right now.")
    return fn


def _apply_change(domain, trip: dict, change: dict) -> dict:
    if change["op"] == CITY_PLAN_OP:
        return _extension(domain, "rebuild_city_plan")(trip, list(change["cities"]), list(change["nights"]))
    return domain.change(trip, change)


def edit_package(factory: sessionmaker, domain: DomainPort, package_id: str, if_match: int, key: str,
                 change: dict) -> Outcome:
    if not isinstance(change, dict) or change.get("op") not in CHANGE_OPS | {CITY_PLAN_OP}:
        raise ApiError(422, "VALIDATION_ERROR", f"Change op must be one of: {', '.join(sorted(CHANGE_OPS | {CITY_PLAN_OP}))}.")
    req_hash = request_hash({"change": change})
    with transaction(factory) as session:
        _lock_timeout(session)
        pkg = get_package(session, package_id, lock=True)
        replayed = _replay(session, package_id, "PATCH", key, req_hash)
        if replayed:
            return replayed
        if pkg.status == "BOOKED":
            raise ApiError(409, "ALREADY_BOOKED", "This package is already booked and can no longer be changed.")
        if pkg.version != if_match:
            raise ApiError(409, "VERSION_CONFLICT", "The package was changed elsewhere. Reload and try again.",
                           {"current_version": pkg.version})

        original = pkg.trip
        result = _apply_change(domain, original, change)
        problems = change_result_shape_errors(result, original)
        if problems:
            log.error("domain.change violated the contract: %s", problems[:5])
            raise ApiError(500, "INTERNAL_ERROR", "Planner returned an invalid change result.")

        if not result["ok"]:  # rejected: package row untouched; the outcome itself is replayable
            body = _error_body("INVALID_CHANGE", "The change was refused.", result["violations"])
            return _remember(session, package_id, "PATCH", key, req_hash, (422, body))

        new_trip = result["trip"]
        pkg.trip, pkg.total_inr, pkg.version = new_trip, new_trip["total_inr"], pkg.version + 1
        pkg.updated_at = func.now()
        session.flush()
        checkpoint("patch:after_update")
        session.refresh(pkg)
        body = {**package_view(pkg, None), "adjustments": result["adjustments"]}
        return _remember(session, package_id, "PATCH", key, req_hash, (200, body))


# ---------------------------------------------------------------- CONFIRM

def reservation_view(res: AppReservation) -> dict:
    return {"reference": res.reference, "package_id": res.package_id, "status": "BOOKED",
            "booked_at": res.created_at.isoformat(), "trip": res.snapshot}


def _new_reference(rng: Callable[[], str]) -> str:
    ref = rng()
    if not isinstance(ref, str) or not REFERENCE_RE.fullmatch(ref):
        raise ValueError("reference generator must return 10 ASCII digits as str")
    return ref


def random_reference() -> str:
    return f"{secrets.randbelow(10**10):010d}"  # TEXT, leading zeros kept


def confirm_package(factory: sessionmaker, domain: DomainPort, package_id: str, key: str,
                    rng: Callable[[], str] | None = None) -> Outcome:
    rng = rng or (lambda: random_reference())  # resolved at call time (tests may patch random_reference)
    req_hash = request_hash({"confirm": package_id})
    with transaction(factory) as session:
        _lock_timeout(session)
        pkg = get_package(session, package_id, lock=True)
        replayed = _replay(session, package_id, "CONFIRM", key, req_hash)
        if replayed:
            return replayed
        if pkg.status == "BOOKED":  # BK-09: any key returns the one existing reservation
            existing = get_reservation_for(session, package_id)
            if existing is None:
                raise ApiError(500, "INTERNAL_ERROR", "Booked package has no reservation.")
            return _remember(session, package_id, "CONFIRM", key, req_hash, (200, reservation_view(existing)))

        trip = pkg.trip  # latest committed draft, read under the lock
        violations = domain.validate(trip, "confirm")  # authoritative re-check against the catalog (SY-18)
        if violation_shape_errors(violations):
            log.error("confirm: invalid validate() output for %s", package_id)
            raise ApiError(500, "INTERNAL_ERROR", "Package could not be validated.")
        if violations:
            body = _error_body("CONFIRM_REJECTED", "The package is no longer valid and cannot be booked.", violations)
            return _remember(session, package_id, "CONFIRM", key, req_hash, (422, body))
        if trip_shape_errors(trip, draft=True):  # domain says valid but shape is broken: inconsistency, never book
            log.error("confirm: domain accepted a malformed stored draft for %s", package_id)
            raise ApiError(500, "INTERNAL_ERROR", "Package could not be validated.")

        reservation = None
        for _ in range(MAX_REFERENCE_ATTEMPTS):
            ref = _new_reference(rng)
            try:
                snapshot = booked_export(trip, ref)
            except ExportError as e:
                log.error("confirm: booked snapshot invalid: %s", e.problems[:5])
                raise ApiError(500, "INTERNAL_ERROR", "Package could not be booked.") from None
            try:
                with session.begin_nested():  # SAVEPOINT: a reference collision must not abort the txn
                    reservation = AppReservation(reference=ref, package_id=package_id, snapshot=snapshot)
                    session.add(reservation)
                    session.flush()
                break
            except IntegrityError:
                reservation = None
                if session.execute(select(AppReservation.reference).where(AppReservation.reference == ref)).first():
                    continue  # BK-05: collision with another booking -> new reference
                raise
        if reservation is None:
            raise ApiError(503, "SERVICE_UNAVAILABLE", "Could not allocate a reservation number. Please retry.")

        checkpoint("confirm:after_reservation_insert")
        pkg.status, pkg.updated_at = "BOOKED", func.now()
        session.flush()
        checkpoint("confirm:after_status_update")
        session.refresh(reservation)
        return _remember(session, package_id, "CONFIRM", key, req_hash, (201, reservation_view(reservation)))


# ---------------------------------------------------------------- lookup

def get_reservation(factory: sessionmaker, reference: str) -> dict:
    if not isinstance(reference, str) or not REFERENCE_RE.fullmatch(reference):
        raise ApiError(404, "NOT_FOUND", "Reservation not found.")
    with transaction(factory) as session:
        res = session.get(AppReservation, reference)
        if res is None:
            raise ApiError(404, "NOT_FOUND", "Reservation not found.")
        return reservation_view(res)


def get_alternatives(factory: sessionmaker, domain: DomainPort, package_id: str, component: str,
                     filters: dict) -> list[dict]:
    if component not in ALTERNATIVE_COMPONENTS:
        raise ApiError(422, "VALIDATION_ERROR", f"component must be one of: {', '.join(sorted(ALTERNATIVE_COMPONENTS))}.")
    with transaction(factory) as session:
        pkg = get_package(session, package_id)
        status, trip = pkg.status, pkg.trip
    if status == "BOOKED":
        raise ApiError(409, "ALREADY_BOOKED", "This package is already booked and can no longer be changed.")
    alts = domain.alternatives(trip, component, filters)
    problems = alternatives_shape_errors(alts, component)
    if problems:
        log.error("domain.alternatives violated the contract: %s", problems[:5])
        raise ApiError(500, "INTERNAL_ERROR", "Planner returned invalid alternatives.")
    return alts


# ---------------------------------------------------------------- G3 city plans (read-only)

def current_city_plan(trip: dict) -> dict:
    stays = sorted((i for i in trip["items"] if i["role"] == "hotel_stay"), key=lambda i: i["start"])
    nights = [(date.fromisoformat(i["end"][:10]) - date.fromisoformat(i["start"][:10])).days for i in stays]
    return {"cities": [i["city"] for i in stays], "nights": nights}


def _plan_ok(p) -> bool:
    return (isinstance(p, dict) and isinstance(p.get("cities"), list) and isinstance(p.get("nights"), list)
            and len(p["cities"]) == len(p["nights"]) > 0 and all(isinstance(c, str) and c for c in p["cities"])
            and all(type(n) is int and n > 0 for n in p["nights"])
            and all(type(p.get(k)) is int for k in ("total_inr", "price_diff_inr"))
            and type(p.get("within_budget")) is bool)


def get_city_plans(factory: sessionmaker, domain, package_id: str) -> dict:
    options = _extension(domain, "city_plan_options")
    with transaction(factory) as session:
        pkg = get_package(session, package_id)
        status, trip, version = pkg.status, pkg.trip, pkg.version
    if status == "BOOKED":
        raise ApiError(409, "ALREADY_BOOKED", "This package is already booked and can no longer be changed.")
    plans = options(trip)
    if not isinstance(plans, list) or not all(_plan_ok(p) for p in plans):
        log.error("domain.city_plan_options returned an invalid shape")
        raise ApiError(500, "INTERNAL_ERROR", "Planner returned invalid city plans.")
    current = current_city_plan(trip)
    out = [{"cities": p["cities"], "nights": p["nights"], "total_inr": p["total_inr"],
            "price_diff_inr": p["price_diff_inr"], "within_budget": p["within_budget"],
            "current": p["cities"] == current["cities"] and p["nights"] == current["nights"],
            "change": {"op": CITY_PLAN_OP, "cities": p["cities"], "nights": p["nights"]}} for p in plans]
    return {"package_id": package_id, "version": version, "current": current, "plans": out}
