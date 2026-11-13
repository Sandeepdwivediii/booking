"""Phase 7: final domain verification against the ORIGINAL official catalog with independent oracles.

- judge_oracle (raw JSON, own time/price code) audits every BUILT trip, every edit produced from every
  listed alternative, and booked exports (booked_oracle).
- An independent brute force on the official catalog checks the exact cheapest total (M8) for 3-night trips.
- NO_PACKAGE flight reasons are re-derived from raw flights.
"""

from __future__ import annotations

import copy
import itertools
from datetime import date, datetime, timedelta

import pytest

from app.contracts import alternatives_shape_errors, build_result_shape_errors, change_result_shape_errors
from app.domain.adapter import Domain
from app.domain.catalog import load_catalog
from tests.domain_fixtures.booked_oracle import booked_export_problems, stamp_booked
from tests.domain_fixtures.catalog_builder import OFFICIAL_CATALOG_DIR
from tests.domain_fixtures.judge_oracle import (
    Raw, attraction_cost, dt, judge, legal_departure, transfer_cost, visit_ok,
)

HOMES = ["BLR", "DEL", "BOM", "MAA"]
COUNTRIES = ["Japan", "United Arab Emirates"]
PARTIES = [(2, [6]), (1, []), (4, [0, 17, 9])]
STARTS = ["2027-04-01", "2027-04-05", "2027-04-09", "2027-04-13", "2027-04-17"]
NIGHTS = [3, 4, 6, 10]


@pytest.fixture(scope="module")
def raw():
    return Raw(OFFICIAL_CATALOG_DIR)


@pytest.fixture(scope="module")
def domain():
    return Domain(load_catalog(OFFICIAL_CATALOG_DIR))


def req(home, country, start, nights, adults=2, kids=(6,), budget=10 ** 8):
    return {"departure_city": home, "destination_country": country, "start_date": start, "nights": nights,
            "adults": adults, "children_ages": list(kids), "budget_inr": budget}


def no_flight(raw, r, outbound: bool) -> bool:
    """Independent re-derivation of NO_OUTBOUND_FLIGHT / NO_RETURN_FLIGHT from raw flights."""
    T = r["adults"] + len(r["children_ages"])
    cities = {c["name"] for c in raw.country[r["destination_country"]]["cities"]}
    end = (date.fromisoformat(r["start_date"]) + timedelta(days=r["nights"])).isoformat()
    for f in raw.flights.values():
        ap = f["to"] if outbound else f["from"]
        home_ok = f["from"] == r["departure_city"] if outbound else f["to"] == r["departure_city"]
        day_ok = f["arrival_local"][:10] == r["start_date"] if outbound else f["departure_local"][:10] == end
        if home_ok and day_ok and f["seats_left"] >= T and set(raw.airports[ap].get("serves_cities") or []) & cities:
            return False
    return True


# ------------------------------------------------------------------ SY-17 sweep audited by the judge oracle

@pytest.mark.parametrize("home", HOMES)
@pytest.mark.parametrize("country", COUNTRIES)
def test_official_sweep_judged_independently(domain, raw, home, country):
    seen = {"BUILT": 0, "NO_PACKAGE": 0}
    for start, nights, (adults, kids) in itertools.product(STARTS, NIGHTS, PARTIES):
        if date.fromisoformat(start) + timedelta(days=nights) > date(2027, 4, 20):
            continue
        r = req(home, country, start, nights, adults, kids)
        res = domain.build(r, "sweep")
        assert build_result_shape_errors(res, r, "sweep") == []
        seen[res["status"]] = seen.get(res["status"], 0) + 1
        if res["status"] == "BUILT":
            assert judge(raw, res["trip"], initial=True) == [], (r, judge(raw, res["trip"], True))
        elif res["reason_code"] == "NO_OUTBOUND_FLIGHT":
            assert no_flight(raw, r, outbound=True), r  # re-derived from raw flights
    assert seen["BUILT"] > 0  # every origin/country pair yields real packages somewhere in April


# ------------------------------------------------------------------ independent exact minimum (M8)

def _first_pickup(t, earliest):
    for i in range(36 * 60):
        p = earliest + timedelta(minutes=i)
        if legal_departure(p, t):
            return p
    return None


def _last_pickup(t, latest):
    for i in range(36 * 60):
        p = latest - timedelta(minutes=i)
        if legal_departure(p, t):
            return p
    return None


def brute_force_3_nights(raw, r):
    """Cheapest legal 3-night single-city package by exhaustive search on raw data (or None)."""
    A, C = r["adults"], len(r["children_ages"])
    T, S = A + C, date.fromisoformat(r["start_date"])
    E = S + timedelta(days=3)
    full_days = [S + timedelta(days=1), S + timedelta(days=2)]
    best = None
    for city in [c["name"] for c in raw.country[r["destination_country"]]["cities"]]:
        serves = lambda ap: city in (raw.airports[ap].get("serves_cities") or [])  # noqa: E731
        outs, rets, hotels = [], [], []
        for f in raw.flights.values():
            if f["seats_left"] < T:
                continue
            if f["from"] == r["departure_city"] and f["arrival_local"][:10] == S.isoformat() and serves(f["to"]):
                for t in raw.transfers.values():
                    if t.get("airport") == f["to"] and t.get("city") == city:
                        p = _first_pickup(t, dt(f["arrival_local"]) + timedelta(minutes=60))
                        if p:
                            outs.append((amt_seat(f) * T + transfer_cost(t, A, C), t,
                                         p + timedelta(minutes=t["duration_minutes"])))
            if f["to"] == r["departure_city"] and f["departure_local"][:10] == E.isoformat() and serves(f["from"]):
                for t in raw.transfers.values():
                    if t.get("airport") == f["from"] and t.get("city") == city:
                        p = _last_pickup(t, dt(f["departure_local"]) - timedelta(minutes=180 + t["duration_minutes"]))
                        if p:
                            rets.append((amt_seat(f) * T + transfer_cost(t, A, C), t, p))
        for h in raw.hotels.values():
            if h["city"] != city:
                continue
            for rm in (x for x in raw.rooms.values() if x["hotel_id"] == h["hotel_id"]):
                for k in range(1, A + 1):
                    fits = A <= k * rm["max_adults"] and T <= k * (rm["max_adults"] + rm["max_children"])
                    nights = [(S + timedelta(days=i)).isoformat() for i in range(3)]
                    if fits and all(raw.avail.get((rm["room_id"], n), -1) >= k for n in nights):
                        hotels.append((rm["price_per_night"]["amount"] * k * 3, h["area"]))
        pool = [a for a in raw.attractions.values() if a["city"] == city]
        memo = {}

        def attractions(enter, leave):
            if (enter, leave) not in memo:
                def slot(a, d):
                    for i in range(0, 24 * 60, 5):
                        s = datetime(d.year, d.month, d.day) + timedelta(minutes=i)
                        if s >= enter and s + timedelta(minutes=a["visit_minutes"]) <= leave and visit_ok(a, s):
                            return True
                    return False
                costs = [attraction_cost(a1, A, C) + attraction_cost(a2, A, C)
                         for a1, a2 in itertools.permutations(pool, 2)
                         if slot(a1, full_days[0]) and slot(a2, full_days[1])]
                memo[(enter, leave)] = min(costs) if costs else None
            return memo[(enter, leave)]

        combos = sorted(((o[0] + rt[0] + hc, o, rt) for o in outs for rt in rets for hc, area in hotels
                        if o[1].get("area") in (None, area) and rt[1].get("area") in (None, area) and rt[2] >= o[2]),
                        key=lambda c: c[0])
        for base, o, rt in combos:
            if best is not None and base >= best:
                break  # attraction costs are >= 0: nothing later can be cheaper
            att = attractions(o[2], rt[2])
            if att is not None and (best is None or base + att < best):
                best = base + att
    return best


def amt_seat(f):
    return f["price_per_seat"]["amount"]


@pytest.mark.parametrize("r", [
    req("BLR", "Japan", "2027-04-02", 3), req("DEL", "United Arab Emirates", "2027-04-05", 3),
    req("BOM", "Japan", "2027-04-12", 3, 1, ()), req("MAA", "United Arab Emirates", "2027-04-15", 3, 4, (0, 17, 9)),
    req("BLR", "United Arab Emirates", "2027-04-09", 3), req("DEL", "Japan", "2027-04-16", 3, 3, (5,)),
], ids=lambda r: f"{r['departure_city']}-{r['destination_country'][:3]}-{r['start_date'][5:]}")
def test_official_cheapest_matches_independent_brute_force(domain, raw, r):
    expected = brute_force_3_nights(raw, r)
    res = domain.build(r, "bf")
    if expected is None:
        assert res["status"] == "NO_PACKAGE"
    else:
        assert res["status"] == "BUILT" and res["cheapest_total_inr"] == expected
        tight = domain.build(dict(r, budget_inr=expected - 1), "bf")
        assert tight["status"] == "OVER_BUDGET" and tight["shortfall_inr"] == 1
        assert domain.build(dict(r, budget_inr=expected), "bf")["status"] == "BUILT"  # equality is in budget


# ------------------------------------------------------------------ alternatives, edits, booked exports

SAMPLES = [req("BLR", "Japan", "2027-04-10", 6, budget=350000), req("BOM", "United Arab Emirates", "2027-04-10", 4),
           req("DEL", "Japan", "2027-04-07", 8, 2, (4, 11)), req("BLR", "Japan", "2027-04-03", 8, 2, (4, 11)), req("MAA", "United Arab Emirates", "2027-04-12", 6, 1, ())]


@pytest.mark.parametrize("r", SAMPLES, ids=lambda r: f"{r['departure_city']}-{r['destination_country'][:3]}")
def test_every_alternative_is_a_valid_edit_with_exact_price_difference(domain, raw, r):
    res = domain.build(r, "alt")
    if res["status"] != "BUILT":
        pytest.skip(f"no package for {r} ({res['reason_code'] or res['status']}) - nothing to edit")
    trip, before = res["trip"], copy.deepcopy(res["trip"])
    requests = [("outbound_flight", {}), ("return_flight", {})]
    requests += [("hotel", {"item_id": i["item_id"]}) for i in trip["items"] if i["role"] == "hotel_stay"]
    day = date.fromisoformat(trip["start_date"])
    while day.isoformat() < trip["end_date"]:
        requests.append(("attraction", {"date": day.isoformat()}))
        day += timedelta(days=1)
    checked = 0
    for component, filters in requests:
        alts = domain.alternatives(trip, component, dict(filters, limit=50))
        assert alternatives_shape_errors(alts, component) == []
        for alt in alts:
            out = domain.change(trip, alt["change"])
            assert out["ok"] and change_result_shape_errors(out, trip) == []
            assert out["trip"]["total_inr"] - trip["total_inr"] == alt["price_diff_inr"]
            assert judge(raw, out["trip"], initial=False) == [], (alt["change"], judge(raw, out["trip"], False))
            checked += 1
    assert trip == before and checked > 0
    booked = stamp_booked(trip, "0042424242")
    assert booked_export_problems(booked, "0042424242", trip) == [] and judge(raw, booked, initial=True) == []


def test_rejected_edits_never_change_anything(domain, raw):
    trip = domain.build(SAMPLES[0], "rej")["trip"]
    before = copy.deepcopy(trip)
    visits = [i for i in trip["items"] if i["role"] == "attraction_visit"]
    bad = [{"op": "add_attraction", "attraction_id": visits[0]["catalog_id"], "date": visits[1]["start"][:10]},
           {"op": "add_attraction", "attraction_id": "ATT-NAR-001", "date": visits[0]["start"][:10]},
           {"op": "add_attraction", "attraction_id": "ATT-TYO-006", "date": "2027-04-12"},  # closed 10-20 Apr
           {"op": "remove_attraction", "item_id": "i999"},
           {"op": "replace_flight", "item_id": trip["items"][0]["item_id"], "flight_id": "FL-1001"}]
    for change in bad:
        out = domain.change(trip, change)
        assert out["ok"] is False and out["trip"] is None and out["violations"], change
    assert trip == before and judge(raw, trip, initial=True) == []


# ------------------------------------------------------------------ boundaries

@pytest.mark.parametrize("start, nights", [("2027-04-01", 3), ("2027-04-10", 10), ("2027-04-17", 3)])
def test_data_window_boundaries_buildable_or_reasoned(domain, raw, start, nights):
    for home, country in itertools.product(HOMES, COUNTRIES):
        r = req(home, country, start, nights)
        res = domain.build(r, "edge")
        assert res["status"] in ("BUILT", "NO_PACKAGE")
        if res["trip"]:
            assert judge(raw, res["trip"], initial=True) == []
        else:
            assert res["reason_code"] != "OUTSIDE_DATA_WINDOW" and res["reason"]


@pytest.mark.parametrize("start, nights", [("2027-03-31", 3), ("2027-04-18", 3), ("2027-04-11", 10)])
def test_outside_window_is_reasoned_no_package(domain, start, nights):
    res = domain.build(req("BLR", "Japan", start, nights), "edge")
    assert res["status"] == "NO_PACKAGE" and res["reason_code"] == "OUTSIDE_DATA_WINDOW"


def test_large_party_respects_inventory(domain, raw):
    for country in COUNTRIES:
        r = req("BLR", country, "2027-04-08", 5, adults=9, kids=(1, 2, 3, 4, 5, 6))
        res = domain.build(r, "big")
        assert res["status"] in ("BUILT", "NO_PACKAGE", "OVER_BUDGET")
        if res["trip"]:
            assert judge(raw, res["trip"], initial=True) == []


# ------------------------------------------------------------------ the oracles themselves are not vacuous

def test_brute_force_finds_real_packages(raw):
    found = [brute_force_3_nights(raw, r) for r in (req("BLR", "Japan", "2027-04-02", 3),
                                                     req("DEL", "United Arab Emirates", "2027-04-05", 3))]
    assert all(isinstance(x, int) and x > 0 for x in found)


@pytest.mark.parametrize("mutate, needle", [
    (lambda t, i: i("arrival_transfer").update(start=t["_land59"]), "pickup < landing + 60"),
    (lambda t, i: (i("hotel_stay").update(price_inr=i("hotel_stay")["price_inr"] + 1),
                   t.update(total_inr=t["total_inr"] + 1)), "quantity/price"),
    (lambda t, i: i("attraction_visit").update(catalog_id="ATT-NAR-001"), "not in that night's hotel city"),
    (lambda t, i: i("return_flight").update(catalog_id="FL-1001"), "bad return flight"),
    (lambda t, i: t.update(budget_inr=t["total_inr"] - 1), "over budget"),
    (lambda t, i: t["items"].remove(i("attraction_visit")), "without attraction"),
])
def test_judge_oracle_detects_defects(domain, raw, mutate, needle):
    trip = copy.deepcopy(domain.build(SAMPLES[0], "m")["trip"])
    item = lambda role: next(x for x in trip["items"] if x["role"] == role)  # noqa: E731
    landed = dt(item("outbound_flight")["end"])
    trip["_land59"] = (landed + timedelta(minutes=59)).strftime("%Y-%m-%dT%H:%M")
    mutate(trip, item)
    trip.pop("_land59")
    assert any(needle in p for p in judge(raw, trip, initial=True)), judge(raw, trip, initial=True)
