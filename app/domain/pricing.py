"""Exact integer-INR pricing (problem §4 "Prices"). All inputs are catalog records; client
prices are never used. Children pay `child_price` only where it exists, else the adult price.
"""

from __future__ import annotations

from datetime import date


def _amount(money) -> int:
    return int(money["amount"])


def _child_amount(record, adult_key: str) -> int:
    child = record.get("child_price")
    return _amount(child) if child is not None else _amount(record[adult_key])


def flight_price(flight, travelers: int) -> int:
    """price_per_seat x every traveler (children and infants need a seat, FL-12)."""
    return _amount(flight["price_per_seat"]) * travelers


def hotel_price(room, rooms: int, nights: int) -> int:
    return _amount(room["price_per_night"]) * rooms * nights


def transfer_quantity(transfer, travelers: int) -> int:
    """PER_VEHICLE: vehicles = ceil(travelers / max_passengers). PER_PERSON: travelers."""
    if transfer["pricing"] == "PER_VEHICLE":
        return -(-travelers // transfer["max_passengers"])
    return travelers


def transfer_price(transfer, adults: int, children: int) -> int:
    if transfer["pricing"] == "PER_VEHICLE":
        return _amount(transfer["price"]) * transfer_quantity(transfer, adults + children)
    return adults * _amount(transfer["price"]) + children * _child_amount(transfer, "price")


def attraction_price(attraction, adults: int, children: int) -> int:
    return adults * _amount(attraction["price_per_person"]) + children * _child_amount(attraction, "price_per_person")


def stay_nights_count(check_in: str, check_out: str) -> int:
    if not (isinstance(check_in, str) and isinstance(check_out, str) and len(check_in) == len(check_out) == 10):
        raise ValueError("stay dates must be YYYY-MM-DD")
    return (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days


class PricingError(ValueError):
    """An item cannot be priced from the catalog (unknown ID, malformed dates/quantity)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def price_item(item: dict, catalog, adults: int, children: int) -> tuple[int, int]:
    """Catalog (quantity, price_inr) for one trip item. Hotel quantity is the item's room
    count (a planner choice checked by the capacity rule); every other quantity is derived."""
    kind, cid = item.get("type"), item.get("catalog_id")
    travelers = adults + children
    if kind == "flight":
        flight = catalog.flights.get(cid)
        if flight is None:
            raise PricingError("UNKNOWN_CATALOG_ID", f"unknown flight {cid!r}")
        return travelers, flight_price(flight, travelers)
    if kind == "hotel":
        room = catalog.rooms.get(item.get("room_id"))
        if room is None:
            raise PricingError("UNKNOWN_CATALOG_ID", f"unknown room {item.get('room_id')!r}")
        rooms = item.get("quantity")
        if not isinstance(rooms, int) or isinstance(rooms, bool) or rooms < 1:
            raise PricingError("QUANTITY_MISMATCH", "hotel quantity must be a positive int")
        try:
            nights = stay_nights_count(item["start"], item["end"])
        except (KeyError, TypeError, ValueError):
            raise PricingError("ITEM_MALFORMED", "hotel start/end must be YYYY-MM-DD") from None
        if nights < 1:
            raise PricingError("ITEM_MALFORMED", "hotel stay must have at least one night")
        return rooms, hotel_price(room, rooms, nights)
    if kind == "transfer":
        transfer = catalog.transfers.get(cid)
        if transfer is None:
            raise PricingError("UNKNOWN_CATALOG_ID", f"unknown transfer {cid!r}")
        return transfer_quantity(transfer, travelers), transfer_price(transfer, adults, children)
    if kind == "attraction":
        attraction = catalog.attractions.get(cid)
        if attraction is None:
            raise PricingError("UNKNOWN_CATALOG_ID", f"unknown attraction {cid!r}")
        return travelers, attraction_price(attraction, adults, children)
    raise PricingError("ITEM_MALFORMED", f"unknown item type {kind!r}")


def price_trip(trip: dict, catalog) -> dict:
    """Reprice every item from the catalog. Returns
    {"items": {item_id: (quantity, price_inr)}, "total_inr": int, "errors": [(code, msg, item_id)]}.
    The total only counts items that could be priced."""
    traveler = trip.get("traveler") or {}
    adults = traveler.get("adults", 0)
    children = len(traveler.get("child_ages") or [])
    priced: dict[str, tuple[int, int]] = {}
    errors: list[tuple[str, str, str | None]] = []
    for item in trip.get("items") or []:
        try:
            priced[item.get("item_id")] = price_item(item, catalog, adults, children)
        except PricingError as exc:
            errors.append((exc.code, str(exc), item.get("item_id")))
    return {"items": priced, "total_inr": sum(p for _, p in priced.values()), "errors": errors}
