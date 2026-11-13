"""Phase 3 validate_trip: every rule rejects a single targeted mutation of a valid trip.

Base trip (synthetic, see test_phase3_planner): i1 outbound FL-1, i2 arrival taxi, i3 Tokyo hotel,
i4 ATT-T1, i5 train, i6 Kyoto hotel, i7 ATT-K1, i8 KIX bus, i9 return FL-2.
After each mutation prices are re-derived from the catalog so only the targeted rule can fire.
"""

from __future__ import annotations

import copy

import pytest

from app.contracts import InvalidRequestError
from app.domain.catalog import load_catalog
from app.domain.planner import build_trip
from app.domain.pricing import price_trip
from app.domain.validator import validate_trip
from tests.domain_fixtures.catalog_builder import OFFICIAL_CATALOG_DIR, set_availability, synthetic_catalog, write_catalog
from tests.domain_fixtures.oracle import INR

REQ = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 4,
       "adults": 2, "children_ages": [6], "budget_inr": 10 ** 7}


def _extend(d):
    """Extra catalog records used only by mutations (planner-irrelevant decoys)."""
    d["airports"].append({"code": "DEL", "name": "Delhi", "city": "Delhi", "country": "IN", "timezone": "Asia/Kolkata"})
    d["destinations"]["departure_cities"].append({"airport": "DEL", "city": "Delhi"})
    d["flights"] += [
        dict(d["flights"][0], flight_id="FL-LATE", departure_local="2027-04-10T23:50:00",
             arrival_local="2027-04-11T10:30:00"),
        dict(d["flights"][0], flight_id="FL-KIX-IN", to="KIX"),
        dict(d["flights"][0], flight_id="FL-FEW", seats_left=2),
        dict(d["flights"][1], flight_id="FL-EARLY", departure_local="2027-04-13T12:00:00",
             arrival_local="2027-04-13T18:00:00"),
        dict(d["flights"][1], flight_id="FL-DEL", to="DEL"),
    ]
    d["transfers"] += [
        dict(d["transfers"][0], transfer_id="TR-NRT-ASA", area="Asakusa"),
        dict(d["transfers"][2], transfer_id="TR-UNLINKED"),
        dict(d["transfers"][2], transfer_id="TR-KYO-TYO", from_city="Kyoto", to_city="Tokyo"),
    ]
    d["hotels"].append(dict(d["hotels"][0], hotel_id="HTL-T2", name="T2"))
    d["rooms"] += [dict(d["rooms"][0], room_id="RM-T2-A", hotel_id="HTL-T2"),
                   dict(d["rooms"][0], room_id="RM-T1-B", name="Twin B")]
    for rid in ("RM-T2-A", "RM-T1-B"):
        set_availability(d, rid, "2027-04-10", [3, 3, 3, 3])
    d["attractions"].append(dict(d["attractions"][1], attraction_id="ATT-K2", name="Kyoto 2"))


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    data = synthetic_catalog()
    _extend(data)
    cat = load_catalog(write_catalog(tmp_path_factory.mktemp("cat"), data))
    result = build_trip(cat, REQ, "trip-1")
    assert result["status"] == "BUILT" and result["trip"]["total_inr"] == 252000
    return cat, result["trip"]


def item(trip, iid):
    return next(i for i in trip["items"] if i["item_id"] == iid)


def reprice(cat, trip):
    priced = price_trip(trip, cat)
    for it in trip["items"]:
        if it["item_id"] in priced["items"]:
            it["quantity"], it["price_inr"] = priced["items"][it["item_id"]]
    trip["total_inr"] = sum(i["price_inr"] for i in trip["items"])
    return trip


def codes(cat, trip, mode="initial"):
    return {v["code"] for v in validate_trip(cat, trip, mode)}


def mutated(world, fn, mode="initial"):
    cat, trip = world
    t = copy.deepcopy(trip)
    fn(t)
    return codes(cat, reprice(cat, t), mode)


def test_valid_trip_has_no_violations_in_every_mode(world):
    cat, trip = world
    for mode in ("initial", "modified", "confirm"):
        assert validate_trip(cat, trip, mode) == []


def test_unknown_mode_raises(world):
    with pytest.raises(InvalidRequestError):
        validate_trip(*world, "draft")


def test_validate_ignores_booking_metadata(world):
    cat, trip = world
    booked = copy.deepcopy(trip)
    booked["status"] = "BOOKED"
    for it in booked["items"]:
        it.update(status="BOOKED", booking={"reference": "0123456789"})
    assert validate_trip(cat, booked, "confirm") == []


def test_malformed_trip_returns_violations_not_exceptions(world):
    cat, _ = world
    assert {v["code"] for v in validate_trip(cat, {"items": []}, "initial")} == {"TRIP_MALFORMED"}


def test_tampered_price_rejected(world):
    cat, trip = world
    t = copy.deepcopy(trip)
    item(t, "i3")["price_inr"] -= 1000
    t["total_inr"] -= 1000
    assert codes(cat, t) == {"PRICE_MISMATCH"}


# ------------------------------------------------------------------ CT

def test_CT01_CT03_city_with_too_few_nights(world):
    def short_tokyo(t):  # Tokyo 1 night + Kyoto 3 nights
        item(t, "i3")["end"] = "2027-04-11"
        item(t, "i6")["start"] = "2027-04-11"
        item(t, "i5").update(start="2027-04-11T09:00", end="2027-04-11T11:15")
        t["items"].remove(item(t, "i4"))
    assert mutated(world, short_tokyo, "modified") == {"CITY_MIN_NIGHTS"}


def test_CT04_HT08_same_city_twice_rejected(world):
    def tokyo_again(t):  # Tokyo -> Tokyo (second Tokyo hotel) replaces Kyoto
        item(t, "i6").update(catalog_id="HTL-T2", room_id="RM-T2-A", city="Tokyo")
    found = mutated(world, tokyo_again)
    assert "CITY_REPEATED" in found


def test_HT09_room_type_change_mid_city_rejected(world):
    def split(t):
        item(t, "i3")["end"] = "2027-04-11"
        extra = copy.deepcopy(item(t, "i3"))
        extra.update(item_id="i3b", room_id="RM-T1-B", start="2027-04-11", end="2027-04-12")
        t["items"].insert(3, extra)
    found = mutated(world, split)
    assert {"CITY_REPEATED", "CITY_MIN_NIGHTS"} <= found


def test_CT05_reverse_link_missing(world):
    def reverse(t):  # swap the two hotels' cities/dates: Kyoto first, then Tokyo
        tokyo, kyoto = item(t, "i3"), item(t, "i6")
        tokyo["start"], tokyo["end"], kyoto["start"], kyoto["end"] = "2027-04-12", "2027-04-14", "2027-04-10", "2027-04-12"
    assert "NO_CITY_LINK" in mutated(world, reverse)


def test_CT06_city_of_another_country_rejected():
    cat = load_catalog(OFFICIAL_CATALOG_DIR)
    req = dict(REQ, nights=6, budget_inr=10 ** 7)
    trip = build_trip(cat, req, "x")["trip"]
    hotel = next(i for i in trip["items"] if i["role"] == "hotel_stay")
    dubai = cat.hotels_by_city["Dubai"][0]
    hotel.update(catalog_id=dubai["hotel_id"], room_id=cat.rooms_by_hotel[dubai["hotel_id"]][-1]["room_id"],
                 city="Dubai")
    found = codes(cat, reprice(cat, trip))
    assert "CITY_NOT_IN_COUNTRY" in found


def test_CT07_missing_intercity_on_change_day(world):
    assert "INTERCITY_MISSING" in mutated(world, lambda t: t["items"].remove(item(t, "i5")))


def test_CT08_hotel_night_gap(world):
    def gap(t):
        item(t, "i6")["start"] = "2027-04-13"
    assert "HOTEL_GAP_OVERLAP" in mutated(world, gap)


def test_CT08_overlap(world):
    def overlap(t):
        item(t, "i3")["end"] = "2027-04-13"
    assert "HOTEL_GAP_OVERLAP" in mutated(world, overlap)


def test_CT09_nara_attraction_rejected(world):
    def nara(t):
        item(t, "i7").update(catalog_id="ATT-N1", city="Nara")
    assert "ATTRACTION_WRONG_CITY" in mutated(world, nara)


# ------------------------------------------------------------------ FL

def test_FL02_arrival_local_date_after_start(world):
    def late(t):
        item(t, "i1").update(catalog_id="FL-LATE", start="2027-04-10T23:50", end="2027-04-11T10:30")
    assert "FLIGHT_WRONG_DATE" in mutated(world, late)


def test_FL03_return_departs_day_before_end(world):
    def early(t):
        item(t, "i9").update(catalog_id="FL-EARLY", start="2027-04-13T12:00", end="2027-04-13T18:00")
    assert "FLIGHT_WRONG_DATE" in mutated(world, early)


def test_FL05_insufficient_seats(world):
    assert "FLIGHT_SEATS" in mutated(world, lambda t: item(t, "i1").update(catalog_id="FL-FEW"))


def test_FL08_arrival_airport_not_serving_first_city(world):
    assert "FLIGHT_WRONG_AIRPORT" in mutated(world, lambda t: item(t, "i1").update(catalog_id="FL-KIX-IN"))


def test_FL09_return_to_other_airport(world):
    assert "FLIGHT_WRONG_ROUTE" in mutated(world, lambda t: item(t, "i9").update(catalog_id="FL-DEL"))


def test_flight_times_must_match_catalog(world):
    assert mutated(world, lambda t: item(t, "i1").update(start="2027-04-09T23:00")) == {"FLIGHT_TIME_MISMATCH"}


def test_missing_return_flight(world):
    assert "FLIGHT_COUNT" in mutated(world, lambda t: t["items"].remove(item(t, "i9")))


# ------------------------------------------------------------------ TR

def test_TR01_TR02_sixty_minute_boundary(world):
    assert mutated(world, lambda t: item(t, "i2").update(start="2027-04-10T11:30", end="2027-04-10T12:45")) == set()
    assert mutated(world, lambda t: item(t, "i2").update(start="2027-04-10T11:29", end="2027-04-10T12:44")) == {
        "TRANSFER_TOO_EARLY"}


def test_TR04_TR05_TR06_180_minute_boundary(world):
    # FL-2 departs 12:00; KIX bus takes 90 min -> latest pickup 07:30 (arrive 09:00 = exactly 180 min).
    assert mutated(world, lambda t: item(t, "i8").update(start="2027-04-14T07:30", end="2027-04-14T09:00")) == set()
    late = mutated(world, lambda t: item(t, "i8").update(start="2027-04-14T07:31", end="2027-04-14T09:01"))
    assert late == {"TRANSFER_LATE_FOR_FLIGHT"}  # 07:31 is inside service hours: hours alone are not enough


def test_TR03_overnight_service_window_validated(world):
    # Pickup outside the 06:00-00:30 bus window (01:00) is rejected; 00:30 next day is legal.
    found = mutated(world, lambda t: item(t, "i8").update(start="2027-04-14T01:00", end="2027-04-14T02:30"))
    assert found == {"TRANSFER_OUT_OF_SERVICE"}
    ok = mutated(world, lambda t: item(t, "i8").update(start="2027-04-14T00:30", end="2027-04-14T02:00"))
    assert ok == set()


def test_TR07_wrong_area_transfer(world):
    assert mutated(world, lambda t: item(t, "i2").update(catalog_id="TR-NRT-ASA")) == {"TRANSFER_WRONG_AREA"}


def test_TR08_TR15_null_area_used_in_reverse(world):
    cat, trip = world
    assert item(trip, "i8")["catalog_id"] == "TR-KIX-KYO" and cat.transfers["TR-KIX-KYO"]["area"] is None
    assert validate_trip(cat, trip, "initial") == []


def test_TR11_unlinked_intercity_id(world):
    assert mutated(world, lambda t: item(t, "i5").update(catalog_id="TR-UNLINKED")) == {"INTERCITY_NOT_LINKED"}


def test_TR12_wrong_direction_intercity(world):
    assert mutated(world, lambda t: item(t, "i5").update(catalog_id="TR-KYO-TYO")) == {"INTERCITY_WRONG_DIRECTION"}


def test_TR13_off_schedule_intercity_departure(world):
    found = mutated(world, lambda t: item(t, "i5").update(start="2027-04-12T09:10", end="2027-04-12T11:25"))
    assert found == {"TRANSFER_OUT_OF_SERVICE"}


def test_TR14_attraction_during_intercity_transfer(world):
    def during(t):  # train 09:00-11:15 on change day; Kyoto visit at 10:00 starts before arrival
        t["items"].append(dict(copy.deepcopy(item(t, "i7")), item_id="i10", catalog_id="ATT-K2",
                               start="2027-04-12T10:00", end="2027-04-12T11:00"))
    assert mutated(world, during, mode="modified") == {"ATTRACTION_TIMELINE"}

    def after(t):
        t["items"].append(dict(copy.deepcopy(item(t, "i7")), item_id="i10", catalog_id="ATT-K2",
                               start="2027-04-12T11:15", end="2027-04-12T12:15"))
    assert mutated(world, after, mode="modified") == set()


def test_TR17_optional_city_taxi_validated(world):
    def taxi(city_tid):
        def m(t):
            t["items"].insert(4, {**copy.deepcopy(item(t, "i2")), "item_id": "i4b", "role": "city_taxi",
                                  "catalog_id": city_tid, "start": "2027-04-12T08:00", "end": "2027-04-12T08:25"})
        return m
    assert mutated(world, taxi("TR-TYO-TAXI")) == set()
    assert mutated(world, taxi("TR-NRT-GNZ")) == {"CITY_TAXI_INVALID"}


# ------------------------------------------------------------------ HT-10

def test_HT10_hotel_from_other_city(world):
    def wrong(t):
        item(t, "i6").update(catalog_id="HTL-T2", room_id="RM-T2-A")  # Tokyo hotel, item says Kyoto
    assert "HOTEL_WRONG_CITY" in mutated(world, wrong)


# ------------------------------------------------------------------ attractions / modes

def test_attraction_rules(world):
    # ATT-T1 opens 09:00-22:00, visit 90 min: 21:00 start ends 22:30 (too late); 20:30 fits exactly.
    assert mutated(world, lambda t: item(t, "i4").update(start="2027-04-11T21:00", end="2027-04-11T22:30")) == {
        "ATTRACTION_CLOSED"}
    assert mutated(world, lambda t: item(t, "i4").update(start="2027-04-11T20:30", end="2027-04-11T22:00")) == set()
    dup = mutated(world, lambda t: t["items"].append(dict(copy.deepcopy(item(t, "i7")), item_id="i10",
                                                          start="2027-04-13T12:00", end="2027-04-13T13:00")))
    assert "ATTRACTION_DUPLICATE" in dup
    overlap = mutated(world, lambda t: t["items"].append(dict(copy.deepcopy(item(t, "i7")), item_id="i10",
                                                              catalog_id="ATT-K2", start="2027-04-13T10:30",
                                                              end="2027-04-13T11:30")))
    assert overlap == {"ATTRACTION_OVERLAP"}
    adjacent = mutated(world, lambda t: t["items"].append(dict(copy.deepcopy(item(t, "i7")), item_id="i10",
                                                               catalog_id="ATT-K2", start="2027-04-13T11:00",
                                                               end="2027-04-13T12:00")))
    assert adjacent == set()


def test_full_day_without_attraction_initial_vs_modified(world):
    def remove(t):
        t["items"].remove(item(t, "i7"))
    assert mutated(world, remove, "initial") == {"ATTRACTION_COUNT"}
    assert mutated(world, remove, "modified") == set()
    assert mutated(world, remove, "confirm") == set()


def test_return_day_attraction_rejected(world):
    def ret(t):
        t["items"].append(dict(copy.deepcopy(item(t, "i7")), item_id="i10", catalog_id="ATT-K2",
                               start="2027-04-14T06:00", end="2027-04-14T07:00"))
    assert "ATTRACTION_COUNT" in mutated(world, ret, "modified")


def test_over_budget_trip_rejected(world):
    cat, trip = world
    t = copy.deepcopy(trip)
    t["budget_inr"] = t["total_inr"] - 1
    assert codes(cat, t) == {"OVER_BUDGET"}


def test_SY14_end_date_minus_one_rejected(world):
    cat, trip = world
    t = copy.deepcopy(trip)
    t["end_date"] = "2027-04-13"
    assert {"HOTEL_GAP_OVERLAP", "FLIGHT_WRONG_DATE"} <= codes(cat, t)
