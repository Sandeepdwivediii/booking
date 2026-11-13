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

    # ------------------------------------------------------------ deterministic edit behaviour
    # Knobs for tests: change_delay (seconds, simulates planning while the row lock is held),
    # confirm_violations (returned by validate in "confirm" mode).
    change_delay: float = 0.0
    confirm_violations: list | None = None

    def validate(self, trip: dict, mode: str) -> list[dict]:
        if mode not in ("initial", "modified", "confirm"):
            raise InvalidRequestError(f"unknown mode {mode!r}")
        if mode == "confirm" and self.confirm_violations:
            return copy.deepcopy(self.confirm_violations)
        if trip["total_inr"] > trip["budget_inr"]:
            return [{"code": "OVER_BUDGET", "message": "Total exceeds budget.", "item_id": None}]
        return []

    def alternatives(self, trip: dict, component: str, filters: dict) -> list[dict]:
        if component not in ("hotel", "outbound_flight", "return_flight", "attraction"):
            raise InvalidRequestError("unknown component")
        total = trip["total_inr"]

        def alt(comp, change, name, cid, price, room=None, start=None, end=None):
            return {"component": comp, "change": change, "name": name, "catalog_id": cid, "room_id": room,
                    "start": start, "end": end, "price_inr": price, "new_total_inr": None, "price_diff_inr": None}

        if component == "hotel":
            item = filters.get("item_id")
            if not any(i["item_id"] == item and i["type"] == "hotel" for i in trip["items"]):
                raise InvalidRequestError("hotel alternatives need a hotel item_id")
            return [alt("hotel", {"op": "replace_hotel", "item_id": item, "hotel_id": "FAKE-HTL-ALT", "room_id": "FAKE-ROOM-ALT"},
                        "Fake Alt Hotel", "FAKE-HTL-ALT", 1000, room="FAKE-ROOM-ALT"),
                    alt("hotel", {"op": "replace_hotel", "item_id": item, "hotel_id": "FAKE-HTL-PRICEY", "room_id": "FAKE-ROOM-X"},
                        "Fake Pricey Hotel", "FAKE-HTL-PRICEY", 10_000_000, room="FAKE-ROOM-X")]
        if component in ("outbound_flight", "return_flight"):
            item = next(i for i in trip["items"] if i["role"] == component)
            return [alt(component, {"op": "replace_flight", "item_id": item["item_id"], "flight_id": f"FAKE-FL-ALT-{component}"},
                        "Fake Alt Flight", f"FAKE-FL-ALT-{component}", item["price_inr"] + 1000)]
        try:
            day = date.fromisoformat(filters["date"])
        except (KeyError, TypeError, ValueError):
            raise InvalidRequestError("attraction alternatives need date YYYY-MM-DD") from None
        if not date.fromisoformat(trip["start_date"]) <= day < date.fromisoformat(trip["end_date"]):
            return []
        d, aid = day.isoformat(), f"FAKE-ATT-NEW-{day.isoformat()}"
        return [alt("attraction", {"op": "add_attraction", "attraction_id": aid, "date": d, "start_time": "14:00"},
                    "Fake Garden", aid, 700, start=f"{d}T14:00", end=f"{d}T15:00")]

    def change(self, trip: dict, change: dict) -> dict:
        import time
        if self.change_delay:
            time.sleep(self.change_delay)

        def reject(code, msg, item_id=None):
            return {"ok": False, "trip": None, "adjustments": [],
                    "violations": [{"code": code, "message": msg, "item_id": item_id}]}

        new = copy.deepcopy(trip)
        items = new["items"]
        travelers = new["traveler"]["adults"] + new["traveler"]["children"]
        op = change.get("op")
        by_id = {i["item_id"]: i for i in items}
        if op == "remove_attraction":
            it = by_id.get(change.get("item_id"))
            if not it or it["type"] != "attraction":
                return reject("UNKNOWN_ITEM", "No such attraction in this package.", change.get("item_id"))
            items.remove(it)
        elif op == "add_attraction":
            if not change["attraction_id"].startswith("FAKE-"):
                return reject("UNKNOWN_CATALOG_ID", "Unknown attraction.")
            d = change["date"]
            if not new["start_date"] <= d < new["end_date"]:
                return reject("ATTRACTION_DAY_NOT_ALLOWED", "Attractions are not allowed on that day.")
            if any(i["catalog_id"] == change["attraction_id"] for i in items):
                return reject("DUPLICATE_ATTRACTION", "That attraction is already in the package.")
            if sum(1 for i in items if i["type"] == "attraction" and i["start"][:10] == d) >= 2:
                return reject("TOO_MANY_ATTRACTIONS", "At most 2 attractions per day.")
            start = change.get("start_time", "14:00")
            nid = f"i{max(int(i['item_id'][1:]) for i in items) + 1}"
            new_item = {"item_id": nid, "type": "attraction", "role": "attraction_visit", "name": "Fake Garden",
                        "city": items[0]["city"], "status": "PLANNED", "catalog_id": change["attraction_id"],
                        "room_id": None, "start": f"{d}T{start}", "end": f"{d}T{int(start[:2]) + 1:02d}{start[2:]}",
                        "quantity": travelers, "price_inr": 700, "booking": None, "sources": [], "warnings": []}
            pos = max(k for k, i in enumerate(items) if i["start"][:10] <= d) + 1
            items.insert(pos, new_item)
        elif op == "replace_hotel":
            it = by_id.get(change.get("item_id"))
            if not it or it["type"] != "hotel":
                return reject("UNKNOWN_ITEM", "No such hotel in this package.", change.get("item_id"))
            if not (change["hotel_id"].startswith("FAKE-") and change["room_id"].startswith("FAKE-")):
                return reject("UNKNOWN_CATALOG_ID", "Unknown hotel or room.", it["item_id"])
            it.update(catalog_id=change["hotel_id"], room_id=change["room_id"], name=f"Fake {change['hotel_id']}",
                      price_inr=10_000_000 if change["hotel_id"].endswith("PRICEY") else 1000)
        elif op == "replace_flight":
            it = by_id.get(change.get("item_id"))
            if not it or it["type"] != "flight":
                return reject("UNKNOWN_ITEM", "No such flight in this package.", change.get("item_id"))
            it.update(catalog_id=change["flight_id"], price_inr=it["price_inr"] + 1000)
        else:
            return reject("UNKNOWN_OP", "Unsupported change.")
        new["total_inr"] = sum(i["price_inr"] for i in items)
        if new["total_inr"] > new["budget_inr"]:
            return reject("OVER_BUDGET", "This change would exceed the budget.")
        return {"ok": True, "trip": new, "violations": [], "adjustments": []}
