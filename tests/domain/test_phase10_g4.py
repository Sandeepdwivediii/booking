"""Phase 10 (optional extension): G4 cheapest / fastest / best-rated recommendations.

Synthetic world = base Tokyo(2) -> Kyoto(2) catalog plus: a faster pricier outbound flight, a faster flight
without enough seats, a 5-star low-guest-rating Tokyo hotel, and a 5-star hotel sold out one night.
Exactness is checked against the independent exhaustive oracle on seeded random catalogs.
"""

from __future__ import annotations

import pytest

from app.domain.catalog import load_catalog
from app.domain.planner import build_trip, recommend_packages, trip_metrics
from app.domain.validator import validate_trip
from tests.domain_fixtures.catalog_builder import OFFICIAL_CATALOG_DIR, set_availability, synthetic_catalog, write_catalog
from tests.domain_fixtures.judge_oracle import Raw, judge
from tests.domain_fixtures.oracle import INR, brute_force_cheapest, random_catalog

REQ = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 4,
       "adults": 2, "children_ages": [6], "budget_inr": 10 ** 7}
EXHAUSTIVE_SEEDS = [1, 2, 3, 19, 23, 24, 25, 26, 29, 30, 37, 41, 42, 46, 49, 55, 57]


def _world(d):
    out = d["flights"][0]  # FL-1: 490 min, 25,000/seat
    d["flights"] += [
        dict(out, flight_id="FL-FAST", departure_local="2027-04-09T23:00:00", arrival_local="2027-04-10T09:00:00",
             duration_minutes=400, price_per_seat=INR(30000), seats_left=9),
        dict(out, flight_id="FL-FAST-FULL", departure_local="2027-04-10T00:00:00",
             arrival_local="2027-04-10T08:30:00", duration_minutes=300, price_per_seat=INR(31000), seats_left=1),
    ]
    d["hotels"][0]["rating"] = 9.0
    d["hotels"] += [dict(d["hotels"][0], hotel_id="HTL-T5", name="Five Star Low Rating", stars=5, rating=6.9),
                    dict(d["hotels"][0], hotel_id="HTL-T6", name="Five Star Sold Out", stars=5, rating=9.9)]
    d["rooms"] += [dict(d["rooms"][1], room_id="RM-T5-A", hotel_id="HTL-T5", price_per_night=INR(20000)),
                   dict(d["rooms"][1], room_id="RM-T6-A", hotel_id="HTL-T6", price_per_night=INR(1000))]
    set_availability(d, "RM-T5-A", "2027-04-10", [2, 2])
    set_availability(d, "RM-T6-A", "2027-04-10", [2, 0])  # invalid: one night sold out


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    data = synthetic_catalog()
    _world(data)
    root = write_catalog(tmp_path_factory.mktemp("g4"), data)
    return load_catalog(root), Raw(root)


def recs(cat, budget=REQ["budget_inr"], **kw):
    result = recommend_packages(cat, dict(REQ, budget_inr=budget, **kw), "trip-g4")
    return result, {r["label"]: r for r in result["recommendations"]}


def assert_valid(cat, raw, rec, budget):
    assert rec["total_inr"] <= budget and rec["trip"]["total_inr"] == rec["total_inr"]
    assert validate_trip(cat, rec["trip"], "initial") == [] and judge(raw, rec["trip"], initial=True) == []


def test_G4_A01_cheapest_is_exact_m8_minimum(world):
    cat, raw = world
    result, r = recs(cat)
    build = build_trip(cat, REQ, "trip-g4")
    assert {k: v for k, v in result.items() if k != "recommendations"} == build  # build() result unchanged
    assert r["cheapest"]["trip"] == build["trip"] and r["cheapest"]["total_inr"] == build["cheapest_total_inr"]


def test_G4_A02_A08_fastest_counts_flights_and_intercity_only(world):
    cat, raw = world
    _, r = recs(cat)
    fast = r["fastest"]
    out = next(i for i in fast["trip"]["items"] if i["role"] == "outbound_flight")
    assert out["catalog_id"] == "FL-FAST" and fast["travel_minutes"] == 400 + 540 + 135
    assert fast["total_inr"] > r["cheapest"]["total_inr"]  # A08: winner even though not cheapest
    assert r["cheapest"]["travel_minutes"] == 490 + 540 + 135
    assert_valid(cat, raw, fast, REQ["budget_inr"])


def test_G4_A03_A08_best_rated_uses_stars_not_guest_rating(world):
    cat, raw = world
    _, r = recs(cat)
    best = r["best_rated"]
    tokyo = next(i for i in best["trip"]["items"] if i["role"] == "hotel_stay" and i["city"] == "Tokyo")
    assert tokyo["catalog_id"] == "HTL-T5"  # 5 stars despite guest rating 6.9 < 9.0
    assert best["star_nights"] == 5 * 2 + 3 * 2 and best["average_stars"] == 4.0
    assert best["total_inr"] > r["cheapest"]["total_inr"]
    assert_valid(cat, raw, best, REQ["budget_inr"])


def test_G4_A04_over_budget_candidates_excluded(world):
    cat, raw = world
    cheapest = recs(cat)[1]["cheapest"]["total_inr"]
    budget = cheapest + 14999  # FL-FAST costs +15,000 -> unaffordable
    _, r = recs(cat, budget=budget)
    assert r["fastest"]["travel_minutes"] == 490 + 540 + 135
    assert r["best_rated"]["average_stars"] == 3.5  # 5-star option costs more than the budget allows
    for rec in r.values():
        assert_valid(cat, raw, rec, budget)


def test_G4_A05_same_package_may_win_every_label(tmp_path):
    cat = load_catalog(write_catalog(tmp_path, synthetic_catalog()))  # exactly one feasible package
    _, r = recs(cat)
    assert r["cheapest"]["trip"] == r["fastest"]["trip"] == r["best_rated"]["trip"]
    assert [x for x in r] == ["cheapest", "fastest", "best_rated"]


def test_G4_A06_ties_accept_either_winner_with_correct_score(world, tmp_path):
    data = synthetic_catalog()
    _world(data)
    data["flights"].append(dict(data["flights"][-2], flight_id="FL-FAST-TWIN"))  # same 400 min, same price
    cat = load_catalog(write_catalog(tmp_path, data))
    _, r = recs(cat)
    out = next(i for i in r["fastest"]["trip"]["items"] if i["role"] == "outbound_flight")
    assert out["catalog_id"] in {"FL-FAST", "FL-FAST-TWIN"} and r["fastest"]["travel_minutes"] == 1075


def test_G4_A07_multi_city_metrics(world):
    cat, _ = world
    _, r = recs(cat)
    for rec in r.values():
        hotels = [i for i in rec["trip"]["items"] if i["role"] == "hotel_stay"]
        assert len(hotels) == 2  # Tokyo(2) -> Kyoto(2)
        m = trip_metrics(cat, rec["trip"])
        assert m == {k: rec[k] for k in ("travel_minutes", "star_nights", "average_stars")}
    assert r["cheapest"]["average_stars"] == (4 * 2 + 3 * 2) / 4


def test_G4_A09_no_package_has_no_recommendations(tmp_path):
    data = synthetic_catalog()
    data["flights"].pop(1)  # no return flight
    cat = load_catalog(write_catalog(tmp_path, data))
    result, r = recs(cat)
    assert result["status"] == "NO_PACKAGE" and r == {} and result["reason_code"] == "NO_RETURN_FLIGHT"


def test_G4_A10_over_budget_keeps_exact_cheapest_and_no_cards(world):
    cat, _ = world
    cheapest = recs(cat)[1]["cheapest"]["total_inr"]
    result, r = recs(cat, budget=cheapest - 1)
    assert result["status"] == "OVER_BUDGET" and r == {}
    assert result["cheapest_total_inr"] == cheapest and result["shortfall_inr"] == 1


def test_G4_A11_invalid_fast_or_high_rated_combinations_excluded(world):
    cat, _ = world
    _, r = recs(cat)
    ids = {i["catalog_id"] for rec in r.values() for i in rec["trip"]["items"]}
    assert "FL-FAST-FULL" not in ids  # 300-minute flight lacks seats for 3 travelers
    assert "HTL-T6" not in ids        # 5-star hotel sold out on one night


@pytest.mark.parametrize("seed", EXHAUSTIVE_SEEDS)
def test_G4_A12_winners_match_exhaustive_oracle(tmp_path, seed):
    data, req = random_catalog(seed)
    cat = load_catalog(write_catalog(tmp_path, data))
    packages: list = []
    brute_force_cheapest(cat, req, collect=packages)
    assert len(packages) >= 10
    costs = sorted(c for c, _ in packages)
    for budget in (costs[0], costs[len(costs) // 2], costs[-1]):
        result = recommend_packages(cat, dict(req, budget_inr=budget), "T")
        within = [(c, trip_metrics(cat, t)) for c, t in packages if c <= budget]
        r = {x["label"]: x for x in result["recommendations"]}
        assert r["cheapest"]["total_inr"] == min(c for c, _ in within)
        assert r["fastest"]["travel_minutes"] == min(m["travel_minutes"] for _, m in within)
        assert r["best_rated"]["star_nights"] == max(m["star_nights"] for _, m in within)
        assert all(x["total_inr"] <= budget and validate_trip(cat, x["trip"], "initial") == [] for x in r.values())


@pytest.mark.parametrize("req", [
    {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-03", "nights": 8,
     "adults": 2, "children_ages": [6], "budget_inr": 320000},
    {"departure_city": "BOM", "destination_country": "United Arab Emirates", "start_date": "2027-04-10",
     "nights": 4, "adults": 2, "children_ages": [6], "budget_inr": 350000},
], ids=["japan", "uae"])
def test_official_recommendations_valid_and_within_budget(req):
    cat, raw = load_catalog(OFFICIAL_CATALOG_DIR), Raw(OFFICIAL_CATALOG_DIR)
    result = recommend_packages(cat, req, "official")
    assert result["status"] == "BUILT" and len(result["recommendations"]) == 3
    for rec in result["recommendations"]:
        assert_valid(cat, raw, rec, req["budget_inr"])
    r = {x["label"]: x for x in result["recommendations"]}
    assert r["fastest"]["travel_minutes"] <= r["cheapest"]["travel_minutes"]
    assert r["best_rated"]["star_nights"] >= r["cheapest"]["star_nights"]
