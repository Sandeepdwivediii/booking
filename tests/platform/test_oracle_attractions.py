"""AT-01..AT-17 on the independent raw-fixture attraction oracle (does not call Tanish's validator).
Planner integration (oracle run on real BUILT trips) is added once the domain branch is merged."""

from __future__ import annotations

import copy
import json

import pytest

from tests.platform.fixtures.attraction_oracle import (
    WEEKDAYS, check_attractions, load_catalog_attractions, normalize_attraction,
)
from tests.platform.fixtures.contract_fixtures import CATALOG_DIR

ALL_DAYS = list(WEEKDAYS)
# Raw records shaped like data/catalog/attractions.json (synthetic ids; TEST-ONLY).
RAW = [
    {"attraction_id": "T-MUSEUM", "city": "Tokyo", "open_days": ALL_DAYS, "open_from": "09:00", "open_until": "17:00",
     "visit_minutes": 90, "closed_dates": [], "child_friendly": True},
    {"attraction_id": "T-TOWER", "city": "Tokyo", "open_days": ALL_DAYS, "open_from": "09:00", "open_until": "22:00",
     "visit_minutes": 60, "child_friendly": True},                                   # closed_dates absent
    {"attraction_id": "T-GARDEN", "city": "Tokyo", "open_days": ALL_DAYS, "open_from": "09:00", "open_until": "18:00",
     "visit_minutes": 60, "closed_dates": None, "child_friendly": False},            # closed_dates null; AT-17
    {"attraction_id": "T-WEEKEND", "city": "Tokyo", "open_days": ["SAT", "SUN"], "open_from": "09:00",
     "open_until": "17:00", "visit_minutes": 60, "closed_dates": []},
    {"attraction_id": "T-CLOSED12", "city": "Tokyo", "open_days": ALL_DAYS, "open_from": "09:00", "open_until": "17:00",
     "visit_minutes": 60, "closed_dates": ["2027-04-12"]},
    {"attraction_id": "T-LATE", "city": "Tokyo", "open_days": ALL_DAYS, "open_from": "10:00", "open_until": "24:00",
     "visit_minutes": 60, "closed_dates": []},
    {"attraction_id": "K-TEMPLE", "city": "Kyoto", "open_days": ALL_DAYS, "open_from": "06:00", "open_until": "18:00",
     "visit_minutes": 90, "closed_dates": []},
    {"attraction_id": "K-SHRINE", "city": "Kyoto", "open_days": ALL_DAYS, "open_from": "06:00", "open_until": "18:00",
     "visit_minutes": 60, "closed_dates": []},
    {"attraction_id": "K-CASTLE", "city": "Kyoto", "open_days": ALL_DAYS, "open_from": "09:00", "open_until": "17:00",
     "visit_minutes": 60, "closed_dates": []},
    {"attraction_id": "N-DEER", "city": "Nara", "open_days": ALL_DAYS, "open_from": "00:00", "open_until": "24:00",
     "visit_minutes": 60, "closed_dates": []},
]
CAT = {r["attraction_id"]: normalize_attraction(r) for r in RAW}


def item(iid, role, typ, city, cid, start, end, room=None):
    return {"item_id": iid, "type": typ, "role": role, "name": cid, "city": city, "status": "PLANNED",
            "catalog_id": cid, "room_id": room, "start": start, "end": end, "quantity": 3, "price_inr": 0,
            "booking": None, "sources": [], "warnings": []}


def att(iid, cid, city, start, minutes):
    h, m = map(int, start[11:].split(":"))
    e = h * 60 + m + minutes
    return item(iid, "attraction_visit", "attraction", city, cid, start, f"{start[:10]}T{e // 60:02d}:{e % 60:02d}")


def base_trip():
    """Tokyo 10-13 Apr, Kyoto 13-16 Apr. Arrival 10 (in city 12:45), change 13 (in Kyoto 12:15), return 16.
    Full days: 11, 12, 14, 15 — one attraction each."""
    return {
        "trip_id": "t", "country": "Japan", "start_date": "2027-04-10", "end_date": "2027-04-16",
        "budget_inr": 1, "total_inr": 0,
        "traveler": {"name": None, "home_airport": "BLR", "adults": 2, "children": 1, "child_ages": [6]},
        "items": [
            item("f1", "outbound_flight", "flight", "Tokyo", "FL", "2027-04-09T23:50", "2027-04-10T10:30"),
            item("t1", "arrival_transfer", "transfer", "Tokyo", "TR", "2027-04-10T11:30", "2027-04-10T12:45"),
            item("h1", "hotel_stay", "hotel", "Tokyo", "H1", "2027-04-10", "2027-04-13", room="R1"),
            att("a1", "T-MUSEUM", "Tokyo", "2027-04-11T10:00", 90),
            att("a2", "T-TOWER", "Tokyo", "2027-04-12T10:00", 60),
            item("t2", "intercity_transfer", "transfer", "Kyoto", "TR2", "2027-04-13T10:00", "2027-04-13T12:15"),
            item("h2", "hotel_stay", "hotel", "Kyoto", "H2", "2027-04-13", "2027-04-16", room="R2"),
            att("a3", "K-TEMPLE", "Kyoto", "2027-04-14T09:00", 90),
            att("a4", "K-SHRINE", "Kyoto", "2027-04-15T09:00", 60),
            item("t3", "departure_transfer", "transfer", "Kyoto", "TR3", "2027-04-16T08:00", "2027-04-16T09:30"),
            item("f2", "return_flight", "flight", "Bengaluru", "FL2", "2027-04-16T13:00", "2027-04-16T19:00"),
        ],
    }


def with_items(*extra, drop=()):
    t = base_trip()
    t["items"] = [i for i in t["items"] if i["item_id"] not in drop] + list(extra)
    return t


def codes(trip, mode="initial"):
    return {c for c, _ in check_attractions(trip, CAT, mode)}


def test_base_trip_valid_in_all_modes():
    for mode in ("initial", "modified", "confirm"):
        assert check_attractions(base_trip(), CAT, mode) == []


def test_AT_01_full_day_without_attraction_rejected_initially():
    assert "FULL_DAY_WITHOUT_ATTRACTION" in codes(with_items(drop=("a1",)))


def test_AT_02_three_on_a_full_day_rejected():
    t = with_items(att("x1", "T-GARDEN", "Tokyo", "2027-04-11T12:00", 60), att("x2", "T-LATE", "Tokyo", "2027-04-11T14:00", 60))
    assert "TOO_MANY_ATTRACTIONS" in codes(t)
    assert "TOO_MANY_ATTRACTIONS" in codes(t, "modified")  # max 2 in every mode


def test_AT_03_arrival_and_change_days_may_be_empty():
    assert codes(base_trip()) == set()  # base trip has none on 10 and 13


def test_AT_04_return_day_attraction_rejected():
    assert "RETURN_DAY_ATTRACTION" in codes(with_items(att("x", "K-CASTLE", "Kyoto", "2027-04-16T09:00", 60)))


def test_AT_05_nara_during_kyoto_night_rejected():
    assert "WRONG_CITY" in codes(with_items(att("x", "N-DEER", "Kyoto", "2027-04-14T13:00", 60)))
    assert "WRONG_CITY" in codes(with_items(att("x", "N-DEER", "Nara", "2027-04-14T13:00", 60)))


def test_AT_05_tokyo_attraction_on_kyoto_night_rejected():
    assert "WRONG_CITY" in codes(with_items(att("x", "T-GARDEN", "Kyoto", "2027-04-14T13:00", 60)))


def test_AT_06_closed_date_overrides_weekly_schedule():
    assert "CLOSED_DATE" in codes(with_items(att("x", "T-CLOSED12", "Tokyo", "2027-04-12T13:00", 60)))
    assert codes(with_items(att("x", "T-CLOSED12", "Tokyo", "2027-04-11T13:00", 60))) == set()


def test_AT_07_wrong_weekday_rejected():
    assert WEEKDAYS[__import__("datetime").date(2027, 4, 12).weekday()] == "MON"
    assert "CLOSED_WEEKDAY" in codes(with_items(att("x", "T-WEEKEND", "Tokyo", "2027-04-12T13:00", 60)))
    assert codes(with_items(att("x", "T-WEEKEND", "Tokyo", "2027-04-11T13:00", 60))) == set()  # Sunday


def test_AT_08_start_before_opening_rejected():
    assert "BEFORE_OPENING" in codes(with_items(att("x", "T-GARDEN", "Tokyo", "2027-04-11T08:59", 60)))


def test_AT_09_visit_ending_exactly_at_close_allowed():
    t = with_items(att("x", "T-MUSEUM", "Tokyo", "2027-04-11T15:30", 90), drop=("a1",))  # 90 min, closes 17:00
    assert codes(t) == set()


def test_AT_10_visit_ending_after_close_rejected():
    assert codes(with_items(att("x", "T-MUSEUM", "Tokyo", "2027-04-11T15:31", 90), drop=("a1",))) == {"AFTER_CLOSING"}


def test_AT_11_overlap_rejected():
    t = with_items(att("x1", "T-GARDEN", "Tokyo", "2027-04-12T13:00", 60), drop=("a2",))
    t["items"].append(att("x2", "T-LATE", "Tokyo", "2027-04-12T13:59", 60))
    assert "OVERLAP" in codes(t)


def test_AT_12_adjacent_visits_allowed():
    t = with_items(att("x1", "T-GARDEN", "Tokyo", "2027-04-12T13:00", 60), drop=("a2",))
    t["items"].append(att("x2", "T-LATE", "Tokyo", "2027-04-12T14:00", 60))
    assert codes(t) == set()


def test_AT_13_same_attraction_twice_rejected():
    assert "DUPLICATE_ATTRACTION" in codes(with_items(att("x", "T-MUSEUM", "Tokyo", "2027-04-12T13:00", 90)))


@pytest.mark.parametrize("day, start, city, cid", [
    ("2027-04-10", "12:44", "Tokyo", "T-GARDEN"),  # arrival day: in city at 12:45
    ("2027-04-13", "12:14", "Kyoto", "K-CASTLE"),  # change day: in Kyoto at 12:15
])
def test_AT_14_before_city_arrival_rejected(day, start, city, cid):
    assert "BEFORE_CITY_ARRIVAL" in codes(with_items(att("x", cid, city, f"{day}T{start}", 60)))
    late = int(start[3:]) + 1
    assert codes(with_items(att("x", cid, city, f"{day}T{start[:3]}{late:02d}", 60))) == set()


def test_AT_15_removing_sole_full_day_attraction_ok_after_edit():
    t = with_items(drop=("a1",))
    assert codes(t, "modified") == set() and codes(t, "confirm") == set()
    assert "FULL_DAY_WITHOUT_ATTRACTION" in codes(t, "initial")


@pytest.mark.parametrize("closed", ["absent", None, []])
def test_AT_16_closed_dates_absent_or_null_normalized(closed):
    raw = {k: v for k, v in RAW[0].items() if k != "closed_dates"}
    if closed != "absent":
        raw["closed_dates"] = closed
    assert normalize_attraction(raw)["closed_dates"] == set()


def test_AT_16_real_catalog_normalizes_without_crash():
    cat = load_catalog_attractions(CATALOG_DIR)
    raw = json.loads((CATALOG_DIR / "attractions.json").read_text(encoding="utf-8"))
    records = raw["attractions"] if isinstance(raw, dict) else raw
    assert len(cat) == len(records) == 56
    assert any("closed_dates" not in r for r in records)  # the real data really omits it sometimes
    assert {a["city"] for a in cat.values()} >= {"Nara"}


def test_AT_17_child_friendly_false_is_not_a_hard_rule():
    t = with_items(att("x", "T-GARDEN", "Tokyo", "2027-04-12T13:00", 60))
    assert CAT["T-GARDEN"]["child_friendly"] is False and t["traveler"]["children"] == 1
    assert codes(t) == set()


def test_oracle_does_not_import_domain_validator():
    import ast

    import tests.platform.fixtures.attraction_oracle as oracle
    tree = ast.parse(open(oracle.__file__, encoding="utf-8").read())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m and m.startswith("app") for m in imported), imported


def test_unknown_attraction_id_flagged():
    t = base_trip()
    t["items"][3] = copy.deepcopy(t["items"][3]) | {"catalog_id": "NOT-IN-CATALOG"}
    assert "UNKNOWN_ATTRACTION" in codes(t)
