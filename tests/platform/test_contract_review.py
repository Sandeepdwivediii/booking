"""Regression tests for Tanish's Phase 0 review of commit fd4f331 (findings 1-10)."""

from __future__ import annotations

import copy
import sys

import pytest

from app import contracts as c
from tests.platform.fixtures.contract_fixtures import (
    MENON_REQUEST, REFERENCE, REPO_ROOT, SAMPLE_TOTAL, TRIP_ID, FakeDomain, booked_trip, built_result,
    no_package_result, over_budget_result, sample_trip,
)


def _attr(item_id, day, hh="15:00", end="16:00"):
    return {"item_id": item_id, "type": "attraction", "role": "attraction_visit", "name": "x",
            "city": "Tokyo", "status": "PLANNED", "catalog_id": f"FAKE-{item_id}", "room_id": None,
            "start": f"{day}T{hh}", "end": f"{day}T{end}", "quantity": 3, "price_inr": 0,
            "booking": None, "sources": [], "warnings": []}


def _with(items_to_add=(), drop=()):
    t = sample_trip()
    t["items"] = [i for i in t["items"] if i["item_id"] not in drop] + list(items_to_add)
    t["total_inr"] = sum(i["price_inr"] for i in t["items"])
    return t


# ---------------------------------------------------------------- 1. attraction day rules

def test_sample_trip_satisfies_all_modes():
    for mode in c.VALIDATION_MODES:
        assert c.attraction_count_errors(sample_trip(), mode) == []


@pytest.mark.parametrize("mode", sorted(c.VALIDATION_MODES))
def test_max_two_per_day_every_mode(mode):  # AT-02, all modes
    t = _with([_attr("x1", "2027-04-11", "13:00", "14:00"), _attr("x2", "2027-04-11")])
    assert any("max is 2" in e for e in c.attraction_count_errors(t, mode))


@pytest.mark.parametrize("mode", sorted(c.VALIDATION_MODES))
def test_return_day_zero_every_mode(mode):  # AT-04
    t = _with([_attr("x1", "2027-04-16", "06:00", "07:00")])
    assert any("return day" in e for e in c.attraction_count_errors(t, mode))


def test_full_day_minimum_only_in_initial():  # AT-01 vs AT-15
    t = _with(drop=("i4",))  # 2027-04-11 full day now empty
    assert any("full day" in e for e in c.attraction_count_errors(t, "initial"))
    assert c.attraction_count_errors(t, "modified") == []
    assert c.attraction_count_errors(t, "confirm") == []


@pytest.mark.parametrize("day", ["2027-04-10", "2027-04-13"])  # arrival, city-change
def test_arrival_and_change_days_allow_zero_to_two(day):  # AT-03
    assert c.attraction_count_errors(sample_trip(), "initial") == []  # 0 on those days
    two = _with([_attr("x1", day, "14:00", "15:00"), _attr("x2", day, "16:00", "17:00")])
    assert c.attraction_count_errors(two, "initial") == []
    three = _with([_attr("x1", day, "14:00", "15:00"), _attr("x2", day, "16:00", "17:00"),
                   _attr("x3", day, "18:00", "19:00")])
    assert c.attraction_count_errors(three, "initial")


def test_attraction_outside_trip_and_bad_mode():
    assert c.attraction_count_errors(_with([_attr("x1", "2027-04-20")]), "modified")
    assert c.attraction_count_errors(sample_trip(), "bogus")


def test_built_and_change_checkers_apply_attraction_rules():
    res = built_result()
    res["trip"] = _with(drop=("i4",))
    assert c.build_result_shape_errors(res, MENON_REQUEST, TRIP_ID)  # initial min violated
    orig = sample_trip()
    third = _with([_attr("x1", "2027-04-11", "13:00", "14:00"), _attr("x2", "2027-04-11")])
    bad = {"ok": True, "trip": third, "violations": [], "adjustments": []}
    assert any("max is 2" in e for e in c.change_result_shape_errors(bad, orig))  # add_attraction 3rd


# ---------------------------------------------------------------- 2. booked export (SY-10, SY-11, BK-14)

def test_booked_trip_valid():
    assert c.booked_trip_shape_errors(booked_trip(), REFERENCE) == []


@pytest.mark.parametrize("label, fn, ref", [
    ("top-level status missing", lambda t: t.pop("status"), REFERENCE),
    ("top-level only BOOKED (SY-11)", lambda t: [i.update(status="PLANNED", booking=None) for i in t["items"]], REFERENCE),
    ("mixed PLANNED/BOOKED", lambda t: t["items"][3].update(status="PLANNED"), REFERENCE),
    ("one item missing booking", lambda t: t["items"][0].update(booking=None), REFERENCE),
    ("different reference on one item", lambda t: t["items"][1].update(booking={"reference": "9999999999"}), REFERENCE),
    ("int reference in booking", lambda t: t["items"][1].update(booking={"reference": 123456789}), REFERENCE),
    ("extra booking keys", lambda t: t["items"][1].update(booking={"reference": REFERENCE, "x": 1}), REFERENCE),
])
def test_booked_trip_rejects(label, fn, ref):
    t = booked_trip(); fn(t)
    assert c.booked_trip_shape_errors(t, ref), label


@pytest.mark.parametrize("ref", ["123456789", "12345678901", "12345abcde", 1234567890, None, " 0123456789", "0123456789\n", "٠١٢٣٤٥٦٧٨٩"])
def test_booked_reference_format(ref):
    t = booked_trip()
    for it in t["items"]:
        it["booking"] = {"reference": ref}
    assert any("10 digits" in e for e in c.booked_trip_shape_errors(t, ref))


def test_draft_export_with_booked_item_rejected():  # SY-10
    t = sample_trip(); t["items"][0].update(status="BOOKED", booking={"reference": REFERENCE})
    assert c.trip_shape_errors(t, draft=True)


# ---------------------------------------------------------------- 3. change result invariants

def _ok(trip):
    return {"ok": True, "trip": trip, "violations": [], "adjustments": []}


def test_change_ok_positive():
    orig = sample_trip()
    res = FakeDomain().change(orig, {"op": "remove_attraction", "item_id": "i5"})
    assert c.change_result_shape_errors(res, orig) == []


@pytest.mark.parametrize("field, value", [
    ("trip_id", "other-id"),
    ("country", "United Arab Emirates"),
    ("start_date", "2027-04-11"),
    ("end_date", "2027-04-17"),
    ("budget_inr", 999_999),
    ("traveler", {"name": None, "home_airport": "DEL", "adults": 2, "children": 1, "child_ages": [6]}),
])
def test_change_must_not_alter_fixed_fields(field, value):
    orig, new = sample_trip(), sample_trip()
    new[field] = value
    assert any(f"alter {field}" in e for e in c.change_result_shape_errors(_ok(new), orig))


def test_change_over_budget_rejected():
    orig = sample_trip(); orig["budget_inr"] = SAMPLE_TOTAL - 1
    new = copy.deepcopy(orig)
    assert "changed trip exceeds budget" in c.change_result_shape_errors(_ok(new), orig)


def test_change_rejected_shape():
    orig = sample_trip()
    good = {"ok": False, "trip": None, "adjustments": [],
            "violations": [{"code": "ATTRACTION_CLOSED", "message": "closed", "item_id": None}]}
    assert c.change_result_shape_errors(good, orig) == []
    assert c.change_result_shape_errors(dict(good, trip=orig), orig)
    assert c.change_result_shape_errors(dict(good, ok="yes"), orig)


# ---------------------------------------------------------------- 4. build result invariants

def test_built_end_date_must_be_start_plus_nights():  # SY-14
    res = built_result(); res["trip"]["end_date"] = "2027-04-15"
    assert any("end_date" in e for e in c.build_result_shape_errors(res, MENON_REQUEST, TRIP_ID))


@pytest.mark.parametrize("cheapest, ok", [(None, True), (SAMPLE_TOTAL, True), (SAMPLE_TOTAL - 1, True),
                                          (SAMPLE_TOTAL + 1, False), (-1, False), ("1", False), (1.0, False)])
def test_built_cheapest_total(cheapest, ok):
    res = built_result(); res["cheapest_total_inr"] = cheapest
    assert (c.build_result_shape_errors(res, MENON_REQUEST, TRIP_ID) == []) is ok


def test_over_budget_reason_code_must_be_null_and_reason_required():
    req = dict(MENON_REQUEST, budget_inr=79_999)
    bad = over_budget_result(79_999, 80_000); bad["reason_code"] = "OVER_BUDGET"
    assert "OVER_BUDGET must have reason_code null" in c.build_result_shape_errors(bad, req, TRIP_ID)
    for reason in (None, "", "   "):
        bad = over_budget_result(79_999, 80_000); bad["reason"] = reason
        assert any("non-empty reason" in e for e in c.build_result_shape_errors(bad, req, TRIP_ID))


def test_no_package_reason_required():
    for reason in (None, "  "):
        bad = no_package_result(); bad["reason"] = reason
        assert any("non-empty reason" in e for e in c.build_result_shape_errors(bad, MENON_REQUEST, TRIP_ID))


# ---------------------------------------------------------------- 6. item semantics

def test_flight_and_attraction_quantity_equals_travelers():
    for idx in (0, 3):  # outbound flight, attraction
        t = sample_trip(); t["items"][idx]["quantity"] = 2
        assert any("traveler count" in e for e in c.trip_shape_errors(t))


# ---------------------------------------------------------------- 7. alternatives

def test_attraction_alternative_requires_start_time():
    d, trip = FakeDomain(), sample_trip()
    alts = d.alternatives(trip, "attraction", {"date": "2027-04-12"})
    assert alts and c.alternatives_shape_errors(alts, "attraction") == []
    del alts[0]["change"]["start_time"]
    assert any("start_time" in e for e in c.alternatives_shape_errors(alts, "attraction"))


def test_alternatives_shape_rejects_wrong_op_and_hotel_without_room():
    alt = {"component": "hotel", "change": {"op": "replace_flight", "item_id": "i3", "flight_id": "x"},
           "name": "x", "catalog_id": "H", "room_id": None, "start": None, "end": None,
           "price_inr": 1, "new_total_inr": None, "price_diff_inr": None}
    errs = c.alternatives_shape_errors([alt], "hotel")
    assert any("change.op" in e for e in errs) and any("room_id" in e for e in errs)


def test_alternatives_dates_return_empty_or_raise():
    d, trip = FakeDomain(), sample_trip()
    assert d.alternatives(trip, "attraction", {"date": "2027-04-16"}) == []  # return day
    assert d.alternatives(trip, "attraction", {"date": "2027-05-01"}) == []  # outside trip
    for bad in ("16-04-2027", "2027-02-30", None):
        with pytest.raises(c.InvalidRequestError):
            d.alternatives(trip, "attraction", {"date": bad})


# ---------------------------------------------------------------- 8. build request handling

def test_build_request_errors_valid():
    assert c.build_request_errors(MENON_REQUEST) == []


@pytest.mark.parametrize("label, req", [
    ("missing key", {k: v for k, v in MENON_REQUEST.items() if k != "nights"}),
    ("extra key", dict(MENON_REQUEST, price=1)),
    ("str nights", dict(MENON_REQUEST, nights="six")),
    ("bool adults", dict(MENON_REQUEST, adults=True)),
    ("float budget", dict(MENON_REQUEST, budget_inr=1.5)),
    ("age object", dict(MENON_REQUEST, children_ages=[{"age": 6}])),
    ("bad date", dict(MENON_REQUEST, start_date="2027-13-01")),
    ("not a dict", ["BLR"]),
])
def test_build_raises_invalid_request(label, req):
    assert c.build_request_errors(req), label
    with pytest.raises(c.InvalidRequestError):
        FakeDomain().build(req, TRIP_ID)


@pytest.mark.parametrize("start, nights", [("2027-03-31", 3), ("2027-04-17", 4)])  # IN-04, IN-05 at domain level
def test_outside_window_is_no_package(start, nights):
    req = dict(MENON_REQUEST, start_date=start, nights=nights)
    res = FakeDomain().build(req, TRIP_ID)
    assert res["status"] == "NO_PACKAGE" and res["reason_code"] == "OUTSIDE_DATA_WINDOW"
    assert c.build_result_shape_errors(res, req, TRIP_ID) == []


def test_window_boundary_is_buildable():  # IN-06
    req = dict(MENON_REQUEST, start_date="2027-04-10", nights=10)  # returns exactly 2027-04-20
    assert FakeDomain().build(req, TRIP_ID)["status"] == "BUILT"


# ---------------------------------------------------------------- 9/10. runtime + files

def test_python_311_plus():
    assert sys.version_info >= (3, 11)
    assert "3.11" in (REPO_ROOT / "app" / "contracts.py").read_text(encoding="utf-8")


def test_contract_doc_references_existing_test_files():
    doc = (REPO_ROOT / "docs" / "INTEGRATION_CONTRACT.md").read_text(encoding="utf-8")
    for p in ("tests/platform/test_contract.py", "tests/platform/test_contract_review.py",
              "tests/platform/fixtures/contract_fixtures.py"):
        assert p in doc and (REPO_ROOT / p).is_file(), p
