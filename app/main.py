"""FastAPI application factory. Run: uvicorn app.main:app --reload"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.errors import envelope, install_error_handlers
from app.api.routes import router
from app.config import MAX_BODY_BYTES, REPO_ROOT, Settings, get_settings
from app.services.domain_provider import DomainLoader, load_domain
from app.services.runtime import Runtime

STATIC_DIR = REPO_ROOT / "app" / "static"
log = logging.getLogger("app")


def create_app(settings: Settings | None = None, domain_loader: DomainLoader | None = None) -> FastAPI:
    settings = settings or get_settings()
    loader = domain_loader or load_domain

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        rt = Runtime(settings, loader)
        app.state.runtime = rt
        # Startup never crashes on missing DB/catalog: health reports 503 until both are ready.
        if not rt.ensure_db():
            log.warning("database not ready at startup; /api/health will report not_ready")
        rt.ensure_domain()
        yield
        rt.dispose()

    app = FastAPI(title="Build My Holiday", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    install_error_handlers(app)

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
            return envelope(422, "VALIDATION_ERROR", "Request body is too large or malformed.")
        return await call_next(request)

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
