"""Reusable validate_trip building blocks (Phase 2). Each check returns contract Violations
({code, message, item_id}); an empty list means the check passed. The full trip validator
(city plan, flights, transfers, attractions, modes) is assembled from these in Phase 3.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.contracts import VALIDATION_MODES, InvalidRequestError, attraction_count_errors, trip_shape_errors
from app.domain.catalog import AIRPORT_TRANSFER_TYPES, CITY_TRANSFER_TYPES, INTERCITY_TRANSFER_TYPES
from app.domain.pricing import price_trip
from app.domain.rules import (
    attraction_visit_ok, rooms_fit, stay_nights, transfer_arrival, transfer_departure_ok, transfer_serves_area,
    unavailable_nights,
)
from app.domain.timeutil import format_local, parse_local

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


# ============================================================================ full validator

MIN_LANDING_TO_PICKUP = timedelta(minutes=60)
MIN_AIRPORT_BEFORE_DEPARTURE = timedelta(minutes=180)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _local(value) -> datetime | None:
    try:
        return parse_local(value)
    except (TypeError, ValueError):
        return None


class _Ctx:
    """Shared state for one validate_trip call."""

    def __init__(self, catalog, trip):
        self.cat, self.trip, self.out = catalog, trip, []
        self.by_role: dict[str, list[dict]] = {}
        for item in trip["items"]:
            self.by_role.setdefault(item["role"], []).append(item)

    def add(self, code: str, message: str, item: dict | None = None) -> None:
        self.out.append(violation(code, message, item.get("item_id") if item else None))

    def one(self, role: str, code: str) -> dict | None:
        items = self.by_role.get(role, [])
        if len(items) != 1:
            self.add(code, f"package needs exactly one {role.replace('_', ' ')}, found {len(items)}")
            return None
        return items[0]


def validate_trip(catalog, trip: dict, mode: str) -> list[dict]:
    """DomainPort.validate: full re-check of a judge-format trip against the catalog and §4 rules.
    Returns Violations ([] = valid). Ignores top-level/item status and booking metadata.
    Independent of the planner so it can serve as the oracle for planner output."""
    if mode not in VALIDATION_MODES:
        raise InvalidRequestError(f"unknown validation mode {mode!r}")
    shape = trip_shape_errors(trip, draft=False)
    if shape:
        return [violation("TRIP_MALFORMED", e) for e in shape]
    ctx = _Ctx(catalog, trip)
    party = _check_header(ctx)
    if party is None:
        return ctx.out
    adults, children = party
    ctx.out += check_prices(trip, catalog)
    segs = _check_hotels(ctx, adults, children)
    out_f, ret_f = _check_flights(ctx, segs, adults + children)
    entry, leave = _check_transfers(ctx, segs, out_f, ret_f)
    _check_attractions(ctx, segs, entry, leave, mode)
    return ctx.out


def _check_header(ctx: _Ctx):
    trip, cat = ctx.trip, ctx.cat
    t = trip["traveler"]
    adults, ages = t["adults"], t["child_ages"]
    if not _is_int(adults) or adults < 1 or not all(_is_int(a) and 0 <= a <= 17 for a in ages):
        ctx.add("TRAVELER_INVALID", "traveler needs >= 1 adult and child ages 0..17")
        return None
    if trip["country"] not in cat.countries:
        ctx.add("COUNTRY_UNKNOWN", f"unknown destination country {trip['country']!r}")
        return None
    if t["home_airport"] not in {d["airport"] for d in cat.departure_cities}:
        ctx.add("HOME_AIRPORT_UNKNOWN", f"{t['home_airport']!r} is not a departure city airport")
        return None
    start, end = date.fromisoformat(trip["start_date"]), date.fromisoformat(trip["end_date"])
    rules = cat.rules
    if not rules["min_nights"] <= (end - start).days <= rules["max_nights"]:
        ctx.add("TRIP_DURATION", f"trip must be {rules['min_nights']}..{rules['max_nights']} nights")
    window = rules["data_window"]
    if trip["start_date"] < window["first_night"] or trip["end_date"] > window["last_departure"]:
        ctx.add("OUTSIDE_DATA_WINDOW", f"trip must lie within {window['first_night']}..{window['last_departure']}")
    return adults, len(ages)


def _check_hotels(ctx: _Ctx, adults: int, children: int) -> list:
    """Validate hotel items and derive the city plan: [(city, check_in, check_out, hotel, item)]."""
    trip, cat = ctx.trip, ctx.cat
    items = ctx.by_role.get("hotel_stay", [])
    if not items:
        ctx.add("HOTEL_MISSING", "package has no hotel")
        return []
    segs = []
    for item in sorted(items, key=lambda i: str(i["start"])):
        problems = check_hotel_item(item, cat, adults, children, city=item["city"])
        ctx.out += problems
        hotel = cat.hotels.get(item["catalog_id"])
        if hotel is None or any(p["code"] in (ITEM_MALFORMED, UNKNOWN_CATALOG_ID) for p in problems):
            return []
        segs.append((hotel["city"], date.fromisoformat(item["start"]), date.fromisoformat(item["end"]), hotel, item))
    country = cat.countries[trip["country"]]
    cities = {c["name"] for c in country["cities"]}
    links = {(l["from_city"], l["to_city"]) for l in country["links"]}
    start, end = date.fromisoformat(trip["start_date"]), date.fromisoformat(trip["end_date"])
    expected = start
    seen: set[str] = set()
    for i, (city, ci, co, _, item) in enumerate(segs):
        if ci != expected:
            ctx.add("HOTEL_GAP_OVERLAP", f"hotel nights must be continuous: expected check-in {expected}, got {ci}",
                    item)
        expected = co
        if city not in cities:
            ctx.add("CITY_NOT_IN_COUNTRY", f"{city} is not a city of {trip['country']}", item)
        if city in seen:
            ctx.add("CITY_REPEATED", f"{city} appears more than once (one hotel and room type per city)", item)
        seen.add(city)
        if (co - ci).days < cat.rules["min_nights_per_city"]:
            ctx.add("CITY_MIN_NIGHTS", f"{city} needs at least {cat.rules['min_nights_per_city']} nights", item)
        if i and segs[i - 1][0] != city and (segs[i - 1][0], city) not in links:
            ctx.add("NO_CITY_LINK", f"no directed link {segs[i - 1][0]} -> {city}", item)
    if expected != end:
        ctx.add("HOTEL_GAP_OVERLAP", f"last check-out must be the return day {end}, got {expected}", segs[-1][4])
    return segs


def _check_flights(ctx: _Ctx, segs, travelers: int):
    trip, cat = ctx.trip, ctx.cat
    home = trip["traveler"]["home_airport"]
    out_i = ctx.one("outbound_flight", "FLIGHT_COUNT")
    ret_i = ctx.one("return_flight", "FLIGHT_COUNT")
    found = []
    for item, is_out in ((out_i, True), (ret_i, False)):
        f = cat.flights.get(item["catalog_id"]) if item else None
        found.append(f)
        if f is None:
            continue
        city = (segs[0][0] if is_out else segs[-1][0]) if segs else None
        local_ap = f["to"] if is_out else f["from"]
        if (f["from"] if is_out else f["to"]) != home:
            ctx.add("FLIGHT_WRONG_ROUTE", f"flight must {'leave from' if is_out else 'return to'} {home}", item)
        if city and city not in (cat.airports[local_ap].get("serves_cities") or []):
            ctx.add("FLIGHT_WRONG_AIRPORT", f"{local_ap} does not serve {city}", item)
        local_date = (f["arrival_local"] if is_out else f["departure_local"])[:10]
        want = trip["start_date"] if is_out else trip["end_date"]
        if local_date != want:
            ctx.add("FLIGHT_WRONG_DATE", f"flight must {'land' if is_out else 'depart'} on {want} (local), "
                                         f"not {local_date}", item)
        if f["seats_left"] < travelers:
            ctx.add("FLIGHT_SEATS", f"only {f['seats_left']} seats left for {travelers} travelers", item)
        if item["start"] != f["departure_local"][:16] or item["end"] != f["arrival_local"][:16]:
            ctx.add("FLIGHT_TIME_MISMATCH", "flight start/end must equal the catalog local times", item)
    return found


def _check_timed_transfer(ctx: _Ctx, item: dict, t) -> datetime | None:
    pickup = _local(item["start"])
    if pickup is None:
        ctx.add(ITEM_MALFORMED, "transfer start must be YYYY-MM-DDTHH:MM", item)
        return None
    if not transfer_departure_ok(t, pickup):
        ctx.add("TRANSFER_OUT_OF_SERVICE", f"{format_local(pickup)} is not a legal departure of {t['transfer_id']}",
                item)
    if item["end"] is not None and item["end"] != format_local(transfer_arrival(t, pickup)):
        ctx.add("TRANSFER_TIME_MISMATCH", "transfer end must be pickup + duration_minutes", item)
    return pickup


def _check_transfers(ctx: _Ctx, segs, out_f, ret_f):
    """Airport, intercity and city transfers. Returns per-segment (entry, leave) times."""
    cat = ctx.cat
    m = len(segs)
    entry: list = [None] * m
    leave: list = [None] * m
    # --- airport transfers (a record serves both directions, TR-15)
    for role, is_arrival in (("arrival_transfer", True), ("departure_transfer", False)):
        item = ctx.one(role, "AIRPORT_TRANSFER_COUNT")
        if item is None or not segs:
            continue
        t = cat.transfers.get(item["catalog_id"])
        if t is None:
            continue
        if t["type"] not in AIRPORT_TRANSFER_TYPES:
            ctx.add("TRANSFER_WRONG_TYPE", f"{t['transfer_id']} is not an airport transfer", item)
            continue
        city, _, _, hotel, _ = segs[0] if is_arrival else segs[-1]
        flight = out_f if is_arrival else ret_f
        if flight is not None and t["airport"] != (flight["to"] if is_arrival else flight["from"]):
            ctx.add("TRANSFER_WRONG_AIRPORT", f"transfer airport {t['airport']} does not match the flight", item)
        if not transfer_serves_area(t, city, hotel["area"]):
            ctx.add("TRANSFER_WRONG_AREA", f"{t['transfer_id']} does not serve {hotel['area']}, {city}", item)
        pickup = _check_timed_transfer(ctx, item, t)
        if pickup is None:
            continue
        if is_arrival:
            entry[0] = transfer_arrival(t, pickup)
            if flight is not None and pickup < parse_local(flight["arrival_local"]) + MIN_LANDING_TO_PICKUP:
                ctx.add("TRANSFER_TOO_EARLY", "pickup must be at least 60 minutes after landing", item)
        else:
            leave[-1] = pickup
            if flight is not None and transfer_arrival(t, pickup) > (
                    parse_local(flight["departure_local"]) - MIN_AIRPORT_BEFORE_DEPARTURE):
                ctx.add("TRANSFER_LATE_FOR_FLIGHT", "traveler must reach the airport 180 minutes before departure",
                        item)
    # --- intercity: exactly one linked transfer with a legal departure on each city-change day
    country = cat.countries[ctx.trip["country"]]
    link_ids = {(l["from_city"], l["to_city"]): set(l["transfer_ids"]) for l in country["links"]}
    change_days = {segs[i][1]: i for i in range(1, m)}
    by_day: dict = {}
    for item in ctx.by_role.get("intercity_transfer", []):
        pickup = _local(item["start"])
        if pickup is None or pickup.date() not in change_days:
            ctx.add("INTERCITY_UNEXPECTED", "intercity transfer is not on a city-change day", item)
            continue
        by_day.setdefault(pickup.date(), []).append(item)
    for day, i in sorted(change_days.items()):
        items = by_day.get(day, [])
        if len(items) != 1:
            ctx.add("INTERCITY_MISSING", f"city-change day {day} needs exactly one intercity transfer, "
                                         f"found {len(items)}")
            continue
        item, prev, cur = items[0], segs[i - 1][0], segs[i][0]
        t = cat.transfers.get(item["catalog_id"])
        if t is None:
            continue
        if t["type"] not in INTERCITY_TRANSFER_TYPES or item["catalog_id"] not in link_ids.get((prev, cur), set()):
            reverse = (t.get("from_city"), t.get("to_city")) == (cur, prev)
            ctx.add("INTERCITY_WRONG_DIRECTION" if reverse else "INTERCITY_NOT_LINKED",
                    f"{t['transfer_id']} is not linked for {prev} -> {cur}", item)
            continue
        pickup = _check_timed_transfer(ctx, item, t)
        if pickup is not None:
            leave[i - 1], entry[i] = pickup, transfer_arrival(t, pickup)
    # --- optional city taxis (TR-17)
    plan_cities = {s[0] for s in segs}
    for item in ctx.by_role.get("city_taxi", []):
        t = cat.transfers.get(item["catalog_id"])
        if t is None:
            continue
        if t["type"] not in CITY_TRANSFER_TYPES or t["city"] not in plan_cities:
            ctx.add("CITY_TAXI_INVALID", f"{t['transfer_id']} is not a city transfer of this plan", item)
            continue
        _check_timed_transfer(ctx, item, t)
    # --- timeline: each city is left after it is entered
    for i in range(m):
        if entry[i] and leave[i] and leave[i] < entry[i]:
            ctx.add("TIMELINE_CONFLICT", f"traveler leaves {segs[i][0]} before arriving there")
    return entry, leave


def _check_attractions(ctx: _Ctx, segs, entry, leave, mode: str) -> None:
    cat, trip = ctx.cat, ctx.trip
    end = date.fromisoformat(trip["end_date"])
    seen: set[str] = set()
    per_day: dict = {}
    for item in ctx.by_role.get("attraction_visit", []):
        a = cat.attractions.get(item["catalog_id"])
        start = _local(item["start"])
        if a is None:
            continue
        if start is None:
            ctx.add(ITEM_MALFORMED, "attraction start must be YYYY-MM-DDTHH:MM", item)
            continue
        if a["attraction_id"] in seen:
            ctx.add("ATTRACTION_DUPLICATE", f"{a['attraction_id']} is already in the package", item)
        seen.add(a["attraction_id"])
        finish = start + timedelta(minutes=a["visit_minutes"])
        if item["end"] != format_local(finish):
            ctx.add("ATTRACTION_TIME_MISMATCH", "attraction end must be start + visit_minutes", item)
        day = start.date()
        if day >= end:
            continue  # return-day / after-trip visits are reported by the count rule below
        seg_i = next((i for i, s in enumerate(segs) if s[1] <= day < s[2]), None)
        if seg_i is None or segs[seg_i][0] != a["city"] or item["city"] != a["city"]:
            ctx.add("ATTRACTION_WRONG_CITY", f"{a['name']} ({a['city']}) is not in that night's hotel city", item)
            continue
        if not attraction_visit_ok(a, start):
            ctx.add("ATTRACTION_CLOSED", f"{a['name']} is closed or the visit does not fit its opening hours", item)
        if (entry[seg_i] and start < entry[seg_i]) or (leave[seg_i] and finish > leave[seg_i]):
            ctx.add("ATTRACTION_TIMELINE", f"{a['name']} must start after reaching {a['city']} and end before "
                                           f"leaving it", item)
        per_day.setdefault(day, []).append((start, finish, item))
    for visits in per_day.values():
        visits.sort(key=lambda v: v[0])
        for (s1, e1, _), (s2, _, it2) in zip(visits, visits[1:]):
            if s2 < e1:
                ctx.add("ATTRACTION_OVERLAP", "attraction visits on the same day overlap", it2)
    for msg in attraction_count_errors(trip, mode):
        ctx.add("ATTRACTION_COUNT", msg)
