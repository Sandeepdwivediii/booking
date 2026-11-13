"""Phase 5: change() and alternatives() on the synthetic base trip (ED-01..08, PR-16).

Base trip (see test_phase3_planner, total 252,000): i1 FL-1 outbound (lands NRT 10:30), i2 NRT taxi
(Ginza), i3 Tokyo HTL-T1/RM-T1-A x2, i4 ATT-T1 04-11, i5 train 04-12 09:00, i6 Kyoto HTL-K1/RM-K1-A,
i7 ATT-K1 04-13 10:00-11:00, i8 KIX bus (area null), i9 FL-2 return 04-14 12:00.
Every test also asserts the input trip is untouched and the result passes the contract checker.
"""

from __future__ import annotations

import copy

import pytest

from app.contracts import InvalidRequestError, alternatives_shape_errors, change_result_shape_errors
from app.domain.adapter import Domain
from app.domain.catalog import load_catalog
from app.domain.pricing import price_trip
from tests.domain_fixtures.catalog_builder import OFFICIAL_CATALOG_DIR, set_availability, synthetic_catalog, write_catalog
from tests.domain_fixtures.oracle import INR

REQ = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 4,
       "adults": 2, "children_ages": [6], "budget_inr": 10 ** 7}


def _extend(d):
    hotel, room = d["hotels"][0], d["rooms"][0]
    d["hotels"] += [dict(hotel, hotel_id="HTL-T2", name="Asakusa Inn", area="Asakusa"),  # no NRT transfer
                    dict(hotel, hotel_id="HTL-T3", name="Shinjuku Stay", area="Shinjuku")]
    d["rooms"] += [
        dict(room, room_id="RM-T2-A", hotel_id="HTL-T2", max_adults=2, max_children=2, price_per_night=INR(5000)),
        dict(room, room_id="RM-T3-A", hotel_id="HTL-T3", max_adults=2, max_children=2, price_per_night=INR(7000)),
        dict(room, room_id="RM-T1-S", name="Single", max_adults=1, max_children=0, price_per_night=INR(1000)),
        dict(room, room_id="RM-T1-F", name="Family", max_adults=2, max_children=2, price_per_night=INR(3000)),
    ]
    for rid in ("RM-T2-A", "RM-T3-A", "RM-T1-S"):
        set_availability(d, rid, "2027-04-10", [3, 3, 3, 3])
    set_availability(d, "RM-T1-F", "2027-04-10", [2, 0, 2, 2])  # one night sold out
    d["transfers"].append(dict(d["transfers"][0], transfer_id="TR-NRT-SJK", name="NRT taxi Shinjuku",
                               area="Shinjuku", max_passengers=2, price=INR(5000)))  # 3 travelers -> 2 vehicles
    base_out, base_ret = d["flights"][0], d["flights"][1]
    d["flights"] += [
        dict(base_out, flight_id="FL-1B", departure_local="2027-04-09T20:00:00", arrival_local="2027-04-10T07:00:00",
             price_per_seat=INR(26000)),
        dict(base_out, flight_id="FL-LATE", departure_local="2027-04-10T23:50:00",
             arrival_local="2027-04-11T10:30:00"),
        dict(base_out, flight_id="FL-KIX-IN", to="KIX"),
        dict(base_out, flight_id="FL-FEW", seats_left=2),
        dict(base_ret, flight_id="FL-2-EARLY", departure_local="2027-04-14T01:00:00",
             arrival_local="2027-04-14T07:00:00"),
    ]
    k1 = d["attractions"][1]
    d["attractions"] += [dict(k1, attraction_id="ATT-K2", name="Kyoto 2"),
                         dict(k1, attraction_id="ATT-K3", name="Kyoto 3", closed_dates=["2027-04-13"]),
                         dict(k1, attraction_id="ATT-K4", name="Kyoto 4")]


@pytest.fixture(scope="module")
def domain(tmp_path_factory):
    data = synthetic_catalog()
    _extend(data)
    return Domain(load_catalog(write_catalog(tmp_path_factory.mktemp("cat"), data)))


@pytest.fixture(scope="module")
def base_trip(domain, tmp_path_factory):
    """Known base trip built on the plain catalog (the extended one has cheaper decoys on purpose)."""
    plain = Domain(load_catalog(write_catalog(tmp_path_factory.mktemp("plain"), synthetic_catalog())))
    result = plain.build(REQ, "trip-1")
    assert result["status"] == "BUILT" and result["trip"]["total_inr"] == 252000
    assert domain.validate(result["trip"], "initial") == []  # still valid against the extended catalog
    return result["trip"]


@pytest.fixture
def trip(base_trip):
    return copy.deepcopy(base_trip)


def run(domain, trip, change):
    """Apply a change and check atomicity + contract shape every time."""
    before = copy.deepcopy(trip)
    result = domain.change(trip, change)
    assert trip == before, "input trip was mutated"
    assert change_result_shape_errors(result, trip) == []
    return result


def codes(result):
    assert result["ok"] is False and result["trip"] is None
    return {v["code"] for v in result["violations"]}


def item(trip, iid):
    return next(i for i in trip["items"] if i["item_id"] == iid)


# ------------------------------------------------------------------ successful changes

def test_replace_hotel_reselects_airport_transfer_and_reprices(domain, trip):
    r = run(domain, trip, {"op": "replace_hotel", "item_id": "i3", "hotel_id": "HTL-T3", "room_id": "RM-T3-A"})
    assert r["ok"], r["violations"]
    new = r["trip"]
    hotel = item(new, "i3")
    assert (hotel["catalog_id"], hotel["room_id"], hotel["quantity"], hotel["price_inr"]) == (
        "HTL-T3", "RM-T3-A", 1, 14000)
    arrival = item(new, "i2")
    assert arrival["catalog_id"] == "TR-NRT-SJK" and arrival["start"] == "2027-04-10T11:30"
    assert any("TR-NRT-SJK" in a for a in r["adjustments"])
    assert domain.validate(new, "modified") == []


def test_PR16_vehicle_ceiling_and_full_reprice_after_change(domain, trip):
    stale = copy.deepcopy(trip)
    item(stale, "i4")["price_inr"] += 999  # stale line item in the stored draft
    stale["total_inr"] += 999
    r = run(domain, stale, {"op": "replace_hotel", "item_id": "i3", "hotel_id": "HTL-T3", "room_id": "RM-T3-A"})
    assert r["ok"], r["violations"]
    new = r["trip"]
    assert item(new, "i2")["quantity"] == 2 and item(new, "i2")["price_inr"] == 10000  # ceil(3/2) vehicles
    assert item(new, "i4")["price_inr"] == 3000  # stale price not preserved
    priced = price_trip(new, domain.catalog)
    assert all((i["quantity"], i["price_inr"]) == priced["items"][i["item_id"]] for i in new["items"])
    assert new["total_inr"] == priced["total_inr"] == 252000 - 24000 + 14000 - 13000 + 10000


def test_replace_room_in_same_hotel(domain, trip):
    r = run(domain, trip, {"op": "replace_hotel", "item_id": "i3", "hotel_id": "HTL-T1", "room_id": "RM-T1-S"})
    assert codes(r) == {"ROOM_CAPACITY"}  # single rooms cannot hold 2A+1C (adults >= k)


def test_replace_outbound_flight_retimes_arrival_transfer(domain, trip):
    r = run(domain, trip, {"op": "replace_flight", "item_id": "i1", "flight_id": "FL-1B"})
    assert r["ok"], r["violations"]
    assert item(r["trip"], "i1")["end"] == "2027-04-10T07:00"
    assert item(r["trip"], "i2")["start"] == "2027-04-10T08:00"  # same taxi, earliest legal pickup
    assert r["adjustments"] == ["Arrival airport transfer NRT taxi pickup moved from 2027-04-10T11:30 to "
                                "2027-04-10T08:00."]
    assert r["trip"]["total_inr"] == 252000 + 3 * 1000


def test_early_return_flight_with_day_before_pickup_is_legal_and_warned(domain, trip):
    r = run(domain, trip, {"op": "replace_flight", "item_id": "i9", "flight_id": "FL-2-EARLY"})
    assert r["ok"], r["violations"]
    dep = item(r["trip"], "i8")
    assert dep["start"] == "2027-04-13T20:30" and dep["warnings"]  # 01:00 flight: 180 min + 90 min bus


def test_add_and_remove_attraction(domain, trip):
    r = run(domain, trip, {"op": "add_attraction", "attraction_id": "ATT-K2", "date": "2027-04-13"})
    assert r["ok"], r["violations"]
    added = next(i for i in r["trip"]["items"] if i["catalog_id"] == "ATT-K2")
    assert added["start"] == "2027-04-13T09:00"  # earliest from 09:00; 09:00-10:00 just touches ATT-K1 10:00-11:00
    r2 = run(domain, r["trip"], {"op": "remove_attraction", "item_id": added["item_id"]})
    assert r2["ok"] and r2["trip"]["items"] == trip["items"]


def test_remove_sole_attraction_of_full_day_allowed_after_build(domain, trip):  # AT-15
    r = run(domain, trip, {"op": "remove_attraction", "item_id": "i7"})
    assert r["ok"] and r["trip"]["total_inr"] == trip["total_inr"]


def test_change_day_attraction_after_arrival(domain, trip):
    r = run(domain, trip, {"op": "add_attraction", "attraction_id": "ATT-K2", "date": "2027-04-12"})
    assert r["ok"]
    added = next(i for i in r["trip"]["items"] if i["catalog_id"] == "ATT-K2")
    assert added["start"] == "2027-04-12T11:15"  # train arrives 11:15
    ids = [i["item_id"] for i in r["trip"]["items"]]
    assert ids.index("i5") < ids.index("i6") < ids.index(added["item_id"]) < ids.index("i7")  # chronological


# ------------------------------------------------------------------ ED-01..08

def test_ED01_new_hotel_area_without_airport_transfer(domain, trip):
    r = run(domain, trip, {"op": "replace_hotel", "item_id": "i3", "hotel_id": "HTL-T2", "room_id": "RM-T2-A"})
    assert "NO_AIRPORT_TRANSFER" in codes(r) and not {c for c in codes(r) if c.startswith("TRANSFER_")}


def test_ED02_room_capacity_or_one_night_unavailable(domain, trip):
    cap = run(domain, trip, {"op": "replace_hotel", "item_id": "i3", "hotel_id": "HTL-T1", "room_id": "RM-T1-S"})
    assert codes(cap) == {"ROOM_CAPACITY"}
    stock = run(domain, trip, {"op": "replace_hotel", "item_id": "i3", "hotel_id": "HTL-T1", "room_id": "RM-T1-F"})
    assert codes(stock) == {"HOTEL_NO_AVAILABILITY"}


@pytest.mark.parametrize("flight_id, expected", [
    ("FL-LATE", "FLIGHT_WRONG_DATE"), ("FL-KIX-IN", "FLIGHT_WRONG_AIRPORT"), ("FL-FEW", "FLIGHT_SEATS")])
def test_ED03_bad_arrival_flight(domain, trip, flight_id, expected):
    r = run(domain, trip, {"op": "replace_flight", "item_id": "i1", "flight_id": flight_id})
    assert expected in codes(r)


def test_ED04_return_flight_too_early_for_180_minute_margin(domain, trip):
    late_visit = run(domain, trip, {"op": "add_attraction", "attraction_id": "ATT-K2", "date": "2027-04-13",
                                    "start_time": "21:00"})
    assert late_visit["ok"]
    r = run(domain, late_visit["trip"], {"op": "replace_flight", "item_id": "i9", "flight_id": "FL-2-EARLY"})
    # 01:00 departure needs pickup by 20:30 the day before, but a visit runs until 22:00.
    assert codes(r) == {"ATTRACTION_TIMELINE"}


@pytest.mark.parametrize("change, expected", [
    ({"attraction_id": "ATT-T1", "date": "2027-04-13"}, "ATTRACTION_WRONG_CITY"),
    ({"attraction_id": "ATT-K3", "date": "2027-04-13"}, "ATTRACTION_CLOSED"),
    ({"attraction_id": "ATT-N1", "date": "2027-04-13"}, "ATTRACTION_WRONG_CITY"),
    ({"attraction_id": "ATT-K2", "date": "2027-04-14"}, "ATTRACTION_COUNT"),  # return day
])
def test_ED05_attraction_wrong_city_or_closed(domain, trip, change, expected):
    assert expected in codes(run(domain, trip, {"op": "add_attraction", **change}))


def test_ED06_overlapping_attraction(domain, trip):
    r = run(domain, trip, {"op": "add_attraction", "attraction_id": "ATT-K2", "date": "2027-04-13",
                           "start_time": "10:30"})
    assert codes(r) == {"ATTRACTION_OVERLAP"}


def test_ED07_duplicate_attraction(domain, trip):
    r = run(domain, trip, {"op": "add_attraction", "attraction_id": "ATT-K1", "date": "2027-04-13",
                           "start_time": "12:00"})
    assert codes(r) == {"ATTRACTION_DUPLICATE"}


def test_ED08_remove_missing_or_non_attraction_item(domain, trip):
    assert codes(run(domain, trip, {"op": "remove_attraction", "item_id": "i99"})) == {"ITEM_NOT_FOUND"}
    assert codes(run(domain, trip, {"op": "remove_attraction", "item_id": "i3"})) == {"ITEM_NOT_REMOVABLE"}


def test_third_attraction_on_a_day_rejected(domain, trip):
    r = run(domain, trip, {"op": "add_attraction", "attraction_id": "ATT-K2", "date": "2027-04-13"})
    r = run(domain, r["trip"], {"op": "add_attraction", "attraction_id": "ATT-K4", "date": "2027-04-13"})
    assert codes(r) == {"ATTRACTION_COUNT"}


def test_over_budget_edit_rejected(domain, trip):
    tight = dict(copy.deepcopy(trip), budget_inr=trip["total_inr"])
    r = run(domain, tight, {"op": "replace_flight", "item_id": "i1", "flight_id": "FL-1B"})  # +3,000
    assert codes(r) == {"OVER_BUDGET"}


@pytest.mark.parametrize("change, expected", [
    ({"op": "teleport"}, "UNKNOWN_OP"), ({"op": "replace_flight", "item_id": "i1"}, "CHANGE_MALFORMED"),
    ({"op": "add_attraction", "attraction_id": "ATT-K2", "date": "13/04/2027"}, "CHANGE_MALFORMED"),
    ({"op": "add_attraction", "attraction_id": "ATT-K2", "date": "2027-04-13", "start_time": "25:00"},
     "CHANGE_MALFORMED"),
    ({"op": "replace_flight", "item_id": "i1", "flight_id": "FL-1"}, "NO_CHANGE"),
    ({"op": "replace_flight", "item_id": "i3", "flight_id": "FL-1B"}, "ITEM_WRONG_TYPE"),
    ({"op": "replace_hotel", "item_id": "i3", "hotel_id": "HTL-T1", "room_id": "RM-K1-A"}, "ROOM_HOTEL_MISMATCH"),
    ({"op": "replace_hotel", "item_id": "i6", "hotel_id": "HTL-T3", "room_id": "RM-T3-A"}, "HOTEL_WRONG_CITY"),
    ({"op": "add_attraction", "attraction_id": "ATT-NOPE", "date": "2027-04-13"}, "UNKNOWN_CATALOG_ID"),
])
def test_rejections_have_stable_codes(domain, trip, change, expected):
    assert expected in codes(run(domain, trip, change))


def test_booked_or_malformed_trip_rejected_without_exception(domain, trip):
    booked = copy.deepcopy(trip)
    for i in booked["items"]:
        i.update(status="BOOKED", booking={"reference": "0123456789"})
    assert codes(domain.change(booked, {"op": "remove_attraction", "item_id": "i7"})) == {"TRIP_BOOKED"}
    assert codes(domain.change({"items": []}, {"op": "remove_attraction", "item_id": "i7"})) == {"TRIP_MALFORMED"}


# ------------------------------------------------------------------ alternatives

def test_hotel_alternatives_only_list_successful_changes(domain, trip):
    alts = domain.alternatives(trip, "hotel", {"item_id": "i3"})
    assert alternatives_shape_errors(alts, "hotel") == []
    listed = {(a["catalog_id"], a["room_id"]) for a in alts}
    assert ("HTL-T3", "RM-T3-A") in listed
    assert not listed & {("HTL-T2", "RM-T2-A"), ("HTL-T1", "RM-T1-S"), ("HTL-T1", "RM-T1-F"), ("HTL-T1", "RM-T1-A")}
    assert [a["new_total_inr"] for a in alts] == sorted(a["new_total_inr"] for a in alts)
    for a in alts:
        result = domain.change(trip, a["change"])
        assert result["ok"] and result["trip"]["total_inr"] == a["new_total_inr"]
        assert a["price_diff_inr"] == a["new_total_inr"] - trip["total_inr"]
    shinjuku = next(a for a in alts if a["catalog_id"] == "HTL-T3")
    assert shinjuku["price_inr"] == 14000 and shinjuku["price_diff_inr"] == -24000 + 14000 - 13000 + 10000


def test_flight_alternatives(domain, trip):
    out = domain.alternatives(trip, "outbound_flight", {})
    assert alternatives_shape_errors(out, "outbound_flight") == []
    assert [a["catalog_id"] for a in out] == ["FL-1B"] and out[0]["price_diff_inr"] == 3000
    ret = domain.alternatives(trip, "return_flight", {})
    assert [a["catalog_id"] for a in ret] == ["FL-2-EARLY"]


def test_attraction_alternatives_include_start_time(domain, trip):
    alts = domain.alternatives(trip, "attraction", {"date": "2027-04-13"})
    assert alternatives_shape_errors(alts, "attraction") == []
    assert {a["catalog_id"] for a in alts} == {"ATT-K2", "ATT-K4"}  # K1 taken, K3 closed, others wrong city
    for a in alts:
        assert a["change"]["start_time"] == "09:00"
        assert domain.change(trip, a["change"])["ok"]


def test_alternatives_argument_errors_and_empty_days(domain, trip):
    assert domain.alternatives(trip, "attraction", {"date": "2027-04-14"}) == []  # return day
    assert domain.alternatives(trip, "attraction", {"date": "2027-05-01"}) == []  # outside trip
    for component, filters in (("spa", {}), ("hotel", {}), ("hotel", {"item_id": "i1"}),
                               ("attraction", {"date": "13/04/2027"}), ("hotel", {"item_id": "i3", "limit": 0})):
        with pytest.raises(InvalidRequestError):
            domain.alternatives(trip, component, filters)
    assert len(domain.alternatives(trip, "hotel", {"item_id": "i3", "limit": 1})) == 1


def test_alternatives_do_not_mutate_trip(domain, trip):
    before = copy.deepcopy(trip)
    for component, filters in (("hotel", {"item_id": "i6"}), ("outbound_flight", {}), ("return_flight", {}),
                               ("attraction", {"date": "2027-04-11"})):
        domain.alternatives(trip, component, filters)
    assert trip == before


# ------------------------------------------------------------------ official catalog

def test_official_menon_more_convenient_flight_alternative():
    """Policy: cheapest stays the default; a same-day arrival is offered as an alternative with exact diff."""
    domain = Domain(load_catalog(OFFICIAL_CATALOG_DIR))
    req = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 6,
           "adults": 2, "children_ages": [6], "budget_inr": 350000}
    trip = domain.build(req, "menon")["trip"]
    pickup = next(i for i in trip["items"] if i["role"] == "arrival_transfer")
    assert pickup["warnings"]  # next-day pickup in the cheapest package is disclosed
    alts = domain.alternatives(trip, "outbound_flight", {})
    assert alts and alternatives_shape_errors(alts, "outbound_flight") == []
    for a in alts:
        r = run(domain, trip, a["change"])
        assert r["ok"] and r["trip"]["total_inr"] - trip["total_inr"] == a["price_diff_inr"]
        assert domain.validate(r["trip"], "modified") == []
    same_day = [a for a in alts if not next(i for i in domain.change(trip, a["change"])["trip"]["items"]
                                            if i["role"] == "arrival_transfer")["warnings"]]
    assert same_day  # at least one alternative removes the overnight wait
    hotels = domain.alternatives(trip, "hotel", {"item_id": next(i["item_id"] for i in trip["items"]
                                                                 if i["role"] == "hotel_stay")})
    assert hotels and alternatives_shape_errors(hotels, "hotel") == []
