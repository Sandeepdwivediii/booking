"""Routes: health, catalog options, build (persist BUILT only), get package, judge export."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.errors import ApiError
from app.contracts import booked_trip_shape_errors, build_result_shape_errors
from app.schemas import BuildRequestIn
from app.services.export import ExportError, draft_export
from app.services.packages import (
    get_package, get_reservation_for, insert_draft, package_view, parse_package_id, transaction,
)
from app.services.request_validation import validate_against_options
from app.services.runtime import Runtime

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
