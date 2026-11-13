"""Phase 1 routes: health, catalog options, build (validate -> domain.build -> persist BUILT only)."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.errors import ApiError
from app.contracts import build_result_shape_errors
from app.models import AppPackage
from app.schemas import BuildRequestIn
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
    with rt.sessionmaker.begin() as session:
        session.add(AppPackage(id=trip_id, status="DRAFT", version=1, request=build_request, trip=trip,
                               total_inr=trip["total_inr"], budget_inr=trip["budget_inr"]))
    return JSONResponse(status_code=201, content={"package_id": trip_id, "version": 1, "status": "DRAFT", "trip": trip})
