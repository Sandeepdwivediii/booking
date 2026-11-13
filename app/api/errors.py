"""Error envelope {"error": {"code", "message", "details"}}; never leaks tracebacks or raw input."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.contracts import API_ERROR_STATUS, DomainError

log = logging.getLogger(__name__)
MAX_DETAILS = 25


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def envelope(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details}})


def _clean_validation(errors) -> list[dict]:
    out = []
    for e in errors[:MAX_DETAILS]:  # drop "input"/"ctx": may be huge or non-serializable
        out.append({"loc": [str(p) for p in e.get("loc", ())], "msg": str(e.get("msg", ""))[:200],
                    "type": str(e.get("type", ""))})
    return out


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return envelope(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return envelope(422, "VALIDATION_ERROR", "Invalid request.", _clean_validation(exc.errors()))

    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError):
        status = API_ERROR_STATUS.get(exc.code, 500)
        return envelope(status, exc.code, exc.message, exc.details or None)

    @app.exception_handler(OperationalError)
    @app.exception_handler(DBAPIError)
    async def _db(_: Request, exc: Exception):
        log.warning("database error: %s", type(exc).__name__)
        return envelope(503, "SERVICE_UNAVAILABLE", "Database is not available.")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = "NOT_FOUND" if exc.status_code == 404 else "INVALID_REQUEST"
        return envelope(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        log.exception("unhandled error")
        return envelope(500, "INTERNAL_ERROR", "Unexpected server error.")
