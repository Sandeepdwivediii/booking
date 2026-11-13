"""DomainPort implementation (contract v1.1.0) and the factory the platform calls at startup.

Phase status: options() is real. build/validate/alternatives/change are NOT implemented yet
(Phases 2-5) and raise NotImplementedError instead of returning fake results.
"""

from __future__ import annotations

from pathlib import Path

from app.domain.catalog import Catalog, load_catalog


class Domain:
    """Pure domain service over an immutable Catalog; safe to share across threads."""

    def __init__(self, catalog: Catalog):
        self._catalog = catalog

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    def options(self) -> dict:
        return self._catalog.options()

    def build(self, request: dict, trip_id: str) -> dict:
        raise NotImplementedError("DomainPort.build: planner not implemented yet (Phase 3)")

    def validate(self, trip: dict, mode: str) -> list[dict]:
        raise NotImplementedError("DomainPort.validate: validator not implemented yet (Phase 2-3)")

    def alternatives(self, trip: dict, component: str, filters: dict) -> list[dict]:
        raise NotImplementedError("DomainPort.alternatives: not implemented yet (Phase 5)")

    def change(self, trip: dict, change: dict) -> dict:
        raise NotImplementedError("DomainPort.change: not implemented yet (Phase 5)")


def create_domain(catalog_dir: str | Path) -> Domain:
    """Load the catalog and return the domain. Raises CatalogUnavailableError if it is unusable."""
    return Domain(load_catalog(catalog_dir))
