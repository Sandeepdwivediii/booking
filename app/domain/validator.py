"""Reusable validate_trip building blocks (Phase 2). Each check returns contract Violations
({code, message, item_id}); an empty list means the check passed. The full trip validator
(city plan, flights, transfers, attractions, modes) is assembled from these in Phase 3.
"""

from __future__ import annotations

from app.domain.pricing import price_trip
from app.domain.rules import rooms_fit, stay_nights, unavailable_nights

# Stable violation codes used so far (UPPER_SNAKE, contract §validate).
UNKNOWN_CATALOG_ID = "UNKNOWN_CATALOG_ID"
ITEM_MALFORMED = "ITEM_MALFORMED"
PRICE_MISMATCH = "PRICE_MISMATCH"
QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
TOTAL_MISMATCH = "TOTAL_MISMATCH"
OVER_BUDGET = "OVER_BUDGET"
ROOM_HOTEL_MISMATCH = "ROOM_HOTEL_MISMATCH"
HOTEL_WRONG_CITY = "HOTEL_WRONG_CITY"
ROOM_CAPACITY = "ROOM_CAPACITY"
HOTEL_NO_AVAILABILITY = "HOTEL_NO_AVAILABILITY"


def violation(code: str, message: str, item_id: str | None = None) -> dict:
    return {"code": code, "message": message, "item_id": item_id}


def check_prices(trip: dict, catalog) -> list[dict]:
    """Every item's quantity/price and the trip total must equal a fresh catalog repricing;
    total must be within budget. Client-supplied amounts are never trusted (PR-07, SY-18)."""
    priced = price_trip(trip, catalog)
    out = [violation(code, msg, iid) for code, msg, iid in priced["errors"]]
    for item in trip.get("items") or []:
        expected = priced["items"].get(item.get("item_id"))
        if expected is None:
            continue
        qty, price = expected
        iid = item.get("item_id")
        if item.get("quantity") != qty:
            out.append(violation(QUANTITY_MISMATCH, f"quantity must be {qty}, got {item.get('quantity')!r}", iid))
        if item.get("price_inr") != price:
            out.append(violation(PRICE_MISMATCH, f"price_inr must be {price}, got {item.get('price_inr')!r}", iid))
    items_sum = sum(i.get("price_inr") for i in trip.get("items") or []
                    if isinstance(i.get("price_inr"), int) and not isinstance(i.get("price_inr"), bool))
    total = trip.get("total_inr")
    if not out and (total != priced["total_inr"] or total != items_sum):
        out.append(violation(TOTAL_MISMATCH, f"total_inr must be {priced['total_inr']}, got {total!r}"))
    budget = trip.get("budget_inr")
    if isinstance(budget, int) and isinstance(total, int) and total > budget:
        out.append(violation(OVER_BUDGET, f"total_inr {total} exceeds budget_inr {budget}"))
    return out


def check_hotel_item(item: dict, catalog, adults: int, children: int, city: str | None = None) -> list[dict]:
    """One hotel item: hotel/room exist and belong together (HT-13), hotel in the expected
    city (HT-10), k rooms fit the party (HT-05/06/07), rooms_left >= k on EVERY night of
    [start, end) with missing rows unavailable (HT-01..04, HT-11, HT-14).
    Check-in/check-out display times are not judged (HT-12) and are ignored."""
    iid = item.get("item_id")
    hotel = catalog.hotels.get(item.get("catalog_id"))
    room = catalog.rooms.get(item.get("room_id"))
    if hotel is None or room is None:
        return [violation(UNKNOWN_CATALOG_ID, "hotel or room is not in the catalog", iid)]
    if room["hotel_id"] != hotel["hotel_id"]:
        return [violation(ROOM_HOTEL_MISMATCH, f"room {room['room_id']} belongs to {room['hotel_id']}, "
                                               f"not {hotel['hotel_id']}", iid)]
    try:
        check_in, check_out = item["start"], item["end"]
        nights = stay_nights(check_in, check_out)
    except (KeyError, TypeError, ValueError):
        return [violation(ITEM_MALFORMED, "hotel start/end must be YYYY-MM-DD dates", iid)]
    if not nights:
        return [violation(ITEM_MALFORMED, "hotel stay must have at least one night", iid)]
    out = []
    if city is not None and hotel["city"] != city:
        out.append(violation(HOTEL_WRONG_CITY, f"hotel is in {hotel['city']}, segment city is {city}", iid))
    k = item.get("quantity")
    if not isinstance(k, int) or isinstance(k, bool) or not rooms_fit(adults, children, k, room):
        out.append(violation(ROOM_CAPACITY, f"{k!r} x {room['room_id']} cannot hold {adults} adults "
                                            f"and {children} children", iid))
        return out
    missing = unavailable_nights(catalog, room["room_id"], check_in, check_out, k)
    if missing:
        out.append(violation(HOTEL_NO_AVAILABILITY, f"fewer than {k} rooms left on {', '.join(missing)}", iid))
    return out
