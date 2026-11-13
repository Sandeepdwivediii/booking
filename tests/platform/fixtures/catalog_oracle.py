"""Independent export ORACLE for SY-06/07/08/09/13/15 on the ORIGINAL catalog files.

Self-contained: reads raw data/catalog/*.json and re-derives every price, quantity, identifier,
availability and local/UTC timeline fact itself. Imports nothing from app.* (no domain, no contract).
Returns a list of (code, item_id, detail) findings; empty list = export is consistent with the catalog.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def _load(catalog_dir: Path, name: str) -> list[dict]:
    data = json.loads((Path(catalog_dir) / f"{name}.json").read_text(encoding="utf-8"))
    return data if isinstance(data, list) else next(v for v in data.values() if isinstance(v, list))


class RawCatalog:
    def __init__(self, catalog_dir: Path):
        self.flights = {r["flight_id"]: r for r in _load(catalog_dir, "flights")}
        self.hotels = {r["hotel_id"]: r for r in _load(catalog_dir, "hotels")}
        self.rooms = {r["room_id"]: r for r in _load(catalog_dir, "rooms")}
        self.availability = {(r["room_id"], r["date"]): r["rooms_left"] for r in _load(catalog_dir, "availability")}
        self.transfers = {r["transfer_id"]: r for r in _load(catalog_dir, "transfers")}
        self.attractions = {r["attraction_id"]: r for r in _load(catalog_dir, "attractions")}
        self.airports = {r["code"]: r for r in _load(catalog_dir, "airports")}
        dest = json.loads((Path(catalog_dir) / "destinations.json").read_text(encoding="utf-8"))
        self.country_tz = {c["name"]: c["timezone"] for c in dest["countries"]}
        self.home_city = {d["airport"]: d["city"] for d in dest["departure_cities"]}


def _amount(v) -> int:
    return v["amount"] if isinstance(v, dict) else v


def _minute(s: str) -> str:
    return s[:16]  # catalog "YYYY-MM-DDTHH:MM:SS" vs trip "YYYY-MM-DDTHH:MM"


def _utc(local: str, tz: str) -> datetime:
    return datetime.fromisoformat(_minute(local)).replace(tzinfo=ZoneInfo(tz)).astimezone(ZoneInfo("UTC"))


def check_export(trip: dict, cat: RawCatalog) -> list[tuple[str, str | None, str]]:
    out: list[tuple[str, str | None, str]] = []
    add = lambda code, iid, detail="": out.append((code, iid, detail))  # noqa: E731
    tr = trip["traveler"]
    adults, children = tr["adults"], len(tr["child_ages"])
    travelers = adults + children
    country_tz = cat.country_tz.get(trip["country"])
    if country_tz is None:
        return [("UNKNOWN_COUNTRY", None, trip["country"])]
    items = trip["items"]
    by_role: dict[str, list[dict]] = {}
    for i in items:
        by_role.setdefault(i["role"], []).append(i)

    # ---------------- SY-06 completeness
    if len(by_role.get("outbound_flight", [])) != 1 or len(by_role.get("return_flight", [])) != 1:
        add("FLIGHT_COUNT", None)
    if len(by_role.get("arrival_transfer", [])) != 1 or len(by_role.get("departure_transfer", [])) != 1:
        add("AIRPORT_TRANSFER_COUNT", None)
    hotels = sorted(by_role.get("hotel_stay", []), key=lambda i: i["start"])
    nights_covered = []
    for h in hotels:
        d0, d1 = date.fromisoformat(h["start"]), date.fromisoformat(h["end"])
        nights_covered += [d0 + timedelta(days=k) for k in range((d1 - d0).days)]
    start, end = date.fromisoformat(trip["start_date"]), date.fromisoformat(trip["end_date"])
    if nights_covered != [start + timedelta(days=k) for k in range((end - start).days)]:
        add("HOTEL_NIGHTS_NOT_EXACT", None)
    if len(by_role.get("intercity_transfer", [])) != max(len(hotels) - 1, 0):
        add("INTERCITY_COUNT", None)
    full_days = [start + timedelta(days=k) for k in range(1, (end - start).days)]
    change_days = {date.fromisoformat(h["start"]) for h in hotels[1:]}
    att_days = {date.fromisoformat(a["start"][:10]) for a in by_role.get("attraction_visit", [])}
    if any(d not in att_days for d in full_days if d not in change_days):
        add("FULL_DAY_WITHOUT_ATTRACTION", None)

    total = 0
    for i in items:
        iid, cid, typ = i["item_id"], i["catalog_id"], i["type"]
        total += i["price_inr"]
        if i["status"] != "PLANNED" or i["booking"] is not None or i["sources"] != []:
            add("DRAFT_ITEM_SHAPE", iid)  # SY-10 / SY-12
        if typ == "flight":
            f = cat.flights.get(cid)
            if f is None:
                add("UNKNOWN_FLIGHT", iid, cid); continue  # SY-13
            if i["quantity"] != travelers or i["price_inr"] != _amount(f["price_per_seat"]) * travelers:
                add("FLIGHT_PRICE_OR_QTY", iid)  # SY-08
            if f["seats_left"] < travelers:
                add("FLIGHT_SEATS", iid)
            if _minute(i["start"]) != _minute(f["departure_local"]) or _minute(i["end"]) != _minute(f["arrival_local"]):
                add("FLIGHT_TIME_NOT_FROM_CATALOG", iid)
            home = tr["home_airport"]
            if i["role"] == "outbound_flight" and (f["from"] != home or _minute(f["arrival_local"])[:10] != trip["start_date"]):
                add("OUTBOUND_ROUTE_OR_DATE", iid)
            if i["role"] == "return_flight" and (f["to"] != home or _minute(f["departure_local"])[:10] != trip["end_date"]
                                                 or i["city"] != cat.home_city[home]):
                add("RETURN_ROUTE_DATE_OR_CITY", iid)
            dep = _utc(f["departure_local"], cat.airports[f["from"]]["timezone"])
            arr = _utc(f["arrival_local"], cat.airports[f["to"]]["timezone"])
            if arr - dep != timedelta(minutes=f["duration_minutes"]):
                add("FLIGHT_DURATION_TZ", iid)  # SY-15 / FL-11
        elif typ == "hotel":
            h, r = cat.hotels.get(cid), cat.rooms.get(i["room_id"] or "")
            if h is None or r is None:
                add("UNKNOWN_HOTEL_OR_ROOM", iid, f"{cid}/{i['room_id']}"); continue  # SY-07 / SY-13
            if r["hotel_id"] != cid or h["city"] != i["city"]:
                add("ROOM_OR_CITY_MISMATCH", iid)
            k = i["quantity"]
            n = (date.fromisoformat(i["end"]) - date.fromisoformat(i["start"])).days
            if not (adults <= k * r["max_adults"] and travelers <= k * (r["max_adults"] + r["max_children"]) and adults >= k):
                add("ROOM_CAPACITY", iid)
            if i["price_inr"] != _amount(r["price_per_night"]) * k * n:
                add("HOTEL_PRICE", iid)
            for d in range(n):
                night = (date.fromisoformat(i["start"]) + timedelta(days=d)).isoformat()
                if cat.availability.get((r["room_id"], night), 0) < k:
                    add("HOTEL_AVAILABILITY", iid, night)
        elif typ == "transfer":
            t = cat.transfers.get(cid)
            if t is None:
                add("UNKNOWN_TRANSFER", iid, cid); continue
            if t["pricing"] == "PER_VEHICLE":
                qty = math.ceil(travelers / t["max_passengers"])
                price = _amount(t["price"]) * qty
            else:
                qty = travelers
                child = _amount(t["child_price"]) if t.get("child_price") else _amount(t["price"])
                price = adults * _amount(t["price"]) + children * child
            if i["quantity"] != qty or i["price_inr"] != price:
                add("TRANSFER_PRICE_OR_QTY", iid, f"{t['pricing']} qty {i['quantity']}!={qty} or price")  # SY-09
            if i["role"] == "intercity_transfer" and i["city"] != t.get("to_city"):
                add("INTERCITY_CITY", iid)
        elif typ == "attraction":
            a = cat.attractions.get(cid)
            if a is None:
                add("UNKNOWN_ATTRACTION", iid, cid); continue
            child = _amount(a["child_price"]) if a.get("child_price") else _amount(a["price_per_person"])
            if i["quantity"] != travelers or i["price_inr"] != adults * _amount(a["price_per_person"]) + children * child:
                add("ATTRACTION_PRICE_OR_QTY", iid)
    if total != trip["total_inr"]:
        add("TOTAL_MISMATCH", None, f"{total} != {trip['total_inr']}")

    # ---------------- SY-15 timeline in real (UTC) time
    def utc_of(i: dict, field: str) -> datetime | None:
        s = i.get(field)
        if not s or len(s) <= 10:
            return None
        if i["type"] == "flight":
            f = cat.flights[i["catalog_id"]]
            return _utc(s, cat.airports[f["from" if field == "start" else "to"]]["timezone"])
        return _utc(s, country_tz)

    timed = [(utc_of(i, "start"), i) for i in items if i["type"] != "hotel" and cat_has(cat, i)]
    for (t1, i1), (t2, i2) in zip(timed, timed[1:]):
        if t1 and t2 and t2 < t1:
            add("TIMELINE_ORDER", i2["item_id"], f"{i1['item_id']} at {t1} after {i2['item_id']} at {t2}")
    out_f, arr_t = by_role.get("outbound_flight", [None])[0], by_role.get("arrival_transfer", [None])[0]
    if out_f and arr_t and cat_has(cat, out_f) and utc_of(arr_t, "start") < utc_of(out_f, "end") + timedelta(minutes=60):
        add("PICKUP_TOO_SOON_AFTER_LANDING", arr_t["item_id"])
    ret_f, dep_t = by_role.get("return_flight", [None])[0], by_role.get("departure_transfer", [None])[0]
    if ret_f and dep_t and dep_t.get("end") and cat_has(cat, ret_f):
        if utc_of(dep_t, "end") > utc_of(ret_f, "start") - timedelta(minutes=180):
            add("AIRPORT_ARRIVAL_TOO_LATE", dep_t["item_id"])
    return out


def cat_has(cat: RawCatalog, i: dict) -> bool:
    return i["type"] != "flight" or i["catalog_id"] in cat.flights
