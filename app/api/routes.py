"""Routes: health, catalog options, build (persist BUILT only), get package, judge export."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Body, Header, Query, Request
from fastapi.responses import JSONResponse

from app.api.errors import ApiError
from app.contracts import booked_trip_shape_errors, build_result_shape_errors
from app.schemas import BuildRequestIn, ChangeIn
from app.services.export import ExportError, draft_export
from app.services.packages import (
    get_package, get_reservation_for, insert_draft, package_view, parse_package_id, transaction,
)
from app.services.request_validation import validate_against_options
from app.services.runtime import Runtime
from app.services.transactions import (
    confirm_package, edit_package, get_alternatives, get_city_plans, get_reservation, parse_idempotency_key, parse_if_match,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _rt(request: Request) -> Runtime:
    return request.app.state.runtime


@router.get("/health")
def health(request: Request):
    rt = _rt(request)
    db_ok, domain_ok = rt.ensure_db(), rt.ensure_domain()
    body = {
        "status": "ready" if db_ok and domain_ok else "not_ready",
        "checks": {"database": "ok" if db_ok else "unavailable",
                   "catalog": "ok" if domain_ok else (rt.domain_error or "unavailable")},
    }
    return JSONResponse(status_code=200 if db_ok and domain_ok else 503, content=body)


@router.get("/catalog/options")
def catalog_options(request: Request):
    return _rt(request).require_domain().options()


@router.post("/packages/build")
def build_package(body: BuildRequestIn, request: Request):
    rt = _rt(request)
    domain = rt.require_domain()
    errors = validate_against_options(body, domain.options())
    if errors:
        raise ApiError(422, "VALIDATION_ERROR", "Invalid request.", errors)
    rt.require_db()  # never build/serve while the database is down (SY-19)

    build_request = body.model_dump()
    trip_id = str(uuid.uuid4())  # created once by the API; persisted as package id
    result = domain.build(build_request, trip_id)
    problems = build_result_shape_errors(result, build_request, trip_id)
    if problems:  # never persist or show a malformed/fake package
        log.error("domain.build returned a result violating the contract: %s", problems[:5])
        raise ApiError(500, "INTERNAL_ERROR", "Planner returned an invalid result.")

    if result["status"] != "BUILT":  # OVER_BUDGET / NO_PACKAGE: nothing persisted
        return JSONResponse(status_code=200, content=result)

    trip = result["trip"]
    with transaction(rt.sessionmaker) as session:
        insert_draft(session, trip_id, build_request, trip)
    return JSONResponse(status_code=201, content={"package_id": trip_id, "version": 1, "status": "DRAFT", "trip": trip},
                        headers={"ETag": '"1"', "Location": f"/api/packages/{trip_id}"})


@router.get("/packages/{package_id}")
def get_package_route(package_id: str, request: Request):
    rt = _rt(request)
    pid = parse_package_id(package_id)
    rt.require_db()
    with transaction(rt.sessionmaker) as session:
        pkg = get_package(session, pid)
        view = package_view(pkg, get_reservation_for(session, pid))
    return JSONResponse(content=view, headers={"ETag": f'"{view["version"]}"', "Cache-Control": "no-store"})


@router.get("/packages/{package_id}/export")
def export_package_route(package_id: str, request: Request):
    rt = _rt(request)
    pid = parse_package_id(package_id)
    rt.require_db()
    with transaction(rt.sessionmaker) as session:
        pkg = get_package(session, pid)
        reservation = get_reservation_for(session, pid)
        status, trip, snapshot = pkg.status, pkg.trip, (reservation.snapshot if reservation else None)
        reference = reservation.reference if reservation else None
    try:
        if status == "BOOKED":
            if snapshot is None:
                raise ExportError(["BOOKED package has no reservation snapshot"])
            problems = booked_trip_shape_errors(snapshot, reference)
            if problems:
                raise ExportError(problems)
            body = snapshot
        else:
            body = draft_export(trip)
    except ExportError as e:
        log.error("refusing to export malformed package %s: %s", pid, e.problems[:5])
        raise ApiError(500, "INTERNAL_ERROR", "Package could not be exported.") from None
    return JSONResponse(content=body, headers={
        "Content-Disposition": f'attachment; filename="trip-{pid}.json"', "Cache-Control": "no-store"})


# ---------------------------------------------------------------- edit / confirm / lookup (Phase 3 machinery)

def _json(outcome: tuple[int, dict]) -> JSONResponse:
    status, body = outcome
    headers = {"Cache-Control": "no-store"}
    if "version" in body:
        headers["ETag"] = f'"{body["version"]}"'
    return JSONResponse(status_code=status, content=body, headers=headers)


@router.get("/packages/{package_id}/alternatives")
def alternatives_route(package_id: str, request: Request,
                       component: str = Query(..., max_length=32),
                       item_id: str | None = Query(None, max_length=64),
                       date: str | None = Query(None, max_length=10),
                       limit: int = Query(20, ge=1, le=50)):
    rt = _rt(request)
    pid = parse_package_id(package_id)
    domain = rt.require_domain()
    rt.require_db()
    filters = {k: v for k, v in {"item_id": item_id, "date": date, "limit": limit}.items() if v is not None}
    return {"alternatives": get_alternatives(rt.sessionmaker, domain, pid, component, filters)}


@router.get("/packages/{package_id}/city-plans")
def city_plans_route(package_id: str, request: Request):
    rt = _rt(request)
    pid = parse_package_id(package_id)
    domain = rt.require_domain()
    rt.require_db()
    return get_city_plans(rt.sessionmaker, domain, pid)


@router.patch("/packages/{package_id}")
def patch_package_route(package_id: str, request: Request, change: ChangeIn = Body(...),
                        if_match: str | None = Header(None, alias="If-Match"),
                        idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    rt = _rt(request)
    pid = parse_package_id(package_id)
    version, key = parse_if_match(if_match), parse_idempotency_key(idempotency_key)
    domain = rt.require_domain()
    rt.require_db()
    return _json(edit_package(rt.sessionmaker, domain, pid, version, key, change.model_dump(exclude_none=True)))


@router.post("/packages/{package_id}/confirm")
def confirm_route(package_id: str, request: Request,
                  idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    rt = _rt(request)
    pid = parse_package_id(package_id)
    key = parse_idempotency_key(idempotency_key)
    domain = rt.require_domain()
    rt.require_db()
    return _json(confirm_package(rt.sessionmaker, domain, pid, key))


@router.get("/reservations/{reference}")
def reservation_route(reference: str, request: Request):
    rt = _rt(request)
    rt.require_db()
    return JSONResponse(content=get_reservation(rt.sessionmaker, reference), headers={"Cache-Control": "no-store"})
