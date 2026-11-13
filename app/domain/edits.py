"""Alternatives and change simulation — Phase 3 SKELETON (finished in Phase 5).

Argument validation follows the contract now so the platform can integrate error paths;
the actual edit/alternative logic raises NotImplementedError instead of returning fake data.
"""

from __future__ import annotations

from datetime import date

from app.contracts import ALTERNATIVE_COMPONENTS, CHANGE_OPS, InvalidRequestError
from app.domain.validator import violation


def list_alternatives(catalog, trip: dict, component: str, filters: dict) -> list[dict]:
    if component not in ALTERNATIVE_COMPONENTS:
        raise InvalidRequestError(f"unknown component {component!r}")
    filters = filters or {}
    if component == "hotel" and not filters.get("item_id"):
        raise InvalidRequestError("hotel alternatives need filters.item_id")
    if component == "attraction":
        raw = filters.get("date")
        try:
            if not isinstance(raw, str) or len(raw) != 10:
                raise ValueError
            date.fromisoformat(raw)
        except ValueError:
            raise InvalidRequestError("attraction alternatives need filters.date as YYYY-MM-DD") from None
    raise NotImplementedError("alternatives: Phase 5")


def apply_change(catalog, trip: dict, change: dict) -> dict:
    op = change.get("op") if isinstance(change, dict) else None
    if op not in CHANGE_OPS:
        return {"ok": False, "trip": None, "violations": [violation("UNKNOWN_OP", f"unknown change op {op!r}")],
                "adjustments": []}
    raise NotImplementedError(f"change {op}: Phase 5")
