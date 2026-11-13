"""Process-wide runtime state: DB engine/session factory and the loaded domain.

Readiness is re-checked lazily, so the API recovers when PostgreSQL or the catalog becomes
available after startup (SY-19) without serving anything in the meantime.
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from app.api.errors import ApiError
from app.config import Settings
from app.contracts import DomainPort
from app.db import create_schema, make_engine, make_sessionmaker, ping
from app.services.domain_provider import DomainLoader

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, settings: Settings, domain_loader: DomainLoader):
        self.settings = settings
        self.engine: Engine = make_engine(settings.database_url)
        self.sessionmaker: sessionmaker = make_sessionmaker(self.engine)
        self._domain_loader = domain_loader
        self.domain: DomainPort | None = None
        self.domain_error: str | None = "not loaded"
        self.schema_ready = False
        self._lock = threading.Lock()

    def ensure_db(self) -> bool:
        if not ping(self.engine):
            return False
        if not self.schema_ready:
            with self._lock:
                if not self.schema_ready:
                    try:
                        create_schema(self.engine)
                        self.schema_ready = True
                    except Exception:
                        log.exception("schema creation failed")
                        return False
        return True

    def ensure_domain(self) -> bool:
        if self.domain is None:
            with self._lock:
                if self.domain is None:
                    self.domain, self.domain_error = self._domain_loader(self.settings.catalog_dir)
                    if self.domain is None:
                        log.warning("domain not ready: %s", self.domain_error)
        return self.domain is not None

    def require_domain(self) -> DomainPort:
        if not self.ensure_domain():
            raise ApiError(503, "CATALOG_UNAVAILABLE", "Catalog/planner is not ready.", {"reason": self.domain_error})
        return self.domain  # type: ignore[return-value]

    def require_db(self) -> None:
        if not self.ensure_db():
            raise ApiError(503, "SERVICE_UNAVAILABLE", "Database is not ready.")

    def dispose(self) -> None:
        self.engine.dispose()
