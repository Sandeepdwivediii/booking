"""Exact planner: the globally cheapest complete feasible package (M2, M3, M8).

Search (see docs/HANDOFF_TANISH.md "Search strategy"):
  for every city plan (directed links, >= min nights per city, no repeated city, sum = nights)
    total = edge block(first city) + edge block(last city) + middle hotels + intercity + attractions
  - Hotels: per (city, check-in, check-out) only the cheapest option PER AREA is kept; area is the
    only hotel property any other component depends on (airport transfers). Within a hotel/room the
    minimal fitting room count k is both cheapest and least demanding on stock.
  - Intercity: every linked transfer is enumerated per change day (price is time independent).
  - Edge blocks enumerate every (flight, airport transfer, hotel area) combination exactly.
  - Attractions: exactly one per full day (prices are >= 0, so extra visits never lower the total),
    distinct per city; the min-cost choice is a min-weight basis of a transversal matroid, found
    exactly by greedy-by-price with augmenting-path matching.
No top-K, sampling or heuristic cut-offs are used; ties break deterministically on catalog order/IDs.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.contracts import InvalidRequestError, build_request_errors
from app.domain.pricing import attraction_price, flight_price, hotel_price, transfer_price, transfer_quantity
from app.domain.rules import (
    attraction_start, min_rooms, room_stay_available, transfer_arrival, transfer_serves_area,
)
from app.domain.timeutil import format_local, latest_departure, next_departure, parse_local
from app.domain.validator import validate_trip

MIN_LANDING_TO_PICKUP = timedelta(minutes=60)
MIN_AIRPORT_BEFORE_DEPARTURE = timedelta(minutes=180)
PREFERRED_INTERCITY_DEPARTURE = "09:00"

# NO_PACKAGE stages in evaluation order; the deepest stage any plan reached is reported.
STAGES = ("NO_OUTBOUND_FLIGHT", "NO_RETURN_FLIGHT", "NO_HOTEL_AVAILABILITY", "NO_INTERCITY_TRANSFER",
          "NO_AIRPORT_TRANSFER", "NO_ATTRACTION_SCHEDULE")
STAGE_TEXT = {
    "NO_OUTBOUND_FLIGHT": "no arrival flight from {home} lands on {start} with {T} free seats at an airport "
                          "serving a possible first city",
    "NO_RETURN_FLIGHT": "no return flight to {home} departs on {end} with {T} free seats from an airport "
                        "serving a possible last city",
    "NO_HOTEL_AVAILABILITY": "no hotel room type fits the party with enough rooms left on every night",
    "NO_INTERCITY_TRANSFER": "no linked transfer between cities operates on the city-change day",
    "NO_AIRPORT_TRANSFER": "no airport transfer meets the 60-minute arrival / 180-minute departure rules, "
                           "service hours and hotel area",
    "NO_ATTRACTION_SCHEDULE": "not enough distinct open attractions to schedule one on every full day",
}


@dataclass(frozen=True)
class Party:
    adults: int
    children: int

    @property
    def travelers(self) -> int:
        return self.adults + self.children


@dataclass(frozen=True)
class Segment:
    city: str
    check_in: date
    check_out: date

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    def full_days(self, trip_start: date, trip_end: date) -> tuple[date, ...]:
        days = [self.check_in + timedelta(days=i) for i in range(1, self.nights)]
        return tuple(d for d in days if trip_start < d < trip_end)


LONG_EXTRA_WAIT = timedelta(hours=3)  # waiting beyond the mandatory 60/180-minute buffers


def _hm(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes // 60} h {minutes % 60:02d} min"


def arrival_wait_warnings(flight, pickup: datetime) -> list[str]:
    """Visible warning for a next-day or unusually long airport wait (legal, never a rejection)."""
    landed = parse_local(flight["arrival_local"])
    wait = pickup - landed
    if pickup.date() > landed.date():
        return [f"Pickup is the next day ({format_local(pickup)}), {_hm(wait)} after landing: the transfer "
                f"does not run earlier, so travelers wait at the airport."]
    if wait - MIN_LANDING_TO_PICKUP >= LONG_EXTRA_WAIT:
        return [f"Long wait: pickup at {format_local(pickup)} is {_hm(wait)} after landing (transfer service hours)."]
    return []


def departure_wait_warnings(flight, transfer, pickup: datetime) -> list[str]:
    departs = parse_local(flight["departure_local"])
    early = departs - transfer_arrival(transfer, pickup)
    if pickup.date() < departs.date():
        return [f"Pickup is the day before the flight ({format_local(pickup)}); travelers reach the airport "
                f"{_hm(early)} before departure because of the transfer's service hours."]
    if early - MIN_AIRPORT_BEFORE_DEPARTURE >= LONG_EXTRA_WAIT:
        return [f"Long wait: travelers reach the airport {_hm(early)} before departure (transfer service hours)."]
    return []


def _midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


def _dt(d: date, hhmm: str) -> datetime:
    return datetime.combine(d, datetime.strptime(hhmm, "%H:%M").time())


# ---------------------------------------------------------------------------- request

def _parse_request(catalog, request) -> tuple[Party, date, int, dict]:
    errs = build_request_errors(request)
    if errs:
        raise InvalidRequestError("invalid build request", {"errors": errs})
    rules = catalog.rules
    problems = []
    if request["destination_country"] not in catalog.countries:
        problems.append("unknown destination_country")
    if request["departure_city"] not in {d["airport"] for d in catalog.departure_cities}:
        problems.append("unknown departure_city")
    if not rules["min_nights"] <= request["nights"] <= rules["max_nights"]:
        problems.append(f"nights must be {rules['min_nights']}..{rules['max_nights']}")
    if request["adults"] < 1:
        problems.append("adults must be >= 1")
    if any(not 0 <= a <= 17 for a in request["children_ages"]):
        problems.append("children_ages must be 0..17")
    if request["budget_inr"] < 0:
        problems.append("budget_inr must be >= 0")
    if problems:
        raise InvalidRequestError("invalid build request", {"errors": problems})
    party = Party(request["adults"], len(request["children_ages"]))
    return party, date.fromisoformat(request["start_date"]), request["nights"], catalog.countries[
        request["destination_country"]]


def city_plans(country, nights: int, min_per_city: int) -> list[tuple[tuple[str, ...], tuple[int, ...]]]:
    """All (cities, nights split) plans: distinct cities, directed links, each >= min_per_city."""
    cities = [c["name"] for c in country["cities"]]
    links = {(l["from_city"], l["to_city"]) for l in country["links"]}
    out: list = []

    def rec(seq: list[str], split: list[int], remaining: int) -> None:
        if remaining == 0:
            out.append((tuple(seq), tuple(split)))
            return
        for city in cities:
            if city in seq or (seq and (seq[-1], city) not in links):
                continue
            for n in range(min_per_city, remaining + 1):
                if remaining - n == 0 or remaining - n >= min_per_city:
                    rec(seq + [city], split + [n], remaining - n)

    rec([], [], nights)
    return sorted(out, key=lambda p: (len(p[0]), [cities.index(c) for c in p[0]], p[1]))


# ---------------------------------------------------------------------------- planner

class _Search:
    def __init__(self, catalog, party: Party, home: str, start: date, nights: int, country):
        self.cat, self.party, self.home = catalog, party, home
        self.start, self.end = start, start + timedelta(days=nights)
        self.country = country
        self._hotels: dict = {}
        self._attr: dict = {}
        self._arrivals: dict = {}
        self._returns: dict = {}
        self._intercity: dict = {}
        self.only: dict = {}  # G4 restrictions: out/ret flight ids, intercity ids per change day, stars per city

    # ------------------------------------------------------------ component options
    def hotel_options(self, seg: Segment) -> dict:
        """area -> (cost, hotel, room, k): cheapest feasible hotel per area for this stay
        (restricted to one star level when self.only["stars"][city] is set, G4)."""
        key = (seg.city, seg.check_in, seg.check_out)
        if key not in self._hotels:
            best: dict = {}  # (area, stars) -> cheapest option
            ci, co = seg.check_in.isoformat(), seg.check_out.isoformat()
            for hotel in self.cat.hotels_by_city.get(seg.city, ()):
                for room in self.cat.rooms_by_hotel.get(hotel["hotel_id"], ()):
                    k = min_rooms(self.party.adults, self.party.children, room)
                    if k is None or not room_stay_available(self.cat, room["room_id"], ci, co, k):
                        continue
                    opt = (hotel_price(room, k, seg.nights), hotel["hotel_id"], room["room_id"], hotel, room, k)
                    group = (hotel["area"], hotel["stars"])
                    if group not in best or opt[:3] < best[group][:3]:
                        best[group] = opt
            self._hotels[key] = best
        star = self.only.get("stars", {}).get(seg.city)
        per_area: dict = {}
        for (area, stars), o in sorted(self._hotels[key].items()):
            if star is None or stars == star:
                if area not in per_area or o[:3] < per_area[area][:3]:
                    per_area[area] = o
        return {a: (o[0], o[3], o[4], o[5]) for a, o in sorted(per_area.items())}

    def _unrestricted(self, fn, *args):
        saved, self.only = self.only, {}
        try:
            return fn(*args)
        finally:
            self.only = saved

    def star_levels(self, seg: Segment) -> list[int]:
        """Star levels with at least one feasible hotel/room for this stay (G4)."""
        saved, self.only = self.only, {}
        self.hotel_options(seg)
        self.only = saved
        return sorted({stars for _, stars in self._hotels[(seg.city, seg.check_in, seg.check_out)]})

    def outbound_flights(self, city: str) -> list:
        out = []
        for ap in self.cat.airports_serving_city.get(city, ()):
            out += [f for f in self.cat.flights_by_arrival.get((self.home, ap, self.start.isoformat()), ())
                    if f["seats_left"] >= self.party.travelers
                    and f["flight_id"] in self.only.get("out", (f["flight_id"],))]
        return out

    def return_flights(self, city: str) -> list:
        out = []
        for ap in self.cat.airports_serving_city.get(city, ()):
            out += [f for f in self.cat.flights_by_departure.get((ap, self.home, self.end.isoformat()), ())
                    if f["seats_left"] >= self.party.travelers
                    and f["flight_id"] in self.only.get("ret", (f["flight_id"],))]
        return out

    def arrival_options(self, city: str) -> list:
        """(cost, flight, transfer, pickup, at_hotel) for every legal flight + airport transfer."""
        if city not in self._arrivals:
            opts = []
            for f in self._unrestricted(self.outbound_flights, city):
                landed = parse_local(f["arrival_local"])
                for t in self.cat.airport_transfers.get((f["to"], city), ()):
                    pickup = next_departure(landed + MIN_LANDING_TO_PICKUP, t["service_from"],
                                            t["service_until"], t.get("frequency_minutes"))
                    if pickup is None:
                        continue
                    cost = flight_price(f, self.party.travelers) + transfer_price(t, self.party.adults,
                                                                                  self.party.children)
                    opts.append((cost, f, t, pickup, transfer_arrival(t, pickup)))
            opts.sort(key=lambda o: (o[0], o[1]["flight_id"], o[2]["transfer_id"]))
            self._arrivals[city] = opts
        allowed = self.only.get("out")
        return [o for o in self._arrivals[city] if allowed is None or o[1]["flight_id"] in allowed]

    def return_options(self, city: str) -> list:
        """(cost, flight, transfer, pickup) with airport reached >= 180 min before departure."""
        if city not in self._returns:
            opts = []
            for f in self._unrestricted(self.return_flights, city):
                deadline = parse_local(f["departure_local"]) - MIN_AIRPORT_BEFORE_DEPARTURE
                for t in self.cat.airport_transfers.get((f["from"], city), ()):
                    latest = deadline - timedelta(minutes=t["duration_minutes"])
                    pickup = latest_departure(latest, t["service_from"], t["service_until"],
                                              t.get("frequency_minutes"))
                    if pickup is None:
                        continue
                    cost = flight_price(f, self.party.travelers) + transfer_price(t, self.party.adults,
                                                                                  self.party.children)
                    opts.append((cost, f, t, pickup))
            opts.sort(key=lambda o: (o[0], o[1]["flight_id"], o[2]["transfer_id"]))
            self._returns[city] = opts
        allowed = self.only.get("ret")
        return [o for o in self._returns[city] if allowed is None or o[1]["flight_id"] in allowed]

    def intercity_options(self, frm: str, to: str, day: date) -> list:
        """(cost, transfer, departure) per linked transfer with a legal departure that day."""
        key = (frm, to, day)
        if key not in self._intercity:
            opts = []
            for t in self.cat.intercity_transfers.get((frm, to), ()):
                window = (t["service_from"], t["service_until"], t.get("frequency_minutes"))
                dep = next_departure(_dt(day, PREFERRED_INTERCITY_DEPARTURE), *window)
                if dep is None or dep.date() != day:
                    dep = next_departure(_midnight(day), *window)
                if dep is None or dep.date() != day:
                    continue
                opts.append((transfer_price(t, self.party.adults, self.party.children), t, dep))
            opts.sort(key=lambda o: (o[0], o[1]["transfer_id"]))
            self._intercity[key] = opts
        return self._intercity[key]

    def attractions(self, seg: Segment, enter: datetime | None, leave: datetime | None):
        """Min-cost distinct attractions, one per full day of seg, visits within [enter, leave].
        Returns (cost, ((day, attraction, start), ...)) or None."""
        days = seg.full_days(self.start, self.end)
        if not days:
            return 0, ()
        lb = enter if enter and enter > _midnight(days[0]) else None
        ub = leave if leave and leave < _midnight(days[-1] + timedelta(days=1)) else None
        key = (seg.city, days, lb, ub)
        if key in self._attr:
            return self._attr[key]
        price = lambda a: attraction_price(a, self.party.adults, self.party.children)  # noqa: E731
        candidates = sorted(self.cat.attractions_by_city.get(seg.city, ()),
                            key=lambda a: (price(a), a["attraction_id"]))
        eligible: dict[str, list[date]] = {}
        by_id = {}
        match_day: dict[date, str] = {}

        def augment(aid: str, seen: set) -> bool:
            for d in eligible[aid]:
                if d in seen:
                    continue
                seen.add(d)
                if d not in match_day or augment(match_day[d], seen):
                    match_day[d] = aid
                    return True
            return False

        for a in candidates:
            if len(match_day) == len(days):
                break
            aid = a["attraction_id"]
            eligible[aid] = [d for d in days if attraction_start(a, d, lb, ub) is not None]
            by_id[aid] = a
            if eligible[aid]:
                augment(aid, set())
        if len(match_day) < len(days):
            result = None
        else:
            visits = tuple((d, by_id[aid], attraction_start(by_id[aid], d, lb, ub))
                           for d, aid in sorted(match_day.items()))
            result = (sum(price(a) for _, a, _ in visits), visits)
        self._attr[key] = result
        return result

    # ------------------------------------------------------------ plan evaluation
    def evaluate(self, cities, split):
        """Cheapest package for one plan: (total, components) or (None, failing_stage)."""
        segs, day = [], self.start
        for city, n in zip(cities, split):
            segs.append(Segment(city, day, day + timedelta(days=n)))
            day += timedelta(days=n)
        first, last = segs[0], segs[-1]
        if not self.outbound_flights(first.city):
            return None, 0
        if not self.return_flights(last.city):
            return None, 1
        hotels = [self.hotel_options(s) for s in segs]
        if not all(hotels):
            return None, 2
        inter_opts = [self.intercity_options(segs[i - 1].city, segs[i].city, segs[i].check_in)
                      for i in range(1, len(segs))]
        if "inter" in self.only:  # G4: one fixed intercity transfer per change day
            inter_opts = [[o for o in opts if o[1]["transfer_id"] == tid]
                          for opts, tid in zip(inter_opts, self.only["inter"])]
        if not all(inter_opts):
            return None, 3
        arrivals, returns = self.arrival_options(first.city), self.return_options(last.city)
        best, stage = None, 4
        for inter in itertools.product(*inter_opts):
            found = self._evaluate_fixed(segs, hotels, inter, arrivals, returns)
            if found[0] is None:
                stage = max(stage, found[1])
            elif best is None or found[0] < best[0]:
                best = found
        return (best if best else (None, stage))

    def _evaluate_fixed(self, segs, hotels, inter, arrivals, returns):
        m = len(segs)
        entry = [None] * m  # time the traveler reaches each city
        leave = [None] * m  # time the traveler leaves each city
        for i, (_, t, dep) in enumerate(inter, start=1):
            leave[i - 1] = dep
            entry[i] = transfer_arrival(t, dep)
        cost = sum(c for c, _, _ in inter)
        middle = []
        for i in range(1, m - 1):
            area_opts = hotels[i]
            area = min(area_opts, key=lambda a: (area_opts[a][0], area_opts[a][1]["hotel_id"],
                                                 area_opts[a][2]["room_id"]))
            att = self.attractions(segs[i], entry[i], leave[i])
            if att is None:
                return None, 5
            cost += area_opts[area][0] + att[0]
            middle.append((area_opts[area], att))
        transfers_ok = False
        if m == 1:
            best = None
            for area, hopt in hotels[0].items():
                for a in arrivals:
                    if not transfer_serves_area(a[2], segs[0].city, area):
                        continue
                    for r in returns:
                        if not transfer_serves_area(r[2], segs[0].city, area) or r[3] < a[4]:
                            continue
                        transfers_ok = True
                        att = self.attractions(segs[0], a[4], r[3])
                        if att is None:
                            continue
                        total = hopt[0] + a[0] + r[0] + att[0]
                        if best is None or total < best[0]:
                            best = (total, area, hopt, a, r, att)
            if best is None:
                return None, 5 if transfers_ok else 4
            total, area, hopt, a, r, att = best
            return cost + total, {"segs": segs, "hotels": [hopt], "attractions": [att], "arrival": a,
                                  "return": r, "intercity": list(inter)}
        # m >= 2: first and last blocks are independent given the intercity choice.
        head = tail = None
        head_t = tail_t = False
        for area, hopt in hotels[0].items():
            for a in arrivals:
                if not transfer_serves_area(a[2], segs[0].city, area) or a[4] > leave[0]:
                    continue
                head_t = True
                att = self.attractions(segs[0], a[4], leave[0])
                if att is not None and (head is None or hopt[0] + a[0] + att[0] < head[0]):
                    head = (hopt[0] + a[0] + att[0], hopt, a, att)
        for area, hopt in hotels[-1].items():
            for r in returns:
                if not transfer_serves_area(r[2], segs[-1].city, area) or r[3] < entry[-1]:
                    continue
                tail_t = True
                att = self.attractions(segs[-1], entry[-1], r[3])
                if att is not None and (tail is None or hopt[0] + r[0] + att[0] < tail[0]):
                    tail = (hopt[0] + r[0] + att[0], hopt, r, att)
        if not head_t or not tail_t:
            return None, 4
        if head is None or tail is None:
            return None, 5
        return cost + head[0] + tail[0], {
            "segs": segs, "hotels": [head[1]] + [h for h, _ in middle] + [tail[1]],
            "attractions": [head[3]] + [a for _, a in middle] + [tail[3]],
            "arrival": head[2], "return": tail[2], "intercity": list(inter)}


# ---------------------------------------------------------------------------- assembly

def assemble_trip(catalog, request: dict, trip_id: str, plan: dict) -> dict:
    """Judge-format draft trip from chosen components; every price comes from the catalog."""
    party = Party(request["adults"], len(request["children_ages"]))
    A, C, T = party.adults, party.children, party.travelers
    home_city = next(d["city"] for d in catalog.departure_cities if d["airport"] == request["departure_city"])
    segs = plan["segs"]
    items: list[dict] = []

    def add(kind, role, name, city, cid, start, end, qty, price, room_id=None):
        items.append({"item_id": f"i{len(items) + 1}", "type": kind, "role": role, "name": name, "city": city,
                      "status": "PLANNED", "catalog_id": cid, "room_id": room_id, "start": start, "end": end,
                      "quantity": qty, "price_inr": price, "booking": None, "sources": [], "warnings": []})

    def add_flight(f, role, city):
        add("flight", role, f"{f['flight_number']} {f['from']} to {f['to']}", city, f["flight_id"],
            f["departure_local"][:16], f["arrival_local"][:16], T, flight_price(f, T))

    def add_transfer(t, role, city, pickup):
        add("transfer", role, t["name"], city, t["transfer_id"], format_local(pickup),
            format_local(transfer_arrival(t, pickup)), transfer_quantity(t, T), transfer_price(t, A, C))

    _, f_out, t_arr, pickup_arr, _ = plan["arrival"]
    add_flight(f_out, "outbound_flight", segs[0].city)
    add_transfer(t_arr, "arrival_transfer", segs[0].city, pickup_arr)
    items[-1]["warnings"] = arrival_wait_warnings(f_out, pickup_arr)
    for i, seg in enumerate(segs):
        if i:
            _, t, dep = plan["intercity"][i - 1]
            add_transfer(t, "intercity_transfer", seg.city, dep)
        _, hotel, room, k = plan["hotels"][i]
        add("hotel", "hotel_stay", hotel["name"], seg.city, hotel["hotel_id"], seg.check_in.isoformat(),
            seg.check_out.isoformat(), k, hotel_price(room, k, seg.nights), room_id=room["room_id"])
        for _, a, start in plan["attractions"][i][1]:
            add("attraction", "attraction_visit", a["name"], seg.city, a["attraction_id"], format_local(start),
                format_local(start + timedelta(minutes=a["visit_minutes"])), T, attraction_price(a, A, C))
    _, f_ret, t_dep, pickup_dep = plan["return"]
    add_transfer(t_dep, "departure_transfer", segs[-1].city, pickup_dep)
    items[-1]["warnings"] = departure_wait_warnings(f_ret, t_dep, pickup_dep)
    add_flight(f_ret, "return_flight", home_city)
    start = date.fromisoformat(request["start_date"])
    return {
        "trip_id": trip_id,
        "traveler": {"name": None, "home_airport": request["departure_city"], "adults": A, "children": C,
                     "child_ages": list(request["children_ages"])},
        "country": request["destination_country"],
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=request["nights"])).isoformat(),
        "budget_inr": request["budget_inr"],
        "total_inr": sum(i["price_inr"] for i in items),
        "items": items,
    }


def _result(status, trip=None, cheapest=None, shortfall=None, reason_code=None, reason=None, suggestion=None):
    return {"status": status, "trip": trip, "cheapest_total_inr": cheapest, "shortfall_inr": shortfall,
            "reason_code": reason_code, "reason": reason, "suggestion": suggestion}


def build_trip(catalog, request: dict, trip_id: str) -> dict:
    """DomainPort.build: BUILT (cheapest package, within budget), OVER_BUDGET or NO_PACKAGE."""
    party, start, nights, country = _parse_request(catalog, request)
    end = start + timedelta(days=nights)
    window = catalog.rules["data_window"]
    if start.isoformat() < window["first_night"] or end.isoformat() > window["last_departure"]:
        return _result("NO_PACKAGE", reason_code="OUTSIDE_DATA_WINDOW",
                       reason=f"The trip must start on or after {window['first_night']} and return on or before "
                              f"{window['last_departure']}; this request returns on {end.isoformat()}.",
                       suggestion="Choose dates inside the catalog window.")
    plans = city_plans(country, nights, catalog.rules["min_nights_per_city"])
    if not plans:
        return _result("NO_PACKAGE", reason_code="NO_CITY_PLAN",
                       reason=f"No city plan in {country['name']} gives {nights} nights with at least "
                              f"{catalog.rules['min_nights_per_city']} nights per city.")
    search = _Search(catalog, party, request["departure_city"], start, nights, country)
    best, deepest = None, -1
    for cities, split in plans:
        total, detail = search.evaluate(cities, split)
        if total is None:
            deepest = max(deepest, detail)
        elif best is None or total < best[0]:
            best = (total, detail)
    if best is None:
        code = STAGES[deepest]
        text = STAGE_TEXT[code].format(home=request["departure_city"], start=start, end=end, T=party.travelers)
        return _result("NO_PACKAGE", reason_code=code,
                       reason=f"No complete package exists for this request: {text}.",
                       suggestion="Try other dates, fewer nights, fewer travelers or another country.")
    trip = assemble_trip(catalog, request, trip_id, best[1])
    if trip["total_inr"] != best[0]:
        raise RuntimeError("planner cost model disagrees with assembled trip prices")
    problems = [v for v in validate_trip(catalog, trip, "initial") if v["code"] != "OVER_BUDGET"]
    if problems:  # never return an invalid package; this is a planner bug, surface it loudly
        raise RuntimeError(f"planner produced an invalid trip: {problems[:3]}")
    cheapest = trip["total_inr"]
    if cheapest > request["budget_inr"]:
        shortfall = cheapest - request["budget_inr"]
        return _result("OVER_BUDGET", cheapest=cheapest, shortfall=shortfall,
                       reason=f"The cheapest complete package costs INR {cheapest:,}, which is INR {shortfall:,} "
                              f"over the budget of INR {request['budget_inr']:,}.",
                       suggestion=f"Increase the budget to at least INR {cheapest:,}, or reduce the nights "
                                  f"or number of travelers.")
    return _result("BUILT", trip=trip, cheapest=cheapest)


# ============================================================================ G3: change city plan (extension)

def _request_from_trip(trip: dict) -> dict:
    """The original 7-field request, recovered from a trip (travelers/country/dates/budget are fixed)."""
    start, end = date.fromisoformat(trip["start_date"]), date.fromisoformat(trip["end_date"])
    return {"departure_city": trip["traveler"]["home_airport"], "destination_country": trip["country"],
            "start_date": trip["start_date"], "nights": (end - start).days, "adults": trip["traveler"]["adults"],
            "children_ages": list(trip["traveler"]["child_ages"]), "budget_inr": trip["budget_inr"]}


def _plan_problems(catalog, country, cities, nights, total_nights: int) -> list[dict]:
    from app.domain.validator import violation
    if (not isinstance(cities, list) or not isinstance(nights, list) or not cities or len(cities) != len(nights)
            or not all(isinstance(c, str) for c in cities)
            or not all(isinstance(n, int) and not isinstance(n, bool) for n in nights)):
        return [violation("CHANGE_MALFORMED", "city plan needs equal-length lists of city names and night counts")]
    out = []
    known = [c["name"] for c in country["cities"]]
    links = {(l["from_city"], l["to_city"]) for l in country["links"]}
    for c in cities:
        if c not in known:
            out.append(violation("CITY_NOT_IN_COUNTRY", f"{c!r} is not a city of {country['name']}"))
    if len(set(cities)) != len(cities):
        out.append(violation("CITY_REPEATED", "each city may appear only once"))
    if any(n < catalog.rules["min_nights_per_city"] for n in nights):
        out.append(violation("CITY_MIN_NIGHTS", f"every city needs at least {catalog.rules['min_nights_per_city']} "
                                                f"consecutive nights"))
    if sum(nights) != total_nights:
        out.append(violation("PLAN_NIGHTS_MISMATCH", f"nights must add up to the trip's {total_nights} nights"))
    for a, b in zip(cities, cities[1:]):
        if (a, b) not in links:
            out.append(violation("NO_CITY_LINK", f"no directed transfer link {a} -> {b}"))
    return out


def city_plan_options(catalog, trip: dict) -> list[dict]:
    """Every feasible city plan for this trip's fixed request, cheapest first (for a plan picker)."""
    req = _request_from_trip(trip)
    party = Party(req["adults"], len(req["children_ages"]))
    country = catalog.countries[req["destination_country"]]
    search = _Search(catalog, party, req["departure_city"], date.fromisoformat(req["start_date"]), req["nights"],
                     country)
    out = []
    for cities, split in city_plans(country, req["nights"], catalog.rules["min_nights_per_city"]):
        total, _ = search.evaluate(cities, split)
        if total is not None:
            out.append({"cities": list(cities), "nights": list(split), "total_inr": total,
                        "price_diff_inr": total - trip["total_inr"], "within_budget": total <= req["budget_inr"]})
    return sorted(out, key=lambda p: (p["total_inr"], p["cities"], p["nights"]))


def rebuild_city_plan(catalog, trip: dict, cities, nights) -> dict:
    """G3: rebuild the complete package for a chosen city plan. Returns a ChangeResult-shaped dict:
    the cheapest valid package for exactly that plan, or ok=false (original trip never modified)."""
    from app.domain.validator import violation
    reject = lambda v: {"ok": False, "trip": None, "violations": v, "adjustments": []}  # noqa: E731
    req = _request_from_trip(trip)
    country = catalog.countries.get(req["destination_country"])
    if country is None:
        return reject([violation("COUNTRY_UNKNOWN", f"unknown country {req['destination_country']!r}")])
    problems = _plan_problems(catalog, country, cities, nights, req["nights"])
    if problems:
        return reject(problems)
    party = Party(req["adults"], len(req["children_ages"]))
    search = _Search(catalog, party, req["departure_city"], date.fromisoformat(req["start_date"]), req["nights"],
                     country)
    total, detail = search.evaluate(tuple(cities), tuple(nights))
    if total is None:
        code = STAGES[detail]
        text = STAGE_TEXT[code].format(home=req["departure_city"], start=req["start_date"],
                                       end=trip["end_date"], T=party.travelers)
        return reject([violation(code, f"No complete package for {' -> '.join(cities)}: {text}.")])
    new = assemble_trip(catalog, req, trip["trip_id"], detail)
    problems = [v for v in validate_trip(catalog, new, "initial") if v["code"] != "OVER_BUDGET"]
    if new["total_inr"] != total or problems:
        raise RuntimeError(f"city-plan rebuild produced an invalid trip: {problems[:3]}")
    if new["total_inr"] > req["budget_inr"]:
        return reject([violation("OVER_BUDGET", f"the cheapest package for {' -> '.join(cities)} costs INR "
                                                f"{new['total_inr']:,}, over the budget of INR {req['budget_inr']:,}")])
    old_plan = " -> ".join(f"{h['city']} ({(date.fromisoformat(h['end']) - date.fromisoformat(h['start'])).days})"
                           for h in sorted((i for i in trip["items"] if i["role"] == "hotel_stay"),
                                           key=lambda i: i["start"]))
    new_plan = " -> ".join(f"{c} ({n})" for c, n in zip(cities, nights))
    diff = new["total_inr"] - trip["total_inr"]
    return {"ok": True, "trip": new, "violations": [],
            "adjustments": [f"City plan changed from {old_plan} to {new_plan}; flights, hotels, transfers and "
                            f"attractions were rebuilt (total {diff:+,} INR)."]}


# ============================================================================ G4: three recommendations (extension)

def _segments(start: date, cities, split) -> list[Segment]:
    segs, day = [], start
    for city, n in zip(cities, split):
        segs.append(Segment(city, day, day + timedelta(days=n)))
        day += timedelta(days=n)
    return segs


def trip_metrics(catalog, trip: dict) -> dict:
    """G4 objectives of a judge-format trip: travel minutes = both flights + every intercity transfer
    (airport transfers, waits and hotel time excluded); average hotel stars weighted by nights (guest
    rating ignored)."""
    flights = sum(catalog.flights[i["catalog_id"]]["duration_minutes"] for i in trip["items"] if i["type"] == "flight")
    inter = sum(catalog.transfers[i["catalog_id"]]["duration_minutes"] for i in trip["items"]
                if i["role"] == "intercity_transfer")
    nights = star_nights = 0
    for h in (i for i in trip["items"] if i["role"] == "hotel_stay"):
        n = (date.fromisoformat(h["end"]) - date.fromisoformat(h["start"])).days
        nights += n
        star_nights += catalog.hotels[h["catalog_id"]]["stars"] * n
    return {"travel_minutes": flights + inter, "star_nights": star_nights,
            "average_stars": round(star_nights / nights, 2) if nights else 0}


def _best_constrained(search: _Search, candidates, budget: int):
    """Candidates are (objective_rank, cities, split, restriction) in ascending rank order (lower is better).
    Returns (rank, total, plan) of the cheapest within-budget package at the best feasible rank, or None.
    Exact: a rank is abandoned only once a better rank has a feasible within-budget package."""
    best = None
    for rank, cities, split, only in candidates:
        if best is not None and rank > best[0]:
            break
        search.only = only
        total, detail = search.evaluate(cities, split)
        search.only = {}
        if total is not None and total <= budget and (best is None or total < best[1]):
            best = (rank, total, detail)
    return best


def recommend_packages(catalog, request: dict, trip_id: str) -> dict:
    """G4: the BuildResult of build_trip() plus `recommendations` = cheapest / fastest / best_rated, each a
    complete, validated, within-budget package (the same package may win several labels). For OVER_BUDGET and
    NO_PACKAGE the build result is returned unchanged with no recommendations."""
    base = build_trip(catalog, request, trip_id)
    if base["status"] != "BUILT":
        return {**base, "recommendations": []}
    party, start, nights, country = _parse_request(catalog, request)
    search = _Search(catalog, party, request["departure_city"], start, nights, country)
    budget = request["budget_inr"]
    plans = city_plans(country, nights, catalog.rules["min_nights_per_city"])

    fastest = []
    for order, (cities, split) in enumerate(plans):
        segs = _segments(start, cities, split)
        inter = [search.intercity_options(segs[i - 1].city, segs[i].city, segs[i].check_in) for i in range(1, len(segs))]
        for fo, fr in itertools.product(search.outbound_flights(cities[0]), search.return_flights(cities[-1])):
            for combo in itertools.product(*inter):
                minutes = fo["duration_minutes"] + fr["duration_minutes"] + sum(t["duration_minutes"] for _, t, _ in combo)
                fastest.append(((minutes, order), cities, split, {
                    "out": {fo["flight_id"]}, "ret": {fr["flight_id"]},
                    "inter": tuple(t["transfer_id"] for _, t, _ in combo)}))
    fastest.sort(key=lambda c: (c[0], c[3]["out"].copy().pop(), c[3]["ret"].copy().pop(), c[3]["inter"]))
    fastest = [((rank[0],), cities, split, only) for rank, cities, split, only in fastest]

    rated = []
    for order, (cities, split) in enumerate(plans):
        segs = _segments(start, cities, split)
        levels = [search.star_levels(s) for s in segs]
        for stars in itertools.product(*levels):
            score = sum(s * n for s, n in zip(stars, split))
            rated.append(((-score,), order, cities, split, {"stars": dict(zip(cities, stars))}))
    rated.sort(key=lambda c: (c[0], c[1]))
    rated = [(rank, cities, split, only) for rank, _, cities, split, only in rated]

    out = [("cheapest", base["trip"])]
    for label, candidates in (("fastest", fastest), ("best_rated", rated)):
        found = _best_constrained(search, candidates, budget)
        if found is None:  # cannot happen when BUILT (the cheapest package is a candidate); never fabricate
            raise RuntimeError(f"no within-budget {label} package although the cheapest one fits")
        trip = assemble_trip(catalog, request, trip_id, found[2])
        if trip["total_inr"] != found[1] or validate_trip(catalog, trip, "initial"):
            raise RuntimeError(f"{label} recommendation failed self-validation")
        out.append((label, trip))
    recs = [{"label": label, "trip": trip, "total_inr": trip["total_inr"], **trip_metrics(catalog, trip)}
            for label, trip in out]
    return {**base, "recommendations": recs}
