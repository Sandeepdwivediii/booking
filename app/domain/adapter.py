"""DomainPort implementation (contract v1.1.0) and the factory the platform calls at startup.

Phase status: options(), build() and validate() are real. alternatives()/change() validate their
arguments but raise NotImplementedError for real work until Phase 5 (no fake results).
"""

from __future__ import annotations

from pathlib import Path

from app.domain.catalog import Catalog, load_catalog
from app.domain.edits import apply_change, list_alternatives
from app.domain.planner import build_trip, city_plan_options, rebuild_city_plan, recommend_packages
from app.domain.rescue import rescue
from app.domain.validator import validate_trip


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
        return build_trip(self._catalog, request, trip_id)

    def validate(self, trip: dict, mode: str) -> list[dict]:
        return validate_trip(self._catalog, trip, mode)

    def alternatives(self, trip: dict, component: str, filters: dict) -> list[dict]:
        return list_alternatives(self._catalog, trip, component, filters)

    def change(self, trip: dict, change: dict) -> dict:
        return apply_change(self._catalog, trip, change)

    # ---- G3 extension (NOT part of frozen DomainPort v1.1.0; proposal in docs/HANDOFF_TANISH.md)
    def city_plan_options(self, trip: dict) -> list[dict]:
        return city_plan_options(self._catalog, trip)

    def rebuild_city_plan(self, trip: dict, cities: list, nights: list) -> dict:
        return rebuild_city_plan(self._catalog, trip, cities, nights)

    # ---- G4 extension (NOT part of frozen DomainPort v1.1.0)
    def recommend(self, request: dict, trip_id: str) -> dict:
        return recommend_packages(self._catalog, request, trip_id)

    # ---- Budget Rescue (NOT part of frozen DomainPort v1.1.0)
    def rescue(self, trip: dict, target_budget_inr: int, locked_attraction_ids=(), max_changes: int = 3) -> dict:
        return rescue(self._catalog, trip, target_budget_inr, locked_attraction_ids, max_changes)


def create_domain(catalog_dir: str | Path) -> Domain:
    """Load the catalog and return the domain. Raises CatalogUnavailableError if it is unusable."""
    return Domain(load_catalog(catalog_dir))
