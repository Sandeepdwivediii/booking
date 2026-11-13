"""Independent attraction-rule ORACLE (problem section 4, "Attractions").

Deliberately self-contained: works on raw attractions.json-shaped records and judge-format trips.
It does NOT import Tanish's validator or app.contracts helpers, so it can cross-check the planner.
Returns a list of (code, item_id) findings; empty list = all attraction rules hold.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)  # "24:00" -> 1440 (end of day)


def normalize_attraction(raw: dict) -> dict:
    """Raw catalog record -> normalized; optional closed_dates absent/null -> [] (AT-16)."""
    return {
        "id": raw["attraction_id"],
        "city": raw["city"],
        "open_days": set(raw.get("open_days") or []),
        "open_from": _minutes(raw["open_from"]),
        "open_until": _minutes(raw["open_until"]),
        "closed_dates": set(raw.get("closed_dates") or []),
        "visit_minutes": int(raw["visit_minutes"]),
        "child_friendly": raw.get("child_friendly"),  # informational only (AT-17)
    }


def load_catalog_attractions(catalog_dir: Path) -> dict[str, dict]:
    raw = json.loads((Path(catalog_dir) / "attractions.json").read_text(encoding="utf-8"))
    records = raw["attractions"] if isinstance(raw, dict) else raw
    return {r["attraction_id"]: normalize_attraction(r) for r in records}


def _dt(s: str) -> tuple[date, int]:
    d = datetime.fromisoformat(s)
    return d.date(), d.hour * 60 + d.minute


def check_attractions(trip: dict, attractions: dict[str, dict], mode: str) -> list[tuple[str, str | None]]:
    findings: list[tuple[str, str | None]] = []
    start, end = date.fromisoformat(trip["start_date"]), date.fromisoformat(trip["end_date"])
    hotels = sorted((i for i in trip["items"] if i["type"] == "hotel"), key=lambda i: i["start"])
    first_hotel_start = date.fromisoformat(hotels[0]["start"][:10]) if hotels else None

    def night_city(d: date) -> str | None:
        for h in hotels:
            if date.fromisoformat(h["start"][:10]) <= d < date.fromisoformat(h["end"][:10]):
                return h["city"]
        return None

    # When the traveler reaches the city on arrival / change days (end of the inbound leg).
    arrival_at: dict[date, int] = {}
    for i in trip["items"]:
        if i["role"] in ("arrival_transfer", "intercity_transfer") and i.get("end"):
            d, m = _dt(i["end"])
            arrival_at[d] = max(arrival_at.get(d, 0), m)
    change_days = {date.fromisoformat(h["start"][:10]) for h in hotels} - {first_hotel_start}

    visits: dict[date, list[tuple[int, int, str]]] = {}
    seen: set[str] = set()
    for item in (i for i in trip["items"] if i["type"] == "attraction"):
        iid, cid = item["item_id"], item["catalog_id"]
        rec = attractions.get(cid)
        if rec is None:
            findings.append(("UNKNOWN_ATTRACTION", iid))
            continue
        if cid in seen:
            findings.append(("DUPLICATE_ATTRACTION", iid))  # AT-13
        seen.add(cid)
        d, s = _dt(item["start"])
        e = s + rec["visit_minutes"]
        if not start <= d <= end:
            findings.append(("OUTSIDE_TRIP", iid))
            continue
        if d == end:
            findings.append(("RETURN_DAY_ATTRACTION", iid))  # AT-04
        city = night_city(d)
        if city is None or rec["city"] != city or item["city"] != city:
            findings.append(("WRONG_CITY", iid))  # AT-05 (incl. Nara during a Kyoto night)
        if WEEKDAYS[d.weekday()] not in rec["open_days"]:
            findings.append(("CLOSED_WEEKDAY", iid))  # AT-07
        if d.isoformat() in rec["closed_dates"]:
            findings.append(("CLOSED_DATE", iid))  # AT-06: explicit closure overrides weekly schedule
        if s < rec["open_from"]:
            findings.append(("BEFORE_OPENING", iid))  # AT-08
        if e > rec["open_until"]:
            findings.append(("AFTER_CLOSING", iid))  # AT-10 (AT-09 boundary allowed)
        if item.get("end"):
            ed, em = _dt(item["end"])
            if (ed, em) != (d, e) and not (e == 1440 and em == 0):
                findings.append(("END_MISMATCH", iid))
        if (d == start or d in change_days) and s < arrival_at.get(d, 0):
            findings.append(("BEFORE_CITY_ARRIVAL", iid))  # AT-14
        visits.setdefault(d, []).append((s, e, iid))

    for d, vs in visits.items():
        vs.sort()
        for (s1, e1, _), (s2, _e2, iid2) in zip(vs, vs[1:]):
            if s2 < e1:
                findings.append(("OVERLAP", iid2))  # AT-11; adjacent (s2 == e1) allowed, AT-12

    d = start
    while d < end:
        n = len(visits.get(d, []))
        if n > 2:
            findings.append(("TOO_MANY_ATTRACTIONS", None))  # AT-02 (all modes)
        is_full_day = d != start and d not in change_days
        if mode == "initial" and is_full_day and n < 1:
            findings.append(("FULL_DAY_WITHOUT_ATTRACTION", None))  # AT-01; AT-15: not in modified/confirm
        d = date.fromordinal(d.toordinal() + 1)
    return findings
