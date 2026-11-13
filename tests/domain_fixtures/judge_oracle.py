"""TEST-ONLY independent judge oracle: problem §4 rules + prices from the RAW catalog JSON.

No imports from app.* — own JSON loading, own local-time arithmetic, own price formulas — so it can
audit the domain planner, validator, edits and booked exports independently.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


class Raw:
    def __init__(self, root: Path):
        load = lambda n: json.loads((root / f"{n}.json").read_text(encoding="utf-8"))  # noqa: E731
        self.dest = load("destinations")
        by = lambda n, k: {r[k]: r for r in load(n)}  # noqa: E731
        self.airports, self.flights = by("airports", "code"), by("flights", "flight_id")
        self.hotels, self.rooms = by("hotels", "hotel_id"), by("rooms", "room_id")
        self.transfers, self.attractions = by("transfers", "transfer_id"), by("attractions", "attraction_id")
        self.avail = {(a["room_id"], a["date"]): a["rooms_left"] for a in load("availability")}
        self.country = {c["name"]: c for c in self.dest["countries"]}


# ------------------------------------------------------------------ time (local, minutes)

def mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def dt(s: str) -> datetime:
    return datetime.strptime(s[:16], "%Y-%m-%dT%H:%M")


def windows(t: datetime, start: str, end: str):
    for back in (1, 0):
        d0 = datetime(t.year, t.month, t.day) - timedelta(days=back)
        o, c = d0 + timedelta(minutes=mins(start)), d0 + timedelta(minutes=mins(end))
        if mins(end) <= mins(start):
            c += timedelta(days=1)
        yield o, c


def legal_departure(t: datetime, rec) -> bool:
    for o, c in windows(t, rec["service_from"], rec["service_until"]):
        if o <= t <= c and (not rec.get("frequency_minutes")
                            or ((t - o).total_seconds() / 60) % rec["frequency_minutes"] == 0):
            return True
    return False


# ------------------------------------------------------------------ prices

def amt(m):
    return m["amount"]


def transfer_cost(t, A, C):
    if t["pricing"] == "PER_VEHICLE":
        return amt(t["price"]) * -(-(A + C) // t["max_passengers"])
    return A * amt(t["price"]) + C * amt(t.get("child_price") or t["price"])


def transfer_qty(t, T):
    return -(-T // t["max_passengers"]) if t["pricing"] == "PER_VEHICLE" else T


def attraction_cost(a, A, C):
    return A * amt(a["price_per_person"]) + C * amt(a.get("child_price") or a["price_per_person"])


def visit_ok(a, start: datetime) -> bool:
    d = start.date()
    if DAYS[d.weekday()] not in a["open_days"] or d.isoformat() in (a.get("closed_dates") or []):
        return False
    o = datetime(d.year, d.month, d.day) + timedelta(minutes=mins(a["open_from"]))
    c = datetime(d.year, d.month, d.day) + timedelta(minutes=mins(a["open_until"]))
    if mins(a["open_until"]) <= mins(a["open_from"]):
        c += timedelta(days=1)
    return o <= start and start + timedelta(minutes=a["visit_minutes"]) <= c


# ------------------------------------------------------------------ judge

def judge(raw: Raw, trip: dict, initial: bool) -> list[str]:
    """§4 violations of a judge-format trip (empty list = correct)."""
    e: list[str] = []
    tv = trip["traveler"]
    A, ages = tv["adults"], tv["child_ages"]
    C, T, home = len(ages), tv["adults"] + len(ages), tv["home_airport"]
    S, E = date.fromisoformat(trip["start_date"]), date.fromisoformat(trip["end_date"])
    rules = raw.dest["rules"]
    country = raw.country.get(trip["country"])
    if country is None:
        return ["unknown country"]
    cities = {c["name"] for c in country["cities"]}
    if not rules["min_nights"] <= (E - S).days <= rules["max_nights"]:
        e.append("nights out of range")
    if S.isoformat() < rules["data_window"]["first_night"] or E.isoformat() > rules["data_window"]["last_departure"]:
        e.append("outside data window")
    if tv["children"] != C or any(not 0 <= x <= 17 for x in ages) or A < 1:
        e.append("bad traveler")
    role = lambda r: [i for i in trip["items"] if i.get("role") == r]  # noqa: E731
    total = 0
    for i in trip["items"]:  # every price from the catalog
        k = i["type"]
        if k == "flight":
            f = raw.flights[i["catalog_id"]]
            want = (T, amt(f["price_per_seat"]) * T)
        elif k == "hotel":
            r = raw.rooms[i["room_id"]]
            n = (date.fromisoformat(i["end"]) - date.fromisoformat(i["start"])).days
            want = (i["quantity"], amt(r["price_per_night"]) * i["quantity"] * n)
        elif k == "transfer":
            t = raw.transfers[i["catalog_id"]]
            want = (transfer_qty(t, T), transfer_cost(t, A, C))
        else:
            want = (T, attraction_cost(raw.attractions[i["catalog_id"]], A, C))
        if (i["quantity"], i["price_inr"]) != want:
            e.append(f"{i['item_id']}: quantity/price {(i['quantity'], i['price_inr'])} != {want}")
        if i.get("sources") != [] or i["status"] not in ("PLANNED", "BOOKED"):
            e.append(f"{i['item_id']}: sources/status")
        total += i["price_inr"]
    if total != trip["total_inr"]:
        e.append("total != sum of items")
    if trip["total_inr"] > trip["budget_inr"]:
        e.append("over budget")
    # hotels -> city plan
    hotels = sorted(role("hotel_stay"), key=lambda i: i["start"])
    if not hotels:
        return e + ["no hotel"]
    expect, seen, segs = S, set(), []
    for h in hotels:
        hotel, room = raw.hotels[h["catalog_id"]], raw.rooms[h["room_id"]]
        ci, co, k = date.fromisoformat(h["start"]), date.fromisoformat(h["end"]), h["quantity"]
        city = hotel["city"]
        if ci != expect:
            e.append(f"hotel gap/overlap at {ci}")
        expect = co
        if room["hotel_id"] != hotel["hotel_id"] or city not in cities or city in seen or h["city"] != city:
            e.append(f"{h['item_id']}: room/hotel/city mismatch or repeated city")
        seen.add(city)
        if (co - ci).days < rules["min_nights_per_city"]:
            e.append(f"{city}: fewer than min nights")
        if not (A <= k * room["max_adults"] and T <= k * (room["max_adults"] + room["max_children"]) and A >= k):
            e.append(f"{h['item_id']}: {k} rooms do not fit")
        for n in range((co - ci).days):
            night = (ci + timedelta(days=n)).isoformat()
            if raw.avail.get((room["room_id"], night), -1) < k:
                e.append(f"{h['item_id']}: no stock {night}")
        segs.append((city, ci, co, hotel))
    if expect != E:
        e.append("last check-out is not the return day")
    links = {(l["from_city"], l["to_city"]): l["transfer_ids"] for l in country["links"]}
    # flights
    out, ret = role("outbound_flight"), role("return_flight")
    if len(out) != 1 or len(ret) != 1:
        return e + ["need exactly one arrival and one return flight"]
    fo, fr = raw.flights[out[0]["catalog_id"]], raw.flights[ret[0]["catalog_id"]]
    if fo["from"] != home or fo["arrival_local"][:10] != S.isoformat() or segs[0][0] not in (
            raw.airports[fo["to"]].get("serves_cities") or []) or fo["seats_left"] < T:
        e.append("bad arrival flight")
    if fr["to"] != home or fr["departure_local"][:10] != E.isoformat() or segs[-1][0] not in (
            raw.airports[fr["from"]].get("serves_cities") or []) or fr["seats_left"] < T:
        e.append("bad return flight")
    for it, f in ((out[0], fo), (ret[0], fr)):
        if (it["start"], it["end"]) != (f["departure_local"][:16], f["arrival_local"][:16]):
            e.append("flight times differ from catalog")
    # airport transfers
    entry, leave = [None] * len(segs), [None] * len(segs)
    for r_, arrival in (("arrival_transfer", True), ("departure_transfer", False)):
        items = role(r_)
        if len(items) != 1:
            e.append(f"need one {r_}")
            continue
        t = raw.transfers[items[0]["catalog_id"]]
        city, _, _, hotel = segs[0] if arrival else segs[-1]
        f = fo if arrival else fr
        p = dt(items[0]["start"])
        if t.get("airport") != (f["to"] if arrival else f["from"]) or t.get("city") != city or (
                t.get("area") not in (None, hotel["area"])):
            e.append(f"{r_}: wrong airport/city/area")
        if not legal_departure(p, t):
            e.append(f"{r_}: pickup outside service")
        if arrival:
            if p < dt(f["arrival_local"]) + timedelta(minutes=60):
                e.append("pickup < landing + 60")
            entry[0] = p + timedelta(minutes=t["duration_minutes"])
        else:
            if p + timedelta(minutes=t["duration_minutes"]) > dt(f["departure_local"]) - timedelta(minutes=180):
                e.append("airport reached < 180 min before departure")
            leave[-1] = p
    # intercity
    inter = role("intercity_transfer")
    for i in range(1, len(segs)):
        day = segs[i][1]
        todays = [x for x in inter if x["start"][:10] == day.isoformat()]
        if len(todays) != 1 or todays[0]["catalog_id"] not in links.get((segs[i - 1][0], segs[i][0]), []):
            e.append(f"bad intercity on {day}")
            continue
        t, p = raw.transfers[todays[0]["catalog_id"]], dt(todays[0]["start"])
        if not legal_departure(p, t):
            e.append("intercity not a legal departure")
        leave[i - 1], entry[i] = p, p + timedelta(minutes=t["duration_minutes"])
    if len(inter) != len(segs) - 1:
        e.append("intercity count")
    # attractions
    ids, per_day = set(), {}
    for v in role("attraction_visit"):
        a, s = raw.attractions[v["catalog_id"]], dt(v["start"])
        d = s.date()
        seg = next((j for j, x in enumerate(segs) if x[1] <= d < x[2]), None)
        if seg is None or segs[seg][0] != a["city"]:
            e.append(f"{v['item_id']}: not in that night's hotel city")
            continue
        if not visit_ok(a, s) or v["end"] != (s + timedelta(minutes=a["visit_minutes"])).strftime("%Y-%m-%dT%H:%M"):
            e.append(f"{v['item_id']}: closed / outside hours / wrong end")
        if (entry[seg] and s < entry[seg]) or (leave[seg] and s + timedelta(minutes=a["visit_minutes"]) > leave[seg]):
            e.append(f"{v['item_id']}: before reaching / after leaving the city")
        if a["attraction_id"] in ids:
            e.append("duplicate attraction")
        ids.add(a["attraction_id"])
        per_day.setdefault(d, []).append((s, s + timedelta(minutes=a["visit_minutes"])))
    change_days = {x[1] for x in segs[1:]}
    d = S
    while d < E:
        visits = sorted(per_day.get(d, []))
        if any(b[0] < a[1] for a, b in zip(visits, visits[1:])):
            e.append(f"overlap on {d}")
        if len(visits) > 2:
            e.append(f"more than 2 attractions on {d}")
        if initial and d != S and d not in change_days and not visits:
            e.append(f"full day {d} without attraction")
        d += timedelta(days=1)
    return e
