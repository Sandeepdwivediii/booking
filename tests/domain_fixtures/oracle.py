"""TEST-ONLY independent brute-force oracle and seeded random catalogs.

`brute_force_cheapest` shares NO search logic with app.domain.planner: it re-implements the
component filters naively (full catalog scans, every room count k, every attraction
permutation, minute-by-minute time scans) and accepts a combination only if the production
validator (`validate_trip`, itself tested rule by rule) returns no violation except OVER_BUDGET.
Branch-and-bound on cost only skips combinations that cannot beat the best valid one found.
"""

from __future__ import annotations

import itertools
import random
from datetime import date, datetime, timedelta

from app.domain.rules import attraction_visit_ok, transfer_departure_ok
from app.domain.validator import validate_trip

INR = lambda a: {"amount": a, "currency": "INR"}  # noqa: E731


# ------------------------------------------------------------------ naive prices (independent)

def _seat(f, T):
    return f["price_per_seat"]["amount"] * T


def _transfer(t, A, C):
    if t["pricing"] == "PER_VEHICLE":
        return t["price"]["amount"] * ((A + C + t["max_passengers"] - 1) // t["max_passengers"])
    child = t.get("child_price") or t["price"]
    return A * t["price"]["amount"] + C * child["amount"]


def _qty(t, T):
    return (T + t["max_passengers"] - 1) // t["max_passengers"] if t["pricing"] == "PER_VEHICLE" else T


def _attr(a, A, C):
    child = a.get("child_price") or a["price_per_person"]
    return A * a["price_per_person"]["amount"] + C * child["amount"]


def _fmt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


def _first_legal(t, start, minutes, step=1):
    for i in range(0, minutes, step):
        cand = start + timedelta(minutes=i)
        if transfer_departure_ok(t, cand):
            return cand
    return None


def _last_legal(t, latest, minutes):
    for i in range(0, minutes):
        cand = latest - timedelta(minutes=i)
        if transfer_departure_ok(t, cand):
            return cand
    return None


# ------------------------------------------------------------------ brute force

def brute_force_cheapest(cat, req):
    """(min_total, trip) over every combination, or None if no valid package exists."""
    A, ages = req["adults"], req["children_ages"]
    C, T = len(ages), A + len(ages)
    H, S, N = req["departure_city"], date.fromisoformat(req["start_date"]), req["nights"]
    E = S + timedelta(days=N)
    country = cat.countries[req["destination_country"]]
    cities = [c["name"] for c in country["cities"]]
    links = {(l["from_city"], l["to_city"]): l["transfer_ids"] for l in country["links"]}
    min_n = cat.rules["min_nights_per_city"]
    serves = lambda ap, city: city in (cat.airports[ap].get("serves_cities") or [])  # noqa: E731
    best = [None, None]

    plans = []
    for m in range(1, len(cities) + 1):
        for perm in itertools.permutations(cities, m):
            if any((perm[i], perm[i + 1]) not in links for i in range(m - 1)):
                continue
            for split in itertools.product(range(min_n, N + 1), repeat=m):
                if sum(split) == N:
                    plans.append((perm, split))

    for perm, split in plans:
        segs, d = [], S
        for city, n in zip(perm, split):
            segs.append((city, d, d + timedelta(days=n)))
            d += timedelta(days=n)
        c1, cm = perm[0], perm[-1]
        outs = []
        for f in cat.flights.values():
            if f["from"] == H and f["arrival_local"][:10] == S.isoformat() and f["seats_left"] >= T and serves(
                    f["to"], c1):
                landed = datetime.fromisoformat(f["arrival_local"])
                for t in cat.transfers.values():
                    if t.get("airport") == f["to"] and t.get("city") == c1:
                        p = _first_legal(t, landed + timedelta(minutes=60), 36 * 60)
                        if p:
                            outs.append((_seat(f, T) + _transfer(t, A, C), f, t, p))
        rets = []
        for f in cat.flights.values():
            if f["to"] == H and f["departure_local"][:10] == E.isoformat() and f["seats_left"] >= T and serves(
                    f["from"], cm):
                dep = datetime.fromisoformat(f["departure_local"])
                for t in cat.transfers.values():
                    if t.get("airport") == f["from"] and t.get("city") == cm:
                        p = _last_legal(t, dep - timedelta(minutes=180 + t["duration_minutes"]), 36 * 60)
                        if p:
                            rets.append((_seat(f, T) + _transfer(t, A, C), f, t, p))
        hotel_opts = []
        for city, ci, co in segs:
            opts = []
            for h in cat.hotels.values():
                if h["city"] != city:
                    continue
                for r in cat.rooms.values():
                    if r["hotel_id"] != h["hotel_id"]:
                        continue
                    for k in range(1, A + 1):
                        if not (A <= k * r["max_adults"] and T <= k * (r["max_adults"] + r["max_children"])):
                            continue
                        nights = [(ci + timedelta(days=i)).isoformat() for i in range((co - ci).days)]
                        if all((cat.availability.get((r["room_id"], n)) or 0) >= k for n in nights):
                            opts.append((r["price_per_night"]["amount"] * k * len(nights), h, r, k))
            hotel_opts.append(opts)
        inter_opts = []
        for i in range(1, len(segs)):
            opts = []
            for tid in links[(perm[i - 1], perm[i])]:
                t = cat.transfers[tid]
                day = segs[i][1]
                p = _first_legal(t, datetime(day.year, day.month, day.day, 9), 15 * 60) or _first_legal(
                    t, datetime(day.year, day.month, day.day), 24 * 60)
                if p:
                    opts.append((_transfer(t, A, C), t, p))
            inter_opts.append(opts)
        attr_opts = []
        for city, ci, co in segs:
            full = [ci + timedelta(days=i) for i in range(1, (co - ci).days) if S < ci + timedelta(days=i) < E]
            pool = [a for a in cat.attractions.values() if a["city"] == city]
            attr_opts.append([(sum(_attr(a, A, C) for a in combo), full, combo)
                              for combo in itertools.permutations(pool, len(full))])
        for o, r, hs, ics, ats in itertools.product(outs, rets, itertools.product(*hotel_opts),
                                                   itertools.product(*inter_opts), itertools.product(*attr_opts)):
            cost = o[0] + r[0] + sum(h[0] for h in hs) + sum(x[0] for x in ics) + sum(x[0] for x in ats)
            if best[0] is not None and cost >= best[0]:
                continue
            trip = _assemble(cat, req, segs, o, r, hs, ics, ats, A, C, T, E)
            if trip is None:
                continue
            if all(v["code"] == "OVER_BUDGET" for v in validate_trip(cat, trip, "initial")):
                best = [cost, trip]
    return None if best[0] is None else tuple(best)


def _assemble(cat, req, segs, o, r, hs, ics, ats, A, C, T, E):
    items = []

    def add(kind, role, cid, city, start, end, qty, price, room_id=None):
        items.append({"item_id": f"x{len(items)}", "type": kind, "role": role, "name": cid, "city": city,
                      "status": "PLANNED", "catalog_id": cid, "room_id": room_id, "start": start, "end": end,
                      "quantity": qty, "price_inr": price, "booking": None, "sources": [], "warnings": []})

    _, fo, ta, pa = o
    add("flight", "outbound_flight", fo["flight_id"], segs[0][0], fo["departure_local"][:16],
        fo["arrival_local"][:16], T, _seat(fo, T))
    enter = pa + timedelta(minutes=ta["duration_minutes"])
    add("transfer", "arrival_transfer", ta["transfer_id"], segs[0][0], _fmt(pa), _fmt(enter), _qty(ta, T),
        _transfer(ta, A, C))
    _, fr, td, pd = r
    for i, (city, ci, co) in enumerate(segs):
        if i:
            _, t, p = ics[i - 1]
            enter = p + timedelta(minutes=t["duration_minutes"])
            add("transfer", "intercity_transfer", t["transfer_id"], city, _fmt(p), _fmt(enter), _qty(t, T),
                _transfer(t, A, C))
        leave = ics[i][2] if i < len(segs) - 1 else pd
        price, h, room, k = hs[i]
        add("hotel", "hotel_stay", h["hotel_id"], city, ci.isoformat(), co.isoformat(), k, price,
            room["room_id"])
        _, days, combo = ats[i]
        for day, a in zip(days, combo):
            start = None
            for step in range(0, 24 * 12):
                cand = datetime(day.year, day.month, day.day) + timedelta(minutes=5 * step)
                fin = cand + timedelta(minutes=a["visit_minutes"])
                if cand >= enter and fin <= leave and attraction_visit_ok(a, cand):
                    start = cand
                    break
            if start is None:
                return None
            add("attraction", "attraction_visit", a["attraction_id"], city, _fmt(start),
                _fmt(start + timedelta(minutes=a["visit_minutes"])), T, _attr(a, A, C))
    add("transfer", "departure_transfer", td["transfer_id"], segs[-1][0], _fmt(pd),
        _fmt(pd + timedelta(minutes=td["duration_minutes"])), _qty(td, T), _transfer(td, A, C))
    home_city = next(d["city"] for d in cat.departure_cities if d["airport"] == req["departure_city"])
    add("flight", "return_flight", fr["flight_id"], home_city, fr["departure_local"][:16], fr["arrival_local"][:16],
        T, _seat(fr, T))
    return {"trip_id": "oracle", "traveler": {"name": None, "home_airport": req["departure_city"], "adults": A,
            "children": C, "child_ages": list(req["children_ages"])}, "country": req["destination_country"],
            "start_date": req["start_date"], "end_date": E.isoformat(), "budget_inr": req["budget_inr"],
            "total_inr": sum(i["price_inr"] for i in items), "items": items}


# ------------------------------------------------------------------ seeded random catalogs

WINDOWS = [("06:00", "23:00"), ("00:00", "24:00"), ("08:00", "20:00"), ("06:00", "00:30"), ("05:00", "24:00")]
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def random_catalog(seed: int) -> tuple[dict, dict]:
    """Small random but loader-valid catalog plus a request (start 2027-04-10)."""
    rng = random.Random(seed)
    cities = ["Tokyo", "Kyoto", "Osaka"][: rng.choice([2, 2, 3])]
    nights = rng.choice([3, 4, 4, 5])
    S = date(2027, 4, 10)
    E = S + timedelta(days=nights)
    aps = {"AP1": [cities[0]], "AP2": cities[1:]}
    data = {
        "airports": [{"code": "BLR", "name": "BLR", "city": "Bengaluru", "country": "IN", "timezone": "Asia/Kolkata"}]
        + [{"code": c, "name": c, "city": s[0], "country": "JP", "timezone": "Asia/Tokyo", "serves_cities": s}
           for c, s in aps.items()],
        "flights": [], "hotels": [], "rooms": [], "availability": [], "transfers": [], "attractions": [],
    }
    links = []
    for a, b in itertools.permutations(cities, 2):
        if rng.random() < 0.6:
            ids = []
            for j in range(rng.choice([1, 2])):
                tid = f"TR-{a[:3]}-{b[:3]}-{j}"
                ids.append(tid)
                win = rng.choice(WINDOWS)
                data["transfers"].append({
                    "transfer_id": tid, "type": rng.choice(["TRAIN", "INTERCITY_TAXI"]), "name": tid,
                    "airport": None, "city": None, "area": None, "from_city": a, "to_city": b,
                    "max_passengers": rng.choice([3, 4, 99]), "service_from": win[0], "service_until": win[1],
                    "frequency_minutes": rng.choice([None, 30, 60]), "duration_minutes": rng.choice([60, 135]),
                    "pricing": rng.choice(["PER_PERSON", "PER_VEHICLE"]), "price": INR(rng.randrange(1000, 9000, 500))})
            links.append({"from_city": a, "to_city": b, "transfer_ids": ids})
    data["destinations"] = {
        "departure_cities": [{"airport": "BLR", "city": "Bengaluru"}],
        "countries": [{"code": "JP", "name": "Japan", "timezone": "Asia/Tokyo",
                       "cities": [{"name": c, "airports": [ap for ap, s in aps.items() if c in s]} for c in cities],
                       "links": links}],
        "rules": {"min_nights": 3, "max_nights": 10, "min_nights_per_city": 2,
                  "data_window": {"first_night": "2027-04-01", "last_departure": "2027-04-20"}},
    }
    fid = itertools.count()
    for ap in aps:
        for day in (S - timedelta(days=1), S, S + timedelta(days=1)):
            for _ in range(rng.choice([1, 2, 3]) if day == S else rng.choice([0, 1])):
                dep = datetime(day.year, day.month, day.day, rng.randrange(0, 24), rng.choice([0, 30])) - timedelta(
                    hours=rng.choice([8, 14]))
                dur = rng.choice([450, 510])
                arr = dep + timedelta(minutes=dur + 210)  # IST -> JST local shift
                data["flights"].append(_flight(f"FL-{next(fid)}", "BLR", ap, dep, arr, dur, rng))
        for day in (E - timedelta(days=1), E, E + timedelta(days=1)):
            for _ in range(rng.choice([1, 2, 3]) if day == E else rng.choice([0, 1])):
                dep = datetime(day.year, day.month, day.day, rng.randrange(6, 24), rng.choice([0, 30]))
                dur = rng.choice([480, 540])
                data["flights"].append(_flight(f"FL-{next(fid)}", ap, "BLR", dep, dep + timedelta(minutes=dur - 210),
                                               dur, rng))
        for city in aps[ap]:
            for j in range(rng.choice([1, 2])):
                win = rng.choice(WINDOWS)
                pricing = rng.choice(["PER_PERSON", "PER_VEHICLE"])
                rec = {"transfer_id": f"TR-{ap}-{city[:3]}-{j}", "type": "AIRPORT_TAXI", "name": "x", "airport": ap,
                       "city": city, "area": rng.choice([None, "A", "B"]), "max_passengers": rng.choice([2, 4]),
                       "service_from": win[0], "service_until": win[1], "duration_minutes": rng.choice([45, 90]),
                       "pricing": pricing, "price": INR(rng.randrange(1000, 6000, 250))}
                if pricing == "PER_PERSON" and rng.random() < 0.5:
                    rec["child_price"] = INR(rng.randrange(0, 1000, 250))
                data["transfers"].append(rec)
    for city in cities:
        for h in range(rng.choice([1, 2])):
            hid = f"HTL-{city[:3]}-{h}"
            data["hotels"].append({"hotel_id": hid, "name": hid, "aliases": [], "city": city,
                                   "area": rng.choice(["A", "B"]), "stars": rng.randrange(2, 6)})
            for r in range(rng.choice([1, 2])):
                rid = f"{hid}-{r}"
                data["rooms"].append({"room_id": rid, "hotel_id": hid, "name": rid, "max_adults": rng.choice([1, 2]),
                                      "max_children": rng.choice([0, 1, 2]),
                                      "price_per_night": INR(rng.randrange(2000, 12000, 500))})
                for i in range(nights + 1):
                    if rng.random() > 0.04:
                        data["availability"].append({"room_id": rid, "date": (S + timedelta(days=i)).isoformat(),
                                                     "rooms_left": rng.choice([0, 1, 2, 3, 3, 4, 4])})
        for k in range(rng.choice([2, 3, 4])):
            win = rng.choice(WINDOWS[:3])
            rec = {"attraction_id": f"ATT-{city[:3]}-{k}", "name": "x", "city": city,
                   "open_days": sorted(rng.sample(DAYS, rng.randrange(4, 8)), key=DAYS.index),
                   "open_from": win[0], "open_until": win[1], "visit_minutes": rng.choice([60, 120, 240]),
                   "price_per_person": INR(rng.choice([0, 0, 500, 1200]))}
            if rng.random() < 0.4:
                rec["child_price"] = INR(rng.choice([0, 200]))
            if rng.random() < 0.3:
                rec["closed_dates"] = [(S + timedelta(days=rng.randrange(1, nights))).isoformat()]
            data["attractions"].append(rec)
    req = {"departure_city": "BLR", "destination_country": "Japan", "start_date": S.isoformat(), "nights": nights,
           "adults": rng.choice([1, 2]), "children_ages": rng.choice([[], [5], [0, 9]]), "budget_inr": 10 ** 8}
    return data, req


def _flight(fid, frm, to, dep, arr, dur, rng):
    return {"flight_id": fid, "airline": "R", "flight_number": fid, "from": frm, "to": to,
            "departure_local": dep.strftime("%Y-%m-%dT%H:%M:00"), "arrival_local": arr.strftime("%Y-%m-%dT%H:%M:00"),
            "stops": rng.choice([0, 1]), "duration_minutes": dur, "price_per_seat": INR(rng.randrange(8000, 30000, 1000)),
            "seats_left": rng.choice([1, 3, 4, 9, 9])}
