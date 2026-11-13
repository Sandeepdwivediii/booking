"""Phase 3 planner: city plans, flights, transfers, exact M8 optimality and result states.

Synthetic BASE (tests/domain_fixtures/catalog_builder.py), request 2A+1C(6), 2027-04-10, 4 nights.
The only feasible plan is Tokyo(2) -> Kyoto(2) (outbound lands NRT, return leaves KIX). Hand total:
  flights 25,000x3 + 24,000x3 = 147,000 | NRT taxi 13,000 x ceil(3/4)=1 = 13,000
  Tokyo twin 6,000 x 2 rooms x 2 nights = 24,000 | ATT-T1 2x1,200 + 600 = 3,000
  train 14,000 x 3 = 42,000 | Kyoto family 9,000 x 1 x 2 = 18,000 | ATT-K1 0 | KIX bus 2x2,000+1,000 = 5,000
  = 252,000
"""

from __future__ import annotations

import copy
from datetime import date, timedelta

import pytest

from app.contracts import InvalidRequestError, build_result_shape_errors
from app.domain.adapter import Domain
from app.domain.catalog import load_catalog
from app.domain.planner import build_trip, city_plans
from app.domain.validator import validate_trip
from tests.domain_fixtures.catalog_builder import OFFICIAL_CATALOG_DIR, set_availability, synthetic_catalog, write_catalog
from tests.domain_fixtures.oracle import INR, brute_force_cheapest, random_catalog

BASE_TOTAL = 252000


def request(**kw) -> dict:
    req = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 4,
           "adults": 2, "children_ages": [6], "budget_inr": 10 ** 7}
    req.update(kw)
    return req


def load(tmp_path, *mutations):
    data = synthetic_catalog()
    for m in mutations:
        m(data)
    return load_catalog(write_catalog(tmp_path, data))


def build(cat, req=None, trip_id="trip-1"):
    req = req or request()
    result = build_trip(cat, req, trip_id)
    assert build_result_shape_errors(result, req, trip_id) == []
    if result["trip"]:
        assert validate_trip(cat, result["trip"], "initial") == []
    return result


def role(trip, name):
    return [i for i in trip["items"] if i["role"] == name]


def flight(fid, frm, to, dep, arr, price, seats=9, stops=0, dur=490):
    return {"flight_id": fid, "airline": "T", "flight_number": fid, "from": frm, "to": to,
            "departure_local": dep + ":00", "arrival_local": arr + ":00", "stops": stops, "duration_minutes": dur,
            "price_per_seat": INR(price), "seats_left": seats, "cabin": "ECONOMY"}


@pytest.fixture
def base(tmp_path):
    return load(tmp_path)


# ------------------------------------------------------------------ baseline

def test_base_package_exact_hand_total(base):
    r = build(base)
    assert r["status"] == "BUILT" and r["trip"]["total_inr"] == BASE_TOTAL == r["cheapest_total_inr"]
    assert [i["role"] for i in r["trip"]["items"]] == [
        "outbound_flight", "arrival_transfer", "hotel_stay", "attraction_visit", "intercity_transfer",
        "hotel_stay", "attraction_visit", "departure_transfer", "return_flight"]


def test_build_is_deterministic_and_pure(base):
    req = request()
    snapshot = copy.deepcopy(req)
    assert build(base, req) == build(base, req)
    assert req == snapshot


def test_SY14_end_date_is_start_plus_nights(base):
    trip = build(base)["trip"]
    assert trip["end_date"] == "2027-04-14"  # not start + nights - 1
    assert role(trip, "hotel_stay")[-1]["end"] == "2027-04-14"  # check-out = return day, no hotel night on it
    assert role(trip, "return_flight")[0]["start"].startswith("2027-04-14")


# ------------------------------------------------------------------ city plans (CT)

def test_CT01_three_nights_only_single_city_plans(base):
    plans = city_plans(base.countries["Japan"], 3, 2)
    assert plans and all(len(cities) == 1 for cities, _ in plans)


def test_CT02_four_nights_two_by_two_is_legal(base):
    assert (("Tokyo", "Kyoto"), (2, 2)) in city_plans(base.countries["Japan"], 4, 2)
    trip = build(base)["trip"]
    assert [h["city"] for h in role(trip, "hotel_stay")] == ["Tokyo", "Kyoto"]


def test_CT03_no_segment_shorter_than_min_nights():
    country = {"cities": [{"name": c} for c in ("A", "B", "C")],
               "links": [{"from_city": a, "to_city": b} for a in "ABC" for b in "ABC" if a != b]}
    plans = city_plans(country, 5, 2)
    assert all(min(split) >= 2 for _, split in plans)
    assert not any(split == (2, 1, 2) for _, split in plans)
    assert (("A", "B"), (2, 3)) in plans and (("A",), (5,)) in plans


def test_CT04_city_never_repeated():
    country = {"cities": [{"name": "Tokyo"}, {"name": "Kyoto"}],
               "links": [{"from_city": "Tokyo", "to_city": "Kyoto"}, {"from_city": "Kyoto", "to_city": "Tokyo"}]}
    plans = city_plans(country, 6, 2)
    assert all(len(set(cities)) == len(cities) for cities, _ in plans)
    assert not any(cities == ("Tokyo", "Kyoto", "Tokyo") for cities, _ in plans)


def test_CT05_links_are_directed(base):
    plans = city_plans(base.countries["Japan"], 4, 2)
    assert (("Tokyo", "Kyoto"), (2, 2)) in plans
    assert not any(cities == ("Kyoto", "Tokyo") for cities, _ in plans)


def test_CT05_reverse_route_needs_its_own_link(tmp_path):
    def reverse_flights(d):  # arrive Kyoto (KIX), leave Tokyo (NRT): needs Kyoto -> Tokyo
        d["flights"][0].update({"to": "KIX"})
        d["flights"][1].update({"from": "NRT"})
    r = build(load(tmp_path / "a", reverse_flights))
    assert r["status"] == "NO_PACKAGE"

    def add_reverse_link(d):
        reverse_flights(d)
        d["transfers"].append(dict(d["transfers"][2], transfer_id="TR-KYO-TYO", from_city="Kyoto", to_city="Tokyo"))
        d["destinations"]["countries"][0]["links"].append(
            {"from_city": "Kyoto", "to_city": "Tokyo", "transfer_ids": ["TR-KYO-TYO"]})
        d["transfers"].append({"transfer_id": "TR-NRT-KYOLESS", "type": "AIRPORT_BUS", "name": "x", "airport": "NRT",
                               "city": "Tokyo", "area": None, "max_passengers": 99, "service_from": "00:00",
                               "service_until": "24:00", "duration_minutes": 60, "pricing": "PER_PERSON",
                               "price": INR(100)})
    r = build(load(tmp_path / "b", add_reverse_link))
    assert r["status"] == "BUILT"
    assert [h["city"] for h in role(r["trip"], "hotel_stay")] == ["Kyoto", "Tokyo"]


def test_CT09_attraction_only_city_never_planned(tmp_path):
    cat = load(tmp_path)
    assert all("Nara" not in cities for cities, _ in city_plans(cat.countries["Japan"], 6, 2))
    trip = build(cat)["trip"]
    assert all(i["city"] != "Nara" for i in trip["items"])


def test_CT10_no_eligible_path_gives_reasoned_no_package(tmp_path):
    def break_paths(d):  # only Kyoto -> Tokyo link, but arrival is Tokyo and return is Kyoto
        d["destinations"]["countries"][0]["links"][0].update({"from_city": "Kyoto", "to_city": "Tokyo"})
        d["transfers"][2].update({"from_city": "Kyoto", "to_city": "Tokyo"})
    r = build(load(tmp_path, break_paths))
    assert r["status"] == "NO_PACKAGE" and r["trip"] is None and r["cheapest_total_inr"] is None
    assert r["reason_code"] in {"NO_OUTBOUND_FLIGHT", "NO_RETURN_FLIGHT"} and r["reason"]


# ------------------------------------------------------------------ flights (FL)

def test_FL01_overnight_outbound_departing_day_before_is_valid(base):
    out = role(build(base)["trip"], "outbound_flight")[0]
    assert out["start"].startswith("2027-04-09") and out["end"].startswith("2027-04-10")


def test_FL02_arrival_on_wrong_local_date_not_used(tmp_path):
    cat = load(tmp_path, lambda d: d["flights"][0].update(
        {"departure_local": "2027-04-10T23:50:00", "arrival_local": "2027-04-11T10:30:00"}))
    r = build(cat)
    assert r["status"] == "NO_PACKAGE" and r["reason_code"] == "NO_OUTBOUND_FLIGHT"


def test_FL03_return_departing_day_before_end_not_used(tmp_path):
    cat = load(tmp_path, lambda d: d["flights"][1].update(
        {"departure_local": "2027-04-13T12:00:00", "arrival_local": "2027-04-13T18:00:00"}))
    r = build(cat)
    assert r["status"] == "NO_PACKAGE" and r["reason_code"] == "NO_RETURN_FLIGHT"


def test_FL04_seats_equal_travelers_is_eligible(base):
    assert base.flights["FL-1"]["seats_left"] == 3
    assert role(build(base)["trip"], "outbound_flight")[0]["catalog_id"] == "FL-1"


def test_FL05_insufficient_outbound_seats_regardless_of_return(tmp_path):
    def seats(d):
        d["flights"][0]["seats_left"] = 2
        d["flights"][1]["seats_left"] = 4
    r = build(load(tmp_path, seats))
    assert r["status"] == "NO_PACKAGE" and r["reason_code"] == "NO_OUTBOUND_FLIGHT"


def test_FL06_one_stop_flight_allowed(base):
    assert base.flights["FL-2"]["stops"] == 1
    assert role(build(base)["trip"], "return_flight")[0]["catalog_id"] == "FL-2"


def test_FL07_airport_serving_city_without_own_airport(base):
    assert "Kyoto" in base.airports["KIX"]["serves_cities"] and base.airports["KIX"]["city"] == "Osaka"
    ret = role(build(base)["trip"], "return_flight")[0]
    assert ret["name"].endswith("KIX to BLR")


def test_FL08_country_airport_not_serving_first_city_rejected(tmp_path):
    # A much cheaper flight lands at KIX (Japan, serves Kyoto only): it cannot start a Tokyo-first plan.
    cat = load(tmp_path, lambda d: d["flights"].append(
        flight("FL-CHEAP", "BLR", "KIX", "2027-04-09T22:00", "2027-04-10T09:00", 1000)))
    trip = build(cat)["trip"]
    assert role(trip, "outbound_flight")[0]["catalog_id"] == "FL-1" and trip["total_inr"] == BASE_TOTAL


def test_FL09_return_must_land_at_home_airport(tmp_path):
    def other_home(d):
        d["airports"].append({"code": "DEL", "name": "Delhi", "city": "Delhi", "country": "IN",
                              "timezone": "Asia/Kolkata"})
        d["flights"][1]["to"] = "DEL"
    r = build(load(tmp_path, other_home))
    assert r["status"] == "NO_PACKAGE" and r["reason_code"] == "NO_RETURN_FLIGHT"


def test_FL10_different_arrival_and_return_airports(base):
    trip = build(base)["trip"]
    assert role(trip, "outbound_flight")[0]["name"].endswith("NRT")
    assert role(trip, "return_flight")[0]["name"].endswith("KIX to BLR")


# ------------------------------------------------------------------ transfers (TR)

def test_TR01_pickup_exactly_60_minutes_after_landing(base):
    arr = role(build(base)["trip"], "arrival_transfer")[0]
    assert arr["start"] == "2027-04-10T11:30"  # landed 10:30


def test_TR03_late_landing_uses_next_day_service(tmp_path):
    def late(d):
        d["flights"][0].update({"departure_local": "2027-04-10T13:00:00", "arrival_local": "2027-04-10T23:30:00"})
        d["transfers"][0].update({"service_from": "06:00", "service_until": "00:30"})
    r = build(load(tmp_path, late))
    arr = role(r["trip"], "arrival_transfer")[0]
    assert r["status"] == "BUILT" and arr["start"] == "2027-04-11T00:30"
    assert arr["warnings"]  # next-day pickup is flagged to the traveler


def test_TR03_closed_at_night_waits_for_morning_service(tmp_path):
    cat = load(tmp_path, lambda d: d["flights"][0].update(
        {"departure_local": "2027-04-10T13:00:00", "arrival_local": "2027-04-10T23:30:00"}))
    arr = role(build(cat)["trip"], "arrival_transfer")[0]
    assert arr["start"] == "2027-04-11T06:00" and arr["warnings"]


def test_TR04_departure_pickup_exactly_180_minutes_boundary(tmp_path):
    def evening(d):
        d["flights"][1].update({"departure_local": "2027-04-14T18:00:00", "arrival_local": "2027-04-15T00:00:00"})
        d["transfers"][1]["duration_minutes"] = 60
    dep = role(build(load(tmp_path, evening))["trip"], "departure_transfer")[0]
    assert dep["start"] == "2027-04-14T14:00" and dep["end"] == "2027-04-14T15:00"


def test_TR07_area_specific_transfer_not_used_for_other_area(tmp_path):
    def asakusa_only(d):
        d["transfers"][0]["area"] = "Asakusa"  # hotel HTL-T1 is in Ginza
    r = build(load(tmp_path, asakusa_only))
    assert r["status"] == "NO_PACKAGE" and r["reason_code"] == "NO_AIRPORT_TRANSFER"


def test_TR07_planner_picks_hotel_area_with_a_transfer(tmp_path):
    def two_hotels(d):
        d["transfers"][0]["area"] = "Asakusa"
        d["hotels"].append(dict(d["hotels"][0], hotel_id="HTL-T2", name="Asakusa Inn", area="Asakusa"))
        d["rooms"].append(dict(d["rooms"][0], room_id="RM-T2-A", hotel_id="HTL-T2", price_per_night=INR(9000)))
        set_availability(d, "RM-T2-A", "2027-04-10", [2, 2])
    trip = build(load(tmp_path, two_hotels))["trip"]
    assert role(trip, "hotel_stay")[0]["catalog_id"] == "HTL-T2"  # pricier hotel, but the only reachable area


def test_TR08_area_null_serves_every_area(base):
    dep = role(build(base)["trip"], "departure_transfer")[0]
    assert dep["catalog_id"] == "TR-KIX-KYO" and base.transfers["TR-KIX-KYO"]["area"] is None


def test_TR15_airport_record_used_in_reverse_for_return(base):
    assert role(build(base)["trip"], "departure_transfer")[0]["catalog_id"] == "TR-KIX-KYO"


def test_TR11_TR12_only_linked_directed_intercity_ids_used(tmp_path):
    def decoys(d):  # cheaper Tokyo->Kyoto service that is NOT on the link, and a reverse service
        d["transfers"].append(dict(d["transfers"][2], transfer_id="TR-UNLINKED", price=INR(1)))
        d["transfers"].append(dict(d["transfers"][2], transfer_id="TR-REVERSE", from_city="Kyoto",
                                   to_city="Tokyo", price=INR(1)))
    trip = build(load(tmp_path, decoys))["trip"]
    assert [i["catalog_id"] for i in role(trip, "intercity_transfer")] == ["TR-TYO-KYO"]
    assert trip["total_inr"] == BASE_TOTAL


def test_TR13_intercity_uses_scheduled_departure(base):
    train = role(build(base)["trip"], "intercity_transfer")[0]
    assert train["start"] == "2027-04-12T09:00" and train["end"] == "2027-04-12T11:15"


def test_TR17_station_transfer_without_optional_city_taxi(base):
    trip = build(base)["trip"]
    assert not role(trip, "city_taxi")


# ------------------------------------------------------------------ hotels (HT-08..10)

def test_HT08_HT09_one_hotel_and_room_type_per_city(tmp_path):
    def mixed(d):  # cheapest per-night mix would be T2 night 1 + T1 night 2; only whole stays allowed
        d["hotels"].append(dict(d["hotels"][0], hotel_id="HTL-T2", name="T2"))
        d["rooms"].append(dict(d["rooms"][0], room_id="RM-T2-A", hotel_id="HTL-T2", price_per_night=INR(1000)))
        set_availability(d, "RM-T2-A", "2027-04-10", [2, 0])
    trip = build(load(tmp_path, mixed))["trip"]
    hotels = role(trip, "hotel_stay")
    assert len(hotels) == 2 and hotels[0]["catalog_id"] == "HTL-T1" and hotels[0]["room_id"] == "RM-T1-A"


def test_HT10_hotel_always_in_segment_city(base):
    for h in role(build(base)["trip"], "hotel_stay"):
        assert base.hotels[h["catalog_id"]]["city"] == h["city"]


# ------------------------------------------------------------------ pricing / result states (PR)

def test_PR08_budget_equal_to_cheapest_is_built(base):
    r = build(base, request(budget_inr=BASE_TOTAL))
    assert r["status"] == "BUILT" and r["trip"]["total_inr"] == BASE_TOTAL


def test_PR09_one_rupee_short_is_over_budget(base):
    r = build(base, request(budget_inr=BASE_TOTAL - 1))
    assert r["status"] == "OVER_BUDGET" and r["cheapest_total_inr"] == BASE_TOTAL and r["shortfall_inr"] == 1
    assert r["trip"] is None and r["suggestion"] and r["reason"]


def test_PR10_cheapest_flight_without_compatible_transfer_skipped(tmp_path):
    def unusable_cheap_flight(d):  # HND serves Tokyo but has no airport transfer at all
        d["airports"].append({"code": "HND", "name": "Haneda", "city": "Tokyo", "country": "JP",
                              "timezone": "Asia/Tokyo", "serves_cities": ["Tokyo"]})
        d["destinations"]["countries"][0]["cities"][0]["airports"].append("HND")
        d["flights"].append(flight("FL-HND", "BLR", "HND", "2027-04-09T22:00", "2027-04-10T09:00", 1000))
    trip = build(load(tmp_path, unusable_cheap_flight))["trip"]
    assert role(trip, "outbound_flight")[0]["catalog_id"] == "FL-1" and trip["total_inr"] == BASE_TOTAL


def test_PR11_cheapest_room_missing_stock_one_night_excluded(tmp_path):
    def cheap_room(stock):
        def m(d):
            d["rooms"].append({"room_id": "RM-T1-C", "hotel_id": "HTL-T1", "name": "Cheap family",
                               "max_adults": 2, "max_children": 2, "price_per_night": INR(1000)})
            set_availability(d, "RM-T1-C", "2027-04-10", stock)
        return m
    r = build(load(tmp_path / "a", cheap_room([1, 0])))
    assert role(r["trip"], "hotel_stay")[0]["room_id"] == "RM-T1-A" and r["trip"]["total_inr"] == BASE_TOTAL
    r = build(load(tmp_path / "b", cheap_room([1, 1])))  # control: with stock it is chosen (k = 1)
    assert role(r["trip"], "hotel_stay")[0]["room_id"] == "RM-T1-C"
    assert r["trip"]["total_inr"] == BASE_TOTAL - 24000 + 2000


def test_PR12_cheaper_sixth_candidate_is_found(tmp_path):
    def many_hotels(d):
        for n in range(2, 8):  # HTL-T2..T7; the cheapest is the LAST one generated
            d["hotels"].append(dict(d["hotels"][0], hotel_id=f"HTL-T{n}", name=f"T{n}"))
            d["rooms"].append(dict(d["rooms"][0], room_id=f"RM-T{n}-A", hotel_id=f"HTL-T{n}",
                                   price_per_night=INR(6000 - 500 * n if n == 7 else 7000 + n)))
            set_availability(d, f"RM-T{n}-A", "2027-04-10", [2, 2])
    trip = build(load(tmp_path, many_hotels))["trip"]
    assert role(trip, "hotel_stay")[0]["catalog_id"] == "HTL-T7"
    assert trip["total_inr"] == BASE_TOTAL - 2 * 2 * 3500


def test_PR13_feasible_but_none_in_budget_is_over_budget_not_no_package(base):
    r = build(base, request(budget_inr=1000))
    assert r["status"] == "OVER_BUDGET" and r["cheapest_total_inr"] == BASE_TOTAL
    assert r["shortfall_inr"] == BASE_TOTAL - 1000 and "252,000" in r["suggestion"]


def test_PR13_zero_budget_is_over_budget(base):
    assert build(base, request(budget_inr=0))["status"] == "OVER_BUDGET"


def test_PR14_no_legal_solution_is_no_package_without_price(tmp_path):
    r = build(load(tmp_path, lambda d: d["flights"].pop(1)), request(budget_inr=0))
    assert r["status"] == "NO_PACKAGE" and r["cheapest_total_inr"] is None and r["shortfall_inr"] is None
    assert r["reason_code"] == "NO_RETURN_FLIGHT"


def test_PR15_status_monotonic_in_budget(base):
    statuses = [build(base, request(budget_inr=b))["status"]
                for b in (0, BASE_TOTAL - 1, BASE_TOTAL, BASE_TOTAL + 1, 10 ** 8)]
    assert statuses == ["OVER_BUDGET", "OVER_BUDGET", "BUILT", "BUILT", "BUILT"]


# ------------------------------------------------------------------ attractions in plans

def test_attraction_closed_date_and_count_drive_feasibility(tmp_path):
    # ATT-T1 is Tokyo's only attraction; closing it on the single Tokyo full day kills the plan.
    r = build(load(tmp_path, lambda d: d["attractions"][0].update({"closed_dates": ["2027-04-11"]})))
    assert r["status"] == "NO_PACKAGE" and r["reason_code"] == "NO_ATTRACTION_SCHEDULE"


def test_cheapest_distinct_attraction_assignment(tmp_path):
    def options(d):  # 5 Tokyo nights would need distinct attractions; use 2 Tokyo full days via 6-night trip
        d["attractions"] += [
            dict(d["attractions"][0], attraction_id="ATT-T2", price_per_person=INR(100), closed_dates=[],
                 open_days=["MON"]),  # 2027-04-12 is a Monday
            dict(d["attractions"][0], attraction_id="ATT-T3", price_per_person=INR(50), closed_dates=[]),
        ]
        d["attractions"][0]["closed_dates"] = []
    cat = load(tmp_path, options)
    r = build(cat, request(nights=4))
    visits = [i["catalog_id"] for i in role(r["trip"], "attraction_visit") if i["city"] == "Tokyo"]
    assert visits == ["ATT-T3"]  # one per full day, cheapest open option


# ------------------------------------------------------------------ requests / errors

@pytest.mark.parametrize("bad", [
    {"nights": True}, {"adults": 0}, {"children_ages": [18]}, {"budget_inr": -1}, {"nights": 11},
    {"destination_country": "France"}, {"departure_city": "XYZ"}, {"start_date": "2027-02-30"},
])
def test_invalid_requests_raise(base, bad):
    with pytest.raises(InvalidRequestError):
        build_trip(base, request(**bad), "t")


@pytest.mark.parametrize("start, nights", [("2027-03-31", 3), ("2027-04-18", 3)])
def test_outside_window_is_no_package(base, start, nights):
    r = build(base, request(start_date=start, nights=nights))
    assert r["status"] == "NO_PACKAGE" and r["reason_code"] == "OUTSIDE_DATA_WINDOW"


# ------------------------------------------------------------------ exact optimality vs brute force

@pytest.mark.parametrize("seed", range(60))
def test_matches_independent_brute_force(tmp_path, seed):
    data, req = random_catalog(seed)
    cat = load_catalog(write_catalog(tmp_path, data))
    oracle = brute_force_cheapest(cat, req)
    result = build(cat, req)
    if oracle is None:
        assert result["status"] == "NO_PACKAGE"
        return
    assert result["status"] == "BUILT" and result["trip"]["total_inr"] == oracle[0]
    tight = build(cat, dict(req, budget_inr=oracle[0] - 1)) if oracle[0] > 0 else None
    if tight:
        assert tight["status"] == "OVER_BUDGET" and tight["cheapest_total_inr"] == oracle[0]


# ------------------------------------------------------------------ official catalog

@pytest.fixture(scope="module")
def official():
    return Domain(load_catalog(OFFICIAL_CATALOG_DIR))


def test_menon_request_official(official):
    req = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 6,
           "adults": 2, "children_ages": [6], "budget_inr": 350000}
    r = official.build(req, "menon")
    assert build_result_shape_errors(r, req, "menon") == []
    assert r["status"] == "BUILT" and official.validate(r["trip"], "initial") == []
    assert r["trip"]["total_inr"] <= 350000


def test_official_single_swaps_never_cheaper(official):
    """Local check of global optimality on real data: no valid single-component swap is cheaper."""
    from app.domain.pricing import price_trip
    req = {"departure_city": "DEL", "destination_country": "United Arab Emirates", "start_date": "2027-04-12",
           "nights": 4, "adults": 2, "children_ages": [9], "budget_inr": 10 ** 8}
    r = official.build(req, "swap")
    assert r["status"] == "BUILT"
    trip, cat = r["trip"], official.catalog
    best = trip["total_inr"]
    for idx, item in enumerate(trip["items"]):
        if item["role"] == "hotel_stay":
            pool = [(h["hotel_id"], rm["room_id"]) for h in cat.hotels_by_city[item["city"]]
                    for rm in cat.rooms_by_hotel[h["hotel_id"]]]
            swaps = [{"catalog_id": h, "room_id": rm, "quantity": k} for h, rm in pool for k in (1, 2)]
        elif item["role"] == "attraction_visit":
            swaps = [{"catalog_id": a["attraction_id"]} for a in cat.attractions_by_city[item["city"]]]
        else:
            continue
        for swap in swaps:
            alt = copy.deepcopy(trip)
            alt["items"][idx].update(swap)
            priced = price_trip(alt, cat)
            for it in alt["items"]:
                if it["item_id"] in priced["items"]:
                    it["quantity"], it["price_inr"] = priced["items"][it["item_id"]]
            alt["total_inr"] = sum(i["price_inr"] for i in alt["items"])
            if alt["total_inr"] < best:
                assert official.validate(alt, "initial") != [], f"cheaper valid swap {swap} at {item['item_id']}"


@pytest.mark.parametrize("home", ["BLR", "DEL", "BOM", "MAA"])
@pytest.mark.parametrize("country", ["Japan", "United Arab Emirates"])
def test_SY17_many_official_requests_are_consistent(official, home, country):
    for start, nights in (("2027-04-02", 3), ("2027-04-06", 5), ("2027-04-10", 10)):
        req = {"departure_city": home, "destination_country": country, "start_date": start, "nights": nights,
               "adults": 2, "children_ages": [6], "budget_inr": 250000}
        r = official.build(req, "x")
        assert build_result_shape_errors(r, req, "x") == []
        if r["trip"]:
            assert official.validate(r["trip"], "initial") == []
        end = date.fromisoformat(start) + timedelta(days=nights)
        assert r["status"] in {"BUILT", "OVER_BUDGET", "NO_PACKAGE"} and end.isoformat() <= "2027-04-20"
