"""Phase 2 pricing: PR-01..07, PR-17, TR-16, FL-12, and check_prices / price_trip."""

from __future__ import annotations

import copy

import pytest

from app.domain.catalog import load_catalog
from app.domain.pricing import (
    attraction_price, flight_price, hotel_price, price_trip, transfer_price, transfer_quantity,
)
from app.domain.validator import check_prices
from tests.domain_fixtures.catalog_builder import synthetic_catalog, write_catalog

INR = lambda a: {"amount": a, "currency": "INR"}  # noqa: E731


@pytest.fixture
def cat(tmp_path):
    """Synthetic catalog priced like the TANISH.md PR examples."""
    d = synthetic_catalog()
    d["flights"][0]["price_per_seat"] = INR(10000)
    d["flights"][1]["price_per_seat"] = INR(8000)
    d["rooms"][1]["price_per_night"] = INR(4500)
    d["rooms"][1].update({"max_adults": 1, "max_children": 1})
    d["transfers"][1].update({"price": INR(1000), "child_price": INR(500)})  # PER_PERSON
    d["transfers"][0].update({"price": INR(1600), "max_passengers": 2})  # PER_VEHICLE
    d["attractions"][0].update({"price_per_person": INR(700), "child_price": INR(300)})
    return load_catalog(write_catalog(tmp_path, d))


def _item(iid, kind, cid, qty, price, start="2027-04-10T12:00", end=None, room_id=None):
    return {"item_id": iid, "type": kind, "catalog_id": cid, "room_id": room_id, "start": start, "end": end,
            "quantity": qty, "price_inr": price, "status": "PLANNED", "booking": None, "sources": [],
            "warnings": []}


def _trip(child_ages=(6,), adults=2, budget=100000):
    items = [
        _item("i1", "flight", "FL-1", 3, 30000),
        _item("i2", "flight", "FL-2", 3, 24000),
        _item("i3", "hotel", "HTL-K1", 2, 18000, "2027-04-10", "2027-04-12", room_id="RM-K1-A"),
        _item("i4", "transfer", "TR-KIX-KYO", 3, 2500),
        _item("i5", "transfer", "TR-NRT-GNZ", 2, 3200),
        _item("i6", "attraction", "ATT-T1", 3, 1700),
    ]
    return {"trip_id": "t", "traveler": {"name": None, "home_airport": "BLR", "adults": adults,
            "children": len(child_ages), "child_ages": list(child_ages)},
            "budget_inr": budget, "total_inr": sum(i["price_inr"] for i in items), "items": items}


# ------------------------------------------------------------------ formulas

def test_PR01_flights_per_seat_times_travelers(cat):
    assert flight_price(cat.flights["FL-1"], 3) + flight_price(cat.flights["FL-2"], 3) == 54000


def test_PR02_hotel_per_room_night(cat):
    assert hotel_price(cat.rooms["RM-K1-A"], 2, 2) == 18000


def test_PR03_per_person_transfer_with_child_price(cat):
    assert transfer_price(cat.transfers["TR-KIX-KYO"], 2, 1) == 2500
    assert transfer_quantity(cat.transfers["TR-KIX-KYO"], 3) == 3


def test_PR04_missing_child_price_charges_adult_price(cat):
    train = cat.transfers["TR-TYO-KYO"]  # PER_PERSON, no child_price, 14000
    assert "child_price" not in train
    assert transfer_price(train, 2, 1) == 3 * 14000
    shrine = dict(cat.attractions["ATT-T1"])
    del shrine["child_price"]
    assert attraction_price(shrine, 2, 1) == 3 * 700


def test_PR05_per_vehicle_rounds_up(cat):
    taxi = cat.transfers["TR-NRT-GNZ"]  # capacity 2, 1600
    assert transfer_quantity(taxi, 3) == 2
    assert transfer_price(taxi, 2, 1) == 3200
    assert transfer_quantity(taxi, 2) == 1 and transfer_quantity(taxi, 1) == 1


def test_PR06_attraction_adult_and_child(cat):
    assert attraction_price(cat.attractions["ATT-T1"], 2, 1) == 1700


def test_PR07_combined_total_and_item_sum(cat):
    trip = _trip()
    # PR-07 line items: 54,000 + 18,000 + 2,500 + 3,200 + 1,700
    priced = price_trip(trip, cat)
    assert priced["errors"] == []
    assert priced["total_inr"] == 79400 == trip["total_inr"] == sum(i["price_inr"] for i in trip["items"])
    assert check_prices(trip, cat) == []


def test_PR17_free_attraction_priced_zero_not_missing(cat):
    free = cat.attractions["ATT-K1"]
    assert attraction_price(free, 2, 1) == 0
    trip = _trip()
    trip["items"].append(_item("i7", "attraction", "ATT-K1", 3, 0))
    assert price_trip(trip, cat)["items"]["i7"] == (3, 0)
    assert check_prices(trip, cat) == []


def test_TR16_vehicle_quantity_seven_travelers_capacity_four(tmp_path):
    cat = load_catalog(write_catalog(tmp_path, synthetic_catalog()))
    taxi = cat.transfers["TR-NRT-GNZ"]  # capacity 4, 13000 per vehicle
    assert transfer_quantity(taxi, 7) == 2
    assert transfer_price(taxi, 5, 2) == 26000
    assert transfer_quantity(taxi, 8) == 2 and transfer_quantity(taxi, 9) == 3


def test_FL12_infant_needs_a_paid_seat(cat):
    trip = _trip(child_ages=(0,))
    assert price_trip(trip, cat)["items"]["i1"] == (3, 30000)


# ------------------------------------------------------------------ check_prices (tampering)

def _codes(v):
    return [x["code"] for x in v]


def test_tampered_item_price_detected(cat):
    trip = _trip()
    trip["items"][2]["price_inr"] = 1
    trip["total_inr"] = sum(i["price_inr"] for i in trip["items"])
    assert _codes(check_prices(trip, cat)) == ["PRICE_MISMATCH"]


def test_tampered_total_detected(cat):
    trip = _trip()
    trip["total_inr"] -= 1
    assert _codes(check_prices(trip, cat)) == ["TOTAL_MISMATCH"]


def test_wrong_quantity_detected(cat):
    trip = _trip()
    trip["items"][4]["quantity"] = 1  # 3 travelers need 2 vehicles of capacity 2
    assert _codes(check_prices(trip, cat)) == ["QUANTITY_MISMATCH"]


def test_unknown_catalog_id_detected(cat):
    trip = _trip()
    trip["items"][0]["catalog_id"] = "FL-NOPE"
    assert "UNKNOWN_CATALOG_ID" in _codes(check_prices(trip, cat))


def test_over_budget_detected_and_equality_allowed(cat):
    assert check_prices(_trip(budget=79400), cat) == []
    assert _codes(check_prices(_trip(budget=79399), cat)) == ["OVER_BUDGET"]


def test_reprice_after_party_change_never_keeps_stale_items(cat):
    trip = _trip()
    bigger = copy.deepcopy(trip)
    bigger["traveler"].update({"children": 2, "child_ages": [6, 9]})
    codes = _codes(check_prices(bigger, cat))
    assert "QUANTITY_MISMATCH" in codes and "PRICE_MISMATCH" in codes
    priced = price_trip(bigger, cat)
    assert priced["items"]["i5"] == (2, 3200)  # 4 travelers / capacity 2
    assert priced["items"]["i4"] == (4, 3000)  # 2 x 1000 + 2 x 500
    assert price_trip(trip, cat)["total_inr"] == 79400  # input untouched


def test_price_trip_does_not_mutate_input(cat):
    trip = _trip()
    snapshot = copy.deepcopy(trip)
    price_trip(trip, cat)
    check_prices(trip, cat)
    assert trip == snapshot


def test_prices_are_ints_on_official_catalog():
    from tests.domain_fixtures.catalog_builder import OFFICIAL_CATALOG_DIR
    cat = load_catalog(OFFICIAL_CATALOG_DIR)
    for t in cat.transfers.values():
        assert isinstance(transfer_price(t, 2, 1), int)
    for a in cat.attractions.values():
        assert isinstance(attraction_price(a, 2, 1), int)
