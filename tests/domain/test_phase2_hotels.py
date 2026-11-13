"""Phase 2 hotel rules: room capacity and nightly availability (HT-01..07, HT-11..14)."""

from __future__ import annotations

import pytest

from app.domain.catalog import load_catalog
from app.domain.rules import min_rooms, room_stay_available, rooms_fit, stay_nights, unavailable_nights
from app.domain.validator import check_hotel_item
from tests.domain_fixtures.catalog_builder import set_availability, synthetic_catalog, write_catalog


def _catalog(tmp_path, mutate=None):
    data = synthetic_catalog()
    if mutate:
        mutate(data)
    return load_catalog(write_catalog(tmp_path, data))


def _hotel_item(check_in, check_out, k, hotel="HTL-T1", room="RM-T1-A"):
    return {"item_id": "h1", "type": "hotel", "catalog_id": hotel, "room_id": room,
            "start": check_in, "end": check_out, "quantity": k}


def _codes(violations):
    return [v["code"] for v in violations]


# ------------------------------------------------------------------ availability

def test_HT01_one_zero_night_rejects_whole_stay(tmp_path):
    cat = _catalog(tmp_path, lambda d: set_availability(d, "RM-K1-A", "2027-04-10", [5, 5, 5, 5, 5, 0]))
    assert not room_stay_available(cat, "RM-K1-A", "2027-04-10", "2027-04-16", 1)
    assert unavailable_nights(cat, "RM-K1-A", "2027-04-10", "2027-04-16", 1) == ["2027-04-15"]
    v = check_hotel_item(_hotel_item("2027-04-10", "2027-04-16", 1, "HTL-K1", "RM-K1-A"), cat, 2, 1)
    assert _codes(v) == ["HOTEL_NO_AVAILABILITY"]


def test_HT02_missing_row_is_unavailable(tmp_path):
    cat = _catalog(tmp_path, lambda d: set_availability(d, "RM-K1-A", "2027-04-10", [3, 3, None, 3]))
    assert cat.rooms_left("RM-K1-A", "2027-04-12") is None
    assert unavailable_nights(cat, "RM-K1-A", "2027-04-10", "2027-04-14", 1) == ["2027-04-12"]
    v = check_hotel_item(_hotel_item("2027-04-10", "2027-04-14", 1, "HTL-K1", "RM-K1-A"), cat, 2, 0)
    assert _codes(v) == ["HOTEL_NO_AVAILABILITY"]


def test_HT03_minimum_night_stock_below_rooms_needed(tmp_path):
    cat = _catalog(tmp_path, lambda d: set_availability(d, "RM-T1-A", "2027-04-10", [2, 2, 1, 3]))
    assert unavailable_nights(cat, "RM-T1-A", "2027-04-10", "2027-04-14", 2) == ["2027-04-12"]
    assert _codes(check_hotel_item(_hotel_item("2027-04-10", "2027-04-14", 2), cat, 4, 0)) == ["HOTEL_NO_AVAILABILITY"]


def test_HT04_exact_stock_equal_to_rooms_is_eligible(tmp_path):
    cat = _catalog(tmp_path, lambda d: set_availability(d, "RM-T1-A", "2027-04-10", [2, 2, 2, 2]))
    assert room_stay_available(cat, "RM-T1-A", "2027-04-10", "2027-04-14", 2)
    assert check_hotel_item(_hotel_item("2027-04-10", "2027-04-14", 2), cat, 4, 0) == []
    assert not room_stay_available(cat, "RM-T1-A", "2027-04-10", "2027-04-14", 3)


# ------------------------------------------------------------------ occupancy

ROOM_2A_1C = {"max_adults": 2, "max_children": 1}
ROOM_1A_1C = {"max_adults": 1, "max_children": 1}
TWIN_2A_0C = {"max_adults": 2, "max_children": 0}
FAMILY_2A_2C = {"max_adults": 2, "max_children": 2}


def test_HT05_one_room_total_capacity_exceeded():
    assert not rooms_fit(2, 2, 1, ROOM_2A_1C)  # 4 travelers > 3
    assert rooms_fit(2, 2, 2, ROOM_2A_1C)


def test_HT05_validator_rejects_capacity(tmp_path):
    cat = _catalog(tmp_path, lambda d: d["rooms"][1].update({"max_children": 1}))
    v = check_hotel_item(_hotel_item("2027-04-10", "2027-04-12", 1, "HTL-K1", "RM-K1-A"), cat, 2, 2)
    assert _codes(v) == ["ROOM_CAPACITY"]


def test_HT06_each_room_needs_an_adult():
    # k=2 small rooms: raw capacities fit (1 <= 2 adults, 4 <= 4 people) but adults 1 < k 2.
    assert not rooms_fit(1, 3, 2, ROOM_1A_1C)
    assert not rooms_fit(1, 3, 2, FAMILY_2A_2C)
    assert min_rooms(1, 3, ROOM_1A_1C) is None  # no legal k at all


def test_HT06_one_adult_three_children_one_family_room():
    # 1 adult + 3 children = 4 <= 1 * (2+2) and adults >= 1 -> one family room is legal, two are not.
    assert rooms_fit(1, 3, 1, FAMILY_2A_2C)
    assert not rooms_fit(1, 3, 2, FAMILY_2A_2C)
    assert min_rooms(1, 3, FAMILY_2A_2C) == 1


def test_HT07_two_small_rooms_hold_family(tmp_path):
    assert rooms_fit(2, 2, 2, ROOM_1A_1C)
    assert not rooms_fit(2, 2, 1, ROOM_1A_1C)
    assert min_rooms(2, 2, ROOM_1A_1C) == 2
    cat = _catalog(tmp_path, lambda d: (d["rooms"][1].update({"max_adults": 1, "max_children": 1}),
                                        set_availability(d, "RM-K1-A", "2027-04-10", [2, 2])))
    assert check_hotel_item(_hotel_item("2027-04-10", "2027-04-12", 2, "HTL-K1", "RM-K1-A"), cat, 2, 2) == []


def test_occupancy_examples_from_data_dictionary():
    # 2 adults + 1 child need 2 TWIN rooms or 1 FAMILY room.
    assert min_rooms(2, 1, TWIN_2A_0C) == 2
    assert min_rooms(2, 1, FAMILY_2A_2C) == 1
    assert not rooms_fit(2, 1, 0, FAMILY_2A_2C)


# ------------------------------------------------------------------ nights / dates / identity

def test_HT11_checkout_night_is_not_required(tmp_path):
    assert stay_nights("2027-04-10", "2027-04-16") == [
        "2027-04-10", "2027-04-11", "2027-04-12", "2027-04-13", "2027-04-14", "2027-04-15"]
    # Rows for 10..15 only; 16 is full -> still available, because 16 is the check-out day.
    cat = _catalog(tmp_path, lambda d: set_availability(d, "RM-K1-A", "2027-04-10", [1, 1, 1, 1, 1, 1, 0]))
    assert room_stay_available(cat, "RM-K1-A", "2027-04-10", "2027-04-16", 1)
    assert check_hotel_item(_hotel_item("2027-04-10", "2027-04-16", 1, "HTL-K1", "RM-K1-A"), cat, 2, 1) == []


def test_HT12_display_checkin_time_does_not_reject(tmp_path):
    cat = _catalog(tmp_path, lambda d: d["hotels"][0].update({"check_in_from": "23:59", "check_out_until": "00:01"}))
    assert check_hotel_item(_hotel_item("2027-04-10", "2027-04-12", 2), cat, 3, 0) == []


def test_HT13_room_of_another_hotel_rejected(tmp_path):
    cat = _catalog(tmp_path)
    v = check_hotel_item(_hotel_item("2027-04-10", "2027-04-12", 1, "HTL-T1", "RM-K1-A"), cat, 2, 0)
    assert _codes(v) == ["ROOM_HOTEL_MISMATCH"]


def test_HT14_exact_night_coverage_not_row_counts(tmp_path):
    # 4 rows exist (= 4 nights) but one is outside the stay: a COUNT(*) check would pass.
    def mutate(d):
        set_availability(d, "RM-T1-A", "2027-04-10", [2, 2, 2])
        d["availability"].append({"room_id": "RM-T1-A", "date": "2027-04-20", "rooms_left": 2})
    cat = _catalog(tmp_path, mutate)
    assert unavailable_nights(cat, "RM-T1-A", "2027-04-10", "2027-04-14", 1) == ["2027-04-13"]
    assert _codes(check_hotel_item(_hotel_item("2027-04-10", "2027-04-14", 1), cat, 2, 0)) == ["HOTEL_NO_AVAILABILITY"]


def test_hotel_wrong_city_and_malformed_dates(tmp_path):
    cat = _catalog(tmp_path)
    assert _codes(check_hotel_item(_hotel_item("2027-04-10", "2027-04-12", 1), cat, 2, 0, city="Kyoto")) == [
        "HOTEL_WRONG_CITY"]
    for start, end in (("2027-04-12", "2027-04-12"), ("20270410", "2027-04-12"), (None, "2027-04-12")):
        assert _codes(check_hotel_item(_hotel_item(start, end, 1), cat, 2, 0)) == ["ITEM_MALFORMED"]
    for k in (0, True, "2"):
        assert _codes(check_hotel_item(_hotel_item("2027-04-10", "2027-04-12", k), cat, 2, 0)) == ["ROOM_CAPACITY"]
    assert _codes(check_hotel_item(_hotel_item("2027-04-10", "2027-04-12", 1, "HTL-X"), cat, 2, 0)) == [
        "UNKNOWN_CATALOG_ID"]


@pytest.mark.parametrize("room_id", ["RM-TYO-001-A", "RM-KYO-004-B"])
def test_official_catalog_availability_lookup(room_id):
    from tests.domain_fixtures.catalog_builder import OFFICIAL_CATALOG_DIR
    cat = load_catalog(OFFICIAL_CATALOG_DIR)
    nights = stay_nights("2027-04-01", "2027-04-21")
    assert all(cat.rooms_left(room_id, n) is not None for n in nights)
    assert cat.rooms_left(room_id, "2027-03-31") is None
