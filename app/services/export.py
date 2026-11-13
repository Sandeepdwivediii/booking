"""Judge export serializer (problem section 6 / DATA_DICTIONARY section 4).

Draft: top-level status DRAFT, every item PLANNED with booking null.
Booked: top-level status BOOKED, every item BOOKED with booking {"reference": "<10 digits>"}.
Output is checked with the frozen contract checkers; a malformed export is never served.
"""

from __future__ import annotations

import copy

from app.contracts import booked_trip_shape_errors, trip_shape_errors

TRIP_KEY_ORDER = ("trip_id", "traveler", "country", "start_date", "end_date", "budget_inr", "total_inr", "status", "items")
ITEM_KEY_ORDER = ("item_id", "type", "role", "name", "city", "status", "catalog_id", "room_id",
                  "start", "end", "quantity", "price_inr", "booking", "sources", "warnings")


class ExportError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems[:5]))
        self.problems = problems


def _ordered(d: dict, order: tuple[str, ...]) -> dict:
    out = {k: d[k] for k in order if k in d}
    out.update({k: v for k, v in d.items() if k not in out})  # keep allowed extra fields
    return out


def _serialize(trip: dict) -> dict:
    t = _ordered(trip, TRIP_KEY_ORDER)
    t["items"] = [_ordered(i, ITEM_KEY_ORDER) for i in t["items"]]
    return t


def draft_export(trip: dict) -> dict:
    t = copy.deepcopy(trip)
    t["status"] = "DRAFT"
    for item in t["items"]:
        item["status"], item["booking"] = "PLANNED", None
    problems = trip_shape_errors(t, draft=True)
    if problems:
        raise ExportError(problems)
    return _serialize(t)


def booked_export(trip: dict, reference: str) -> dict:
    """Immutable booked snapshot/export for a confirmed reservation (used by confirm in Phase 6)."""
    t = copy.deepcopy(trip)
    t["status"] = "BOOKED"
    for item in t["items"]:
        item["status"], item["booking"] = "BOOKED", {"reference": reference}
    problems = booked_trip_shape_errors(t, reference)
    if problems:
        raise ExportError(problems)
    return _serialize(t)
