"""TEST-ONLY contract fixtures and a fake DomainPort.

Synthetic data shaped like the contract. Never import from production code; it is not a
catalog and must not be used to serve real packages.
"""

from __future__ import annotations

import copy
from datetime import date, timedelta
from pathlib import Path

from app.contracts import InvalidRequestError, build_request_errors

# Absolute paths so tests never depend on the current working directory.
REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_DIR = REPO_ROOT / "data" / "catalog"

MENON_REQUEST = {
    "departure_city": "BLR",
    "destination_country": "Japan",
    "start_date": "2027-04-10",
    "nights": 6,
    "adults": 2,
    "children_ages": [6],
    "budget_inr": 350000,
}

TRIP_ID = "3f2b8c1e-0000-4000-8000-000000000001"
REFERENCE = "0123456789"  # leading zero on purpose (BK-06)


def _item(item_id, type_, role, name, city, catalog_id, start, end, qty, price, room_id=None):
    return {
        "item_id": item_id, "type": type_, "role": role, "name": name, "city": city,
        "status": "PLANNED", "catalog_id": catalog_id, "room_id": room_id,
        "start": start, "end": end, "quantity": qty, "price_inr": price,
        "booking": None, "sources": [], "warnings": [],
    }


def sample_trip(trip_id: str = TRIP_ID) -> dict:
    """Menon-shaped 6-night trip: Tokyo 10-13 (3 nights), Kyoto 13-16 (3 nights).
    Arrival 10, full 11 12, change 13, full 14 15, return 16. One attraction per full day."""
    items = [
        _item("i1", "flight", "outbound_flight", "FAKE 100 BLR to NRT", "Tokyo", "FAKE-FL-1",
              "2027-04-09T23:50", "2027-04-10T10:30", 3, 75600),
        _item("i2", "transfer", "arrival_transfer", "Fake airport taxi", "Tokyo", "FAKE-TR-1",
              "2027-04-10T11:30", "2027-04-10T12:45", 1, 13440),
        _item("i3", "hotel", "hotel_stay", "Fake Tokyo Hotel", "Tokyo", "FAKE-HTL-1",
              "2027-04-10", "2027-04-13", 1, 30000, room_id="FAKE-RM-1"),
        _item("i4", "attraction", "attraction_visit", "Fake Museum", "Tokyo", "FAKE-ATT-1",
              "2027-04-11T10:00", "2027-04-11T11:30", 3, 1500),
        _item("i5", "attraction", "attraction_visit", "Fake Tower", "Tokyo", "FAKE-ATT-2",
              "2027-04-12T10:00", "2027-04-12T11:00", 3, 2000),
        _item("i6", "transfer", "intercity_transfer", "Fake train", "Kyoto", "FAKE-TR-2",
              "2027-04-13T10:00", "2027-04-13T12:15", 3, 40000),
        _item("i7", "hotel", "hotel_stay", "Fake Kyoto Hotel", "Kyoto", "FAKE-HTL-2",
              "2027-04-13", "2027-04-16", 1, 27000, room_id="FAKE-RM-2"),
        _item("i8", "attraction", "attraction_visit", "Fake Temple", "Kyoto", "FAKE-ATT-3",
              "2027-04-14T09:00", "2027-04-14T10:30", 3, 900),
        _item("i9", "attraction", "attraction_visit", "Fake Shrine", "Kyoto", "FAKE-ATT-4",
              "2027-04-15T09:00", "2027-04-15T10:00", 3, 0),
        _item("i10", "transfer", "departure_transfer", "Fake airport bus", "Kyoto", "FAKE-TR-3",
              "2027-04-16T08:00", "2027-04-16T09:30", 3, 6000),
        _item("i11", "flight", "return_flight", "FAKE 200 KIX to BLR", "Bengaluru", "FAKE-FL-2",
              "2027-04-16T13:00", "2027-04-16T19:00", 3, 70000),
    ]
    return {
        "trip_id": trip_id,
        "traveler": {"name": None, "home_airport": "BLR", "adults": 2, "children": 1, "child_ages": [6]},
        "country": "Japan",
        "start_date": "2027-04-10",
        "end_date": "2027-04-16",
        "budget_inr": 350000,
        "total_inr": sum(i["price_inr"] for i in items),
        "items": items,
    }


SAMPLE_TOTAL = sample_trip()["total_inr"]


def booked_trip(reference: str = REFERENCE) -> dict:
    t = sample_trip()
    t["status"] = "BOOKED"
    for it in t["items"]:
        it.update(status="BOOKED", booking={"reference": reference})
    return t


def built_result(trip_id: str = TRIP_ID) -> dict:
    return {"status": "BUILT", "trip": sample_trip(trip_id), "cheapest_total_inr": SAMPLE_TOTAL,
            "shortfall_inr": None, "reason_code": None, "reason": None, "suggestion": None}


def over_budget_result(budget: int, cheapest: int) -> dict:
    return {"status": "OVER_BUDGET", "trip": None, "cheapest_total_inr": cheapest,
            "shortfall_inr": cheapest - budget, "reason_code": None,
            "reason": "No complete package fits the budget.",
            "suggestion": f"Increase the budget to at least INR {cheapest} or reduce nights."}


def no_package_result(reason_code: str = "NO_OUTBOUND_FLIGHT") -> dict:
    return {"status": "NO_PACKAGE", "trip": None, "cheapest_total_inr": None, "shortfall_inr": None,
            "reason_code": reason_code,
            "reason": "No flight from BLR lands in Japan on 2027-04-10 with 3 free seats.",
            "suggestion": "Try another start date."}


class FakeDomain:
    """Deterministic TEST-ONLY DomainPort double mirroring the contract's documented behaviour."""

    FIRST_NIGHT, LAST_DEPARTURE = date(2027, 4, 1), date(2027, 4, 20)

    def options(self) -> dict:
        return {
            "departure_cities": [{"airport": "BLR", "city": "Bengaluru"}],
            "countries": [{"name": "Japan", "code": "JP", "timezone": "Asia/Tokyo",
                           "cities": ["Tokyo", "Kyoto"]}],
            "rules": {"min_nights": 3, "max_nights": 10, "min_nights_per_city": 2,
                      "first_night": "2027-04-01", "last_departure": "2027-04-20"},
        }

    def build(self, request: dict, trip_id: str) -> dict:
        errs = build_request_errors(request)
        if errs:
            raise InvalidRequestError("bad build request", {"errors": errs})
        start = date.fromisoformat(request["start_date"])
        if start < self.FIRST_NIGHT or start + timedelta(days=request["nights"]) > self.LAST_DEPARTURE:
            res = no_package_result("OUTSIDE_DATA_WINDOW")
            res["reason"] = "Flights and rooms exist only from 2027-04-01 to 2027-04-20."
            return res
        if request["nights"] == 9:
            return no_package_result()
        if request["budget_inr"] < SAMPLE_TOTAL:
            return over_budget_result(request["budget_inr"], SAMPLE_TOTAL)
        res = built_result(trip_id)
        res["trip"]["budget_inr"] = request["budget_inr"]
        return res

    def validate(self, trip: dict, mode: str) -> list[dict]:
        if mode not in ("initial", "modified", "confirm"):
            raise InvalidRequestError(f"unknown mode {mode!r}")
        if trip["total_inr"] > trip["budget_inr"]:
            return [{"code": "OVER_BUDGET", "message": "Total exceeds budget.", "item_id": None}]
        return []

    def alternatives(self, trip: dict, component: str, filters: dict) -> list[dict]:
        if component != "attraction":
            return []
        try:
            day = date.fromisoformat(filters["date"])
        except (KeyError, TypeError, ValueError):
            raise InvalidRequestError("attraction alternatives need date YYYY-MM-DD") from None
        if not (date.fromisoformat(trip["start_date"]) <= day < date.fromisoformat(trip["end_date"])):
            return []  # return day or outside trip
        d = day.isoformat()
        return [{"component": "attraction",
                 "change": {"op": "add_attraction", "attraction_id": "FAKE-ATT-9", "date": d, "start_time": "13:00"},
                 "name": "Fake Garden", "catalog_id": "FAKE-ATT-9", "room_id": None,
                 "start": f"{d}T13:00", "end": f"{d}T14:00",
                 "price_inr": 900, "new_total_inr": trip["total_inr"] + 900, "price_diff_inr": 900}]

    def change(self, trip: dict, change: dict) -> dict:
        if change.get("op") != "remove_attraction":
            return {"ok": False, "trip": None, "adjustments": [],
                    "violations": [{"code": "UNSUPPORTED_IN_FAKE", "message": "fake", "item_id": None}]}
        if not any(i["item_id"] == change["item_id"] and i["type"] == "attraction" for i in trip["items"]):
            return {"ok": False, "trip": None, "adjustments": [],
                    "violations": [{"code": "UNKNOWN_ITEM", "message": "No such attraction item.",
                                    "item_id": change["item_id"]}]}
        new = copy.deepcopy(trip)
        new["items"] = [i for i in new["items"] if i["item_id"] != change["item_id"]]
        new["total_inr"] = sum(i["price_inr"] for i in new["items"])
        return {"ok": True, "trip": new, "violations": [], "adjustments": []}
