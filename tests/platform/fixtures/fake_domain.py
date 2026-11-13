"""TEST-ONLY DomainPort double for API tests. Generates a contract-valid synthetic trip for any
request (FAKE-* ids, made-up prices). Never used by production code; not catalog data."""

from __future__ import annotations

import copy
from datetime import date, timedelta

from app.contracts import InvalidRequestError, build_request_errors

OPTIONS = {
    "departure_cities": [
        {"airport": "BLR", "city": "Bengaluru"}, {"airport": "DEL", "city": "Delhi"},
        {"airport": "BOM", "city": "Mumbai"}, {"airport": "MAA", "city": "Chennai"},
    ],
    "countries": [
        {"name": "Japan", "code": "JP", "timezone": "Asia/Tokyo", "cities": ["Tokyo", "Kyoto", "Osaka"]},
        {"name": "United Arab Emirates", "code": "AE", "timezone": "Asia/Dubai", "cities": ["Dubai", "Abu Dhabi"]},
    ],
    "rules": {"min_nights": 3, "max_nights": 10, "min_nights_per_city": 2,
              "first_night": "2027-04-01", "last_departure": "2027-04-20"},
}
HOME = {d["airport"]: d["city"] for d in OPTIONS["departure_cities"]}
NO_PACKAGE_NIGHTS = 9  # trigger: fake returns NO_PACKAGE for 9-night requests


def _item(n, type_, role, city, start, end, qty, price, room_id=None):
    return {"item_id": f"i{n}", "type": type_, "role": role, "name": f"Fake {role}", "city": city,
            "status": "PLANNED", "catalog_id": f"FAKE-{role.upper()}-{n}", "room_id": room_id,
            "start": start, "end": end, "quantity": qty, "price_inr": price,
            "booking": None, "sources": [], "warnings": []}


def make_trip(request: dict, trip_id: str) -> dict:
    travelers = request["adults"] + len(request["children_ages"])
    start = date.fromisoformat(request["start_date"])
    end = start + timedelta(days=request["nights"])
    city = "Tokyo" if request["destination_country"] == "Japan" else "Dubai"
    rooms = (request["adults"] + 1) // 2
    d = lambda x: x.isoformat()  # noqa: E731
    items = [
        _item(1, "flight", "outbound_flight", city, f"{d(start - timedelta(days=1))}T23:00", f"{d(start)}T09:00",
              travelers, 20_000 * travelers),
        _item(2, "transfer", "arrival_transfer", city, f"{d(start)}T10:30", f"{d(start)}T11:30", 1, 5_000),
        _item(3, "hotel", "hotel_stay", city, d(start), d(end), rooms, 8_000 * rooms * request["nights"],
              room_id="FAKE-ROOM-1"),
    ]
    n = 4
    for k in range(1, request["nights"]):  # one attraction per full day
        day = start + timedelta(days=k)
        items.append(_item(n, "attraction", "attraction_visit", city, f"{d(day)}T10:00", f"{d(day)}T11:00",
                           travelers, 500 * travelers))
        n += 1
    items.append(_item(n, "transfer", "departure_transfer", city, f"{d(end)}T06:00", f"{d(end)}T07:00", 1, 5_000))
    items.append(_item(n + 1, "flight", "return_flight", HOME[request["departure_city"]],
                       f"{d(end)}T12:00", f"{d(end)}T20:00", travelers, 20_000 * travelers))
    return {
        "trip_id": trip_id,
        "traveler": {"name": None, "home_airport": request["departure_city"], "adults": request["adults"],
                     "children": len(request["children_ages"]), "child_ages": list(request["children_ages"])},
        "country": request["destination_country"],
        "start_date": d(start), "end_date": d(end),
        "budget_inr": request["budget_inr"],
        "total_inr": sum(i["price_inr"] for i in items),
        "items": items,
    }


class ApiFakeDomain:
    """Records build calls. BUILT if budget >= synthetic total, else OVER_BUDGET; 9 nights -> NO_PACKAGE."""

    def __init__(self):
        self.calls: list[dict] = []

    def options(self) -> dict:
        return copy.deepcopy(OPTIONS)

    def build(self, request: dict, trip_id: str) -> dict:
        if build_request_errors(request):
            raise InvalidRequestError("bad build request")
        self.calls.append(copy.deepcopy(request))
        base = {"trip": None, "cheapest_total_inr": None, "shortfall_inr": None,
                "reason_code": None, "reason": None, "suggestion": None}
        if request["nights"] == NO_PACKAGE_NIGHTS:
            return {**base, "status": "NO_PACKAGE", "reason_code": "NO_HOTEL_AVAILABILITY",
                    "reason": "No hotel has rooms for every night.", "suggestion": "Try fewer nights."}
        trip = make_trip(request, trip_id)
        total = trip["total_inr"]
        if request["budget_inr"] < total:
            return {**base, "status": "OVER_BUDGET", "cheapest_total_inr": total,
                    "shortfall_inr": total - request["budget_inr"],
                    "reason": "No complete package fits the budget.",
                    "suggestion": f"Increase the budget to at least {total} INR."}
        return {**base, "status": "BUILT", "trip": trip, "cheapest_total_inr": total}

    def validate(self, trip: dict, mode: str) -> list[dict]:
        return []

    def alternatives(self, trip: dict, component: str, filters: dict) -> list[dict]:
        return []

    def change(self, trip: dict, change: dict) -> dict:
        return {"ok": False, "trip": None, "adjustments": [],
                "violations": [{"code": "NOT_IMPLEMENTED_IN_FAKE", "message": "fake", "item_id": None}]}
