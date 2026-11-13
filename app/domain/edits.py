"""Phase 5: change simulation and alternatives (DomainPort.change / DomainPort.alternatives).

change():  clone -> apply the one change -> re-select dependent airport transfers only when needed
           (current transfer kept when still valid) -> reprice every item from the catalog ->
           validate_trip(..., "modified"). All-or-nothing: the input trip is never mutated and a
           rejected change returns trip=None with >= 1 violation. Attractions are never moved or
           removed implicitly; a conflict rejects the change instead.
alternatives(): every candidate is simulated through change(), so only options that change()
           accepts are listed, with exact new totals (including any transfer re-selection).
"""

from __future__ import annotations

import copy
import itertools
from datetime import date, datetime, timedelta

from app.contracts import ALTERNATIVE_COMPONENTS, CHANGE_OPS, InvalidRequestError, trip_shape_errors
from app.domain.planner import (
    MIN_AIRPORT_BEFORE_DEPARTURE, MIN_LANDING_TO_PICKUP, arrival_wait_warnings, departure_wait_warnings,
)
from app.domain.pricing import price_trip, transfer_price
from app.domain.rules import attraction_start, min_rooms, transfer_arrival, transfer_serves_area
from app.domain.timeutil import format_local, latest_departure, next_departure, parse_hhmm, parse_local
from app.domain.validator import validate_trip, violation

DEFAULT_LIMIT = 20
DAYTIME_FLOOR = timedelta(hours=9)  # add_attraction without start_time: earliest legal start from 09:00 first
REQUIRED_FIELDS = {
    "replace_hotel": ("item_id", "hotel_id", "room_id"),
    "replace_flight": ("item_id", "flight_id"),
    "add_attraction": ("attraction_id", "date"),
    "remove_attraction": ("item_id",),
}


# ============================================================================ helpers

def _reject(violations: list[dict]) -> dict:
    return {"ok": False, "trip": None, "violations": violations, "adjustments": []}


def _party(trip: dict) -> tuple[int, int]:
    return trip["traveler"]["adults"], len(trip["traveler"]["child_ages"])


def _find(trip: dict, item_id) -> dict | None:
    return next((i for i in trip["items"] if i["item_id"] == item_id), None)


def _role(trip: dict, role: str) -> list[dict]:
    return [i for i in trip["items"] if i["role"] == role]


def _hotels(trip: dict) -> list[dict]:
    return sorted(_role(trip, "hotel_stay"), key=lambda i: i["start"])


def _night_city(trip: dict, day: str) -> str | None:
    return next((h["city"] for h in _hotels(trip) if h["start"] <= day < h["end"]), None)


def _new_item_id(trip: dict) -> str:
    ids = {i["item_id"] for i in trip["items"]}
    n = len(trip["items"]) + 1
    while f"i{n}" in ids:
        n += 1
    return f"i{n}"


def _is_date(v) -> bool:
    if not isinstance(v, str) or len(v) != 10:
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _reprice(catalog, trip: dict) -> None:
    """Full repricing from the catalog: no stale line item survives a change (PR-16)."""
    priced = price_trip(trip, catalog)
    for item in trip["items"]:
        if item["item_id"] in priced["items"]:
            item["quantity"], item["price_inr"] = priced["items"][item["item_id"]]
    trip["total_inr"] = sum(i["price_inr"] for i in trip["items"]
                            if isinstance(i["price_inr"], int) and not isinstance(i["price_inr"], bool))


def _order_items(trip: dict) -> None:
    """Chronological order matching the planner: outbound, (transfer, hotel, visits) per city,
    departure transfer, return flight. Destination-side items share one local time zone."""
    entry: dict[str, str] = {}
    for t in _role(trip, "arrival_transfer") + _role(trip, "intercity_transfer"):
        entry.setdefault(t["city"], t["end"] or t["start"])

    def key(item):
        role = item["role"]
        if role == "outbound_flight":
            return (0, "", 0)
        if role == "departure_transfer":
            return (2, item["start"], 0)
        if role == "return_flight":
            return (3, item["start"], 0)
        if role == "hotel_stay":
            moment = entry.get(item["city"], item["start"] + "T00:00")
            return (1, max(moment, item["start"] + "T00:00"), 1)
        return (1, item["start"], 0 if item["type"] == "transfer" else 2)

    trip["items"].sort(key=key)


# ============================================================================ airport transfers

def _transfer_candidates(catalog, trip: dict, arrival: bool, keep_time: bool) -> list[tuple[dict, datetime]]:
    """Ordered (transfer, pickup) options for the arrival or departure airport transfer: current transfer
    at its current time (only when the flight is unchanged, keep_time), current transfer re-timed to the
    most convenient legal time, then the other transfers cheapest first."""
    flight_item = (_role(trip, "outbound_flight") or [None])[0] if arrival else (
        _role(trip, "return_flight") or [None])[0]
    hotels = _hotels(trip)
    current = (_role(trip, "arrival_transfer" if arrival else "departure_transfer") or [None])[0]
    flight = catalog.flights.get(flight_item["catalog_id"]) if flight_item else None
    if flight is None or not hotels or current is None:
        return []
    hotel_item = hotels[0] if arrival else hotels[-1]
    hotel = catalog.hotels.get(hotel_item["catalog_id"])
    if hotel is None:
        return []
    airport = flight["to"] if arrival else flight["from"]
    adults, children = _party(trip)
    out: list[tuple[dict, datetime]] = []
    for t in catalog.airport_transfers.get((airport, hotel["city"]), ()):
        if not transfer_serves_area(t, hotel["city"], hotel["area"]):
            continue
        window = (t["service_from"], t["service_until"], t.get("frequency_minutes"))
        if arrival:
            pickup = next_departure(parse_local(flight["arrival_local"]) + MIN_LANDING_TO_PICKUP, *window)
        else:
            latest = parse_local(flight["departure_local"]) - MIN_AIRPORT_BEFORE_DEPARTURE - timedelta(
                minutes=t["duration_minutes"])
            pickup = latest_departure(latest, *window)
        if pickup is not None:
            out.append((t, pickup))
    out.sort(key=lambda o: (transfer_price(o[0], adults, children), o[0]["transfer_id"]))
    # Current transfer first (at its current time, then re-timed), then the rest cheapest first.
    mine = [o for o in out if o[0]["transfer_id"] == current["catalog_id"]]
    keep = []
    try:
        if mine and keep_time:
            keep.append((mine[0][0], parse_local(current["start"])))
    except (TypeError, ValueError):
        pass
    out = keep + mine + [o for o in out if o[0]["transfer_id"] != current["catalog_id"]]
    seen, unique = set(), []
    for t, pickup in out:
        if (t["transfer_id"], pickup) not in seen:
            seen.add((t["transfer_id"], pickup))
            unique.append((t, pickup))
    return unique


def _set_transfer(catalog, trip: dict, arrival: bool, t, pickup: datetime) -> str | None:
    """Apply (t, pickup) to the arrival/departure transfer item; returns an adjustment note or None."""
    role = "arrival_transfer" if arrival else "departure_transfer"
    item = _role(trip, role)[0]
    before = (item["catalog_id"], item["start"])
    flight_item = _role(trip, "outbound_flight" if arrival else "return_flight")[0]
    flight = catalog.flights[flight_item["catalog_id"]]
    item.update(catalog_id=t["transfer_id"], name=t["name"], start=format_local(pickup),
                end=format_local(transfer_arrival(t, pickup)),
                warnings=arrival_wait_warnings(flight, pickup) if arrival
                else departure_wait_warnings(flight, t, pickup))
    if before == (item["catalog_id"], item["start"]):
        return None
    label = "Arrival" if arrival else "Departure"
    if before[0] == item["catalog_id"]:
        return f"{label} airport transfer {t['name']} pickup moved from {before[1]} to {item['start']}."
    return f"{label} airport transfer changed to {t['name']} ({t['transfer_id']}), pickup {item['start']}."


# ============================================================================ change handlers

def _replace_hotel(catalog, trip, change):
    item = _find(trip, change["item_id"])
    if item is None:
        return [violation("ITEM_NOT_FOUND", f"no item {change['item_id']!r} in this package")], ()
    if item["role"] != "hotel_stay":
        return [violation("ITEM_WRONG_TYPE", "replace_hotel needs a hotel item", item["item_id"])], ()
    hotel, room = catalog.hotels.get(change["hotel_id"]), catalog.rooms.get(change["room_id"])
    if hotel is None or room is None:
        return [violation("UNKNOWN_CATALOG_ID", "hotel or room is not in the catalog", item["item_id"])], ()
    if room["hotel_id"] != hotel["hotel_id"]:
        return [violation("ROOM_HOTEL_MISMATCH", f"room {room['room_id']} belongs to {room['hotel_id']}",
                          item["item_id"])], ()
    if hotel["city"] != item["city"]:
        return [violation("HOTEL_WRONG_CITY", f"{hotel['name']} is in {hotel['city']}, this stay is in "
                                              f"{item['city']}", item["item_id"])], ()
    if (item["catalog_id"], item["room_id"]) == (hotel["hotel_id"], room["room_id"]):
        return [violation("NO_CHANGE", "this hotel and room are already booked for that stay", item["item_id"])], ()
    adults, children = _party(trip)
    k = min_rooms(adults, children, room)
    if k is None:
        return [violation("ROOM_CAPACITY", f"{room['name']} cannot hold {adults} adults and {children} children "
                                           f"with at least one adult per room", item["item_id"])], ()
    hotels = _hotels(trip)
    item.update(catalog_id=hotel["hotel_id"], room_id=room["room_id"], name=hotel["name"], quantity=k)
    needs = []
    if item is hotels[0]:
        needs.append((True, True))
    if item is hotels[-1]:
        needs.append((False, True))
    return None, tuple(needs)


def _replace_flight(catalog, trip, change):
    item = _find(trip, change["item_id"])
    if item is None:
        return [violation("ITEM_NOT_FOUND", f"no item {change['item_id']!r} in this package")], ()
    if item["role"] not in ("outbound_flight", "return_flight"):
        return [violation("ITEM_WRONG_TYPE", "replace_flight needs a flight item", item["item_id"])], ()
    flight = catalog.flights.get(change["flight_id"])
    if flight is None:
        return [violation("UNKNOWN_CATALOG_ID", f"unknown flight {change['flight_id']!r}", item["item_id"])], ()
    if item["catalog_id"] == flight["flight_id"]:
        return [violation("NO_CHANGE", "this flight is already in the package", item["item_id"])], ()
    item.update(catalog_id=flight["flight_id"], name=f"{flight['flight_number']} {flight['from']} to {flight['to']}",
                start=flight["departure_local"][:16], end=flight["arrival_local"][:16])
    return None, ((item["role"] == "outbound_flight", False),)


def _earliest_slot(catalog, trip: dict, a, day: date) -> datetime | None:
    """Earliest legal start on `day` that fits opening hours, the city timeline and existing visits."""
    from app.domain.validator import _Ctx, _check_header, _check_hotels, _check_flights, _check_transfers
    ctx = _Ctx(catalog, trip)
    party = _check_header(ctx)
    if party is None:
        return None
    segs = _check_hotels(ctx, *party)
    out_f, ret_f = _check_flights(ctx, segs, sum(party))
    entry, leave = _check_transfers(ctx, segs, out_f, ret_f)
    seg_i = next((i for i, s in enumerate(segs) if s[1] <= day < s[2]), None)
    if seg_i is None:
        return None
    busy = []
    for v in _role(trip, "attraction_visit"):
        try:
            s, e = parse_local(v["start"]), parse_local(v["end"])
        except (TypeError, ValueError):
            continue
        if s.date() == day:
            busy.append((s, e))
    daytime = datetime.combine(day, datetime.min.time()) + DAYTIME_FLOOR
    floors = [max(daytime, entry[seg_i]) if entry[seg_i] else daytime, entry[seg_i]]
    for floor in floors:  # earliest legal start from 09:00, else earliest legal start at all
        # preferred "00:00" makes attraction_start return the earliest legal start >= floor.
        cand = attraction_start(a, day, floor, leave[seg_i], preferred="00:00")
        while cand is not None:  # jump past clashing visits; strictly increasing, so it terminates
            end = cand + timedelta(minutes=a["visit_minutes"])
            clash = [e for s, e in busy if cand < e and s < end]
            if not clash:
                return cand
            cand = attraction_start(a, day, max(clash), leave[seg_i], preferred="00:00")
    return None


def _add_attraction(catalog, trip, change):
    a = catalog.attractions.get(change["attraction_id"])
    if a is None:
        return [violation("UNKNOWN_CATALOG_ID", f"unknown attraction {change['attraction_id']!r}")], ()
    if not _is_date(change["date"]):
        return [violation("CHANGE_MALFORMED", "date must be YYYY-MM-DD")], ()
    day = date.fromisoformat(change["date"])
    if change.get("start_time") is not None:
        start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=parse_hhmm(change["start_time"]))
    else:
        start = _earliest_slot(catalog, trip, a, day)
        if start is None:  # no legal slot: keep a nominal start so validate_trip names the exact rule broken
            start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=parse_hhmm(a["open_from"]))
    adults, children = _party(trip)
    trip["items"].append({
        "item_id": _new_item_id(trip), "type": "attraction", "role": "attraction_visit", "name": a["name"],
        "city": a["city"], "status": "PLANNED", "catalog_id": a["attraction_id"], "room_id": None,
        "start": format_local(start), "end": format_local(start + timedelta(minutes=a["visit_minutes"])),
        "quantity": adults + children, "price_inr": 0, "booking": None, "sources": [], "warnings": []})
    return None, ()


def _remove_attraction(catalog, trip, change):
    item = _find(trip, change["item_id"])
    if item is None:
        return [violation("ITEM_NOT_FOUND", f"no item {change['item_id']!r} in this package")], ()
    if item["role"] != "attraction_visit":
        return [violation("ITEM_NOT_REMOVABLE", "only attractions can be removed", item["item_id"])], ()
    trip["items"].remove(item)
    return None, ()


HANDLERS = {"replace_hotel": _replace_hotel, "replace_flight": _replace_flight,
            "add_attraction": _add_attraction, "remove_attraction": _remove_attraction}


# ============================================================================ DomainPort.change

def apply_change(catalog, trip: dict, change: dict) -> dict:
    op = change.get("op") if isinstance(change, dict) else None
    if op not in CHANGE_OPS:
        return _reject([violation("UNKNOWN_OP", f"unknown change op {op!r}")])
    missing = [k for k in REQUIRED_FIELDS[op] if not isinstance(change.get(k), str) or not change.get(k)]
    if missing:
        return _reject([violation("CHANGE_MALFORMED", f"{op} needs non-empty string fields {missing}")])
    if op == "add_attraction" and change.get("start_time") is not None:
        try:
            if not isinstance(change["start_time"], str) or parse_hhmm(change["start_time"]) >= 1440:
                raise ValueError
        except ValueError:
            return _reject([violation("CHANGE_MALFORMED", "start_time must be HH:MM")])
    shape = trip_shape_errors(trip, draft=False) if isinstance(trip, dict) else ["trip is not an object"]
    if shape:
        return _reject([violation("TRIP_MALFORMED", shape[0])])
    if trip.get("status") == "BOOKED" or any(i["status"] == "BOOKED" for i in trip["items"]):
        return _reject([violation("TRIP_BOOKED", "a booked package cannot be changed")])

    draft = copy.deepcopy(trip)  # never touch the caller's trip
    problems, needs = HANDLERS[op](catalog, draft, change)
    if problems:
        return _reject(problems)

    slots = {arrival: _transfer_candidates(catalog, draft, arrival, keep_time) for arrival, keep_time in needs}
    missing_side = [arrival for arrival, options in slots.items() if not options]
    if missing_side:  # explain the root cause (e.g. wrong airport) plus the missing transfer
        _reprice(catalog, draft)
        found = [v for v in validate_trip(catalog, draft, "modified") if not v["code"].startswith("TRANSFER_")]
        sides = " and ".join("arrival" if a else "departure" for a in missing_side)
        return _reject(found + [violation("NO_AIRPORT_TRANSFER",
                                          f"no {sides} airport transfer serves this hotel area and airport within "
                                          f"the 60/180-minute and service-hour rules")])
    first_failure = None
    keys = list(slots)
    for combo in itertools.product(*(slots[k] for k in keys)):
        candidate = copy.deepcopy(draft)
        notes = [n for arrival, (t, pickup) in zip(keys, combo)
                 if (n := _set_transfer(catalog, candidate, arrival, t, pickup))]
        _order_items(candidate)
        _reprice(catalog, candidate)
        found = validate_trip(catalog, candidate, "modified")
        if not found:
            return {"ok": True, "trip": candidate, "violations": [], "adjustments": notes}
        first_failure = first_failure or found
    return _reject(first_failure)


# ============================================================================ DomainPort.alternatives

def _limit(filters: dict) -> int:
    limit = filters.get("limit", DEFAULT_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise InvalidRequestError("filters.limit must be a positive integer")
    return limit


def list_alternatives(catalog, trip: dict, component: str, filters: dict) -> list[dict]:
    if component not in ALTERNATIVE_COMPONENTS:
        raise InvalidRequestError(f"unknown component {component!r}")
    filters = filters or {}
    if not isinstance(filters, dict):
        raise InvalidRequestError("filters must be an object")
    limit = _limit(filters)
    if component == "hotel" and not filters.get("item_id"):
        raise InvalidRequestError("hotel alternatives need filters.item_id")
    if component == "attraction" and not _is_date(filters.get("date")):
        raise InvalidRequestError("attraction alternatives need filters.date as YYYY-MM-DD")
    shape = trip_shape_errors(trip, draft=False) if isinstance(trip, dict) else ["trip is not an object"]
    if shape:
        raise InvalidRequestError("trip is malformed", {"errors": shape[:10]})

    changes: list[dict] = []
    if component == "hotel":
        item = _find(trip, filters["item_id"])
        if item is None or item["role"] != "hotel_stay":
            raise InvalidRequestError(f"{filters['item_id']!r} is not a hotel item of this package")
        for hotel in catalog.hotels_by_city.get(item["city"], ()):
            for room in catalog.rooms_by_hotel.get(hotel["hotel_id"], ()):
                if (hotel["hotel_id"], room["room_id"]) != (item["catalog_id"], item["room_id"]):
                    changes.append({"op": "replace_hotel", "item_id": item["item_id"],
                                    "hotel_id": hotel["hotel_id"], "room_id": room["room_id"]})
    elif component in ("outbound_flight", "return_flight"):
        item = (_role(trip, component) or [None])[0]
        hotels = _hotels(trip)
        if item is None or not hotels:
            return []
        home = trip["traveler"]["home_airport"]
        city = hotels[0]["city"] if component == "outbound_flight" else hotels[-1]["city"]
        for ap in catalog.airports_serving_city.get(city, ()):
            pool = (catalog.flights_by_arrival.get((home, ap, trip["start_date"]), ())
                    if component == "outbound_flight" else
                    catalog.flights_by_departure.get((ap, home, trip["end_date"]), ()))
            changes += [{"op": "replace_flight", "item_id": item["item_id"], "flight_id": f["flight_id"]}
                        for f in pool if f["flight_id"] != item["catalog_id"]]
    else:
        day = filters["date"]
        if not (trip["start_date"] <= day < trip["end_date"]):
            return []
        city = _night_city(trip, day)
        taken = {i["catalog_id"] for i in _role(trip, "attraction_visit")}
        changes = [{"op": "add_attraction", "attraction_id": a["attraction_id"], "date": day}
                   for a in catalog.attractions_by_city.get(city, ()) if a["attraction_id"] not in taken]

    out = []
    for change in changes:
        result = apply_change(catalog, trip, change)
        if not result["ok"]:
            continue
        new = result["trip"]
        if component == "attraction":
            new_item = next(i for i in new["items"] if i["catalog_id"] == change["attraction_id"])
            change = {**change, "start_time": new_item["start"][11:16]}
        else:
            new_item = _find(new, change["item_id"])
        out.append({
            "component": component, "change": change, "name": new_item["name"], "catalog_id": new_item["catalog_id"],
            "room_id": new_item["room_id"], "start": new_item["start"], "end": new_item["end"],
            "price_inr": new_item["price_inr"], "new_total_inr": new["total_inr"],
            "price_diff_inr": new["total_inr"] - trip["total_inr"],
        })
    out.sort(key=lambda a: (a["new_total_inr"], a["catalog_id"], a["room_id"] or "", a["start"] or ""))
    return out[:limit]
