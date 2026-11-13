"""Phase 9 (optional extension): G3 change city plan and rebuild the complete package.

Official catalog unless noted. Base: BLR -> Japan, 2027-04-10, 6 nights, 2A + 2C, budget 400,000
(cheapest = Tokyo x6). Results are audited by the independent judge oracle and the contract checker.
"""

from __future__ import annotations

import copy

import pytest

from app.contracts import change_result_shape_errors
from app.domain.adapter import Domain
from app.domain.catalog import load_catalog
from app.domain.planner import STAGES, city_plans
from app.domain.pricing import price_trip
from tests.domain_fixtures.catalog_builder import OFFICIAL_CATALOG_DIR, synthetic_catalog, write_catalog
from tests.domain_fixtures.judge_oracle import Raw, judge

REQ = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 6,
       "adults": 2, "children_ages": [4, 11], "budget_inr": 400000}
FIXED = ("trip_id", "traveler", "country", "start_date", "end_date", "budget_inr")


@pytest.fixture(scope="module")
def world():
    domain = Domain(load_catalog(OFFICIAL_CATALOG_DIR))
    return domain, Raw(OFFICIAL_CATALOG_DIR), domain.build(REQ, "trip-g3")["trip"]


def rebuild(domain, trip, cities, nights):
    before = copy.deepcopy(trip)
    result = domain.rebuild_city_plan(trip, cities, nights)
    assert trip == before  # original never modified
    if result["ok"]:
        assert change_result_shape_errors(result, trip) == []
    else:
        assert result["trip"] is None and result["violations"]
    return result


def codes(r):
    return {v["code"] for v in r["violations"]}


def plan_of(trip):
    hotels = sorted((i for i in trip["items"] if i["role"] == "hotel_stay"), key=lambda i: i["start"])
    return [h["city"] for h in hotels], [h["start"] for h in hotels]


def test_G3_A01_valid_other_plan_rebuilds_and_preserves_request(world):
    domain, raw, trip = world
    assert plan_of(trip)[0] == ["Tokyo"]
    r = rebuild(domain, trip, ["Tokyo", "Osaka"], [2, 4])
    assert r["ok"] and plan_of(r["trip"]) == (["Tokyo", "Osaka"], ["2027-04-10", "2027-04-12"])
    assert all(r["trip"][k] == trip[k] for k in FIXED)
    assert judge(raw, r["trip"], initial=True) == []
    assert r["adjustments"] and "Tokyo (6) to Tokyo (2) -> Osaka (4)" in r["adjustments"][0]


@pytest.mark.parametrize("cities", [["Tokyo", "Dubai"], ["Tokyo", "Nara"], ["Paris"]])
def test_G3_A02_unknown_or_foreign_city(world, cities):
    domain, _, trip = world
    nights = [6] if len(cities) == 1 else [3, 3]
    assert "CITY_NOT_IN_COUNTRY" in codes(rebuild(domain, trip, cities, nights))


def test_G3_A03_repeated_city(world):
    domain, _, trip = world
    assert "CITY_REPEATED" in codes(rebuild(domain, trip, ["Tokyo", "Osaka", "Tokyo"], [2, 2, 2]))


@pytest.mark.parametrize("nights", [[5, 1], [1, 5], [3, 2, 1]])
def test_G3_A04_segment_shorter_than_two_nights(world, nights):
    domain, _, trip = world
    cities = ["Tokyo", "Osaka", "Kyoto"][:len(nights)]
    assert "CITY_MIN_NIGHTS" in codes(rebuild(domain, trip, cities, nights))


def test_G3_A05_missing_or_reversed_directed_link(tmp_path):
    domain = Domain(load_catalog(write_catalog(tmp_path, synthetic_catalog())))  # only Tokyo -> Kyoto linked
    trip = domain.build(dict(REQ, nights=4, children_ages=[6], budget_inr=10 ** 7), "syn")["trip"]
    r = rebuild(domain, trip, ["Kyoto", "Tokyo"], [2, 2])
    assert codes(r) == {"NO_CITY_LINK"}


def test_G3_A06_every_dependent_item_recomputed_from_catalog(world):
    domain, raw, trip = world
    r = rebuild(domain, trip, ["Tokyo", "Kyoto", "Osaka"], [2, 2, 2])
    new = r["trip"]
    priced = price_trip(new, domain.catalog)
    assert priced["errors"] == [] and new["total_inr"] == priced["total_inr"]
    assert all((i["quantity"], i["price_inr"]) == priced["items"][i["item_id"]] for i in new["items"])
    roles = [i["role"] for i in new["items"]]
    assert roles.count("intercity_transfer") == 2 and roles.count("hotel_stay") == 3
    ret = next(i for i in new["items"] if i["role"] == "return_flight")
    assert "Osaka" in domain.catalog.airports[domain.catalog.flights[ret["catalog_id"]]["from"]]["serves_cities"]
    for h in (i for i in new["items"] if i["role"] == "hotel_stay"):  # stock on every night (independent)
        assert judge(raw, new, initial=True) == []
        assert domain.catalog.hotels[h["catalog_id"]]["city"] == h["city"]


def test_G3_A07_structurally_valid_but_infeasible_plan(world, tmp_path):
    domain, _, trip = world
    feasible = {(tuple(p["cities"]), tuple(p["nights"])) for p in domain.city_plan_options(trip)}
    assert feasible <= set(city_plans(domain.catalog.countries["Japan"], 6, 2))  # options are real plans
    syn = Domain(load_catalog(write_catalog(tmp_path, synthetic_catalog())))  # no flight lands at KIX
    base = syn.build(dict(REQ, nights=4, children_ages=[6], budget_inr=10 ** 7), "syn")["trip"]
    r = rebuild(syn, base, ["Kyoto"], [4])
    assert codes(r) == {"NO_OUTBOUND_FLIGHT"} and r["violations"][0]["message"].startswith("No complete package")
    assert ["Kyoto"] not in [p["cities"] for p in syn.city_plan_options(base)]


def test_G3_A08_over_budget_plan_rejected_original_kept(world):
    domain, _, trip = world
    tight = dict(copy.deepcopy(trip), budget_inr=trip["total_inr"])
    r = rebuild(domain, tight, ["Tokyo", "Osaka"], [2, 4])  # +15,826 over the cheapest
    assert codes(r) == {"OVER_BUDGET"}
    options = domain.city_plan_options(tight)
    assert [o["within_budget"] for o in options][0] is True and not all(o["within_budget"] for o in options)


def test_G3_A09_one_to_many_and_reordering_keep_chronology(world):
    domain, raw, trip = world
    for cities, nights in ((["Tokyo", "Kyoto", "Osaka"], [2, 2, 2]), (["Tokyo", "Osaka", "Kyoto"], [2, 2, 2])):
        r = rebuild(domain, trip, cities, nights)
        new = r["trip"]
        assert (new["start_date"], new["end_date"]) == (trip["start_date"], trip["end_date"])
        assert plan_of(new) == (cities, ["2027-04-10", "2027-04-12", "2027-04-14"])
        inter = [i["start"][:10] for i in new["items"] if i["role"] == "intercity_transfer"]
        assert inter == ["2027-04-12", "2027-04-14"]
        timed = [i["start"] for i in new["items"]
                 if i["role"] not in ("outbound_flight", "return_flight", "hotel_stay")]
        assert timed == sorted(timed)  # destination-side events in local-time order (hotels are date ranges)
        assert judge(raw, new, initial=True) == []
        back = rebuild(domain, new, ["Tokyo"], [6])  # and back to one city
        assert back["ok"] and back["trip"]["total_inr"] == trip["total_inr"]


def test_G3_A10_rebuild_is_cheapest_for_the_selected_plan(world, tmp_path):
    domain, _, trip = world
    for option in domain.city_plan_options(trip)[:6]:
        r = rebuild(domain, trip, option["cities"], option["nights"])
        assert r["ok"] and r["trip"]["total_inr"] == option["total_inr"]
        assert r["trip"]["total_inr"] - trip["total_inr"] == option["price_diff_inr"]
    assert domain.city_plan_options(trip)[0]["total_inr"] == trip["total_inr"]  # global cheapest == M8 build
    syn = Domain(load_catalog(write_catalog(tmp_path, synthetic_catalog())))
    base = syn.build(dict(REQ, nights=4, children_ages=[6], budget_inr=10 ** 7), "syn")["trip"]
    same = rebuild(syn, base, ["Tokyo", "Kyoto"], [2, 2])
    assert same["ok"] and same["trip"]["total_inr"] == 252000  # hand-verified cheapest for this plan


def test_malformed_plan_and_edited_trip(world):
    domain, raw, trip = world
    for cities, nights in (("Tokyo", [6]), (["Tokyo"], ["6"]), (["Tokyo", "Osaka"], [6]), ([], [])):
        assert codes(rebuild(domain, trip, cities, nights)) == {"CHANGE_MALFORMED"}
    assert "PLAN_NIGHTS_MISMATCH" in codes(rebuild(domain, trip, ["Tokyo", "Osaka"], [2, 2]))
    visit = next(i for i in trip["items"] if i["role"] == "attraction_visit")
    edited = domain.change(trip, {"op": "remove_attraction", "item_id": visit["item_id"]})["trip"]
    r = rebuild(domain, edited, ["Tokyo", "Kyoto"], [2, 4])
    assert r["ok"] and judge(raw, r["trip"], initial=True) == []
