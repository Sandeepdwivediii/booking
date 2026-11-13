"""G4 (Phase 10) Cheapest / Fastest / Best-rated recommendations.

Uses Tanish's optional `domain.recommend(request, trip_id)` (BuildResult + `recommendations`). Previews persist
nothing. Selection never trusts a client trip or price: the server recomputes the recommendations for the same
request and saves only the chosen one. The package id is uuid5(Idempotency-Key + request hash), so a repeated
or racing selection hits the same primary key and can never create a second draft (no schema change).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.api.errors import ApiError
from app.contracts import build_result_shape_errors, trip_shape_errors
from app.models import AppPackage
from app.services.packages import get_reservation_for, insert_draft, package_view, transaction
from app.services.transactions import request_hash

log = logging.getLogger(__name__)
LABELS = ("cheapest", "fastest", "best_rated")
_NS = uuid.UUID("6f1c2b8e-4d1a-4a8e-9a57-0c3f5d2e7b10")  # fixed namespace for selection package ids


def _rec_errors(rec, request: dict, trip_id: str, domain) -> list[str]:
    if not isinstance(rec, dict) or rec.get("label") not in LABELS:
        return ["bad recommendation label"]
    trip = rec.get("trip")
    errs = trip_shape_errors(trip)
    if errs:
        return errs
    if not all(type(rec.get(k)) is int for k in ("total_inr", "travel_minutes", "star_nights")):
        errs.append("metrics must be integers")
    if type(rec.get("average_stars")) not in (int, float):
        errs.append("average_stars must be a number")
    if trip["trip_id"] != trip_id or rec.get("total_inr") != trip["total_inr"]:
        errs.append("trip id/total mismatch")
    if trip["total_inr"] > request["budget_inr"]:
        errs.append("recommendation over budget")
    if not errs and domain.validate(trip, "initial"):
        errs.append("recommendation fails validation")
    return errs


def recommend(domain, request: dict, trip_id: str) -> tuple[dict, list[dict]]:
    fn = getattr(domain, "recommend", None)
    if not callable(fn):
        raise ApiError(503, "SERVICE_UNAVAILABLE", "Recommendations are not available right now.")
    result = fn(request, trip_id)
    if not isinstance(result, dict):
        raise ApiError(500, "INTERNAL_ERROR", "Planner returned invalid recommendations.")
    base = {k: v for k, v in result.items() if k != "recommendations"}
    recs = result.get("recommendations")
    problems = build_result_shape_errors(base, request, trip_id)
    if not isinstance(recs, list):
        problems.append("recommendations is not a list")
    elif base.get("status") == "BUILT":
        if sorted(r.get("label") for r in recs if isinstance(r, dict)) != sorted(LABELS):
            problems.append("expected one cheapest, fastest and best_rated recommendation")
        for r in recs:
            problems += _rec_errors(r, request, trip_id, domain)
    elif recs:
        problems.append("recommendations without a BUILT package")
    if problems:  # never show or persist a fabricated package
        log.error("domain.recommend violated the contract: %s", problems[:5])
        raise ApiError(500, "INTERNAL_ERROR", "Planner returned invalid recommendations.")
    return base, recs


def preview(domain, request: dict) -> dict:
    base, recs = recommend(domain, request, str(uuid.uuid4()))  # preview id is never persisted
    return {**base, "recommendations": recs}


def _existing(factory: sessionmaker, pid: str) -> dict | None:
    with transaction(factory) as session:
        pkg = session.get(AppPackage, pid)
        return package_view(pkg, get_reservation_for(session, pid)) if pkg else None


def select(factory: sessionmaker, domain, request: dict, choice: str, key: str) -> tuple[int, dict]:
    pid = str(uuid.uuid5(_NS, f"{key}|{request_hash({'select': request, 'choice': choice})}"))
    found = _existing(factory, pid)
    if found:  # repeated selection: the one draft it already created
        return 200, found
    base, recs = recommend(domain, request, pid)
    if base["status"] != "BUILT":  # honest OVER_BUDGET / NO_PACKAGE, nothing saved
        return 200, base
    trip = next(r["trip"] for r in recs if r["label"] == choice)
    try:
        with transaction(factory) as session:
            insert_draft(session, pid, request, trip)
    except IntegrityError:  # a concurrent identical selection won the insert
        found = _existing(factory, pid)
        if found:
            return 200, found
        raise
    return 201, {"package_id": pid, "version": 1, "status": "DRAFT", "trip": trip, "choice": choice}
