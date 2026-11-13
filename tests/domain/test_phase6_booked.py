"""Phase 6 (domain side): booked export audit (BK-14, SY-11) and validate(mode="confirm")."""

from __future__ import annotations

import copy

import pytest

from app.contracts import InvalidRequestError, booked_trip_shape_errors
from app.domain.adapter import Domain
from app.domain.catalog import load_catalog
from tests.domain_fixtures.booked_oracle import booked_export_problems, stamp_booked
from tests.domain_fixtures.catalog_builder import OFFICIAL_CATALOG_DIR

MENON = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 6,
         "adults": 2, "children_ages": [6], "budget_inr": 350000}
REF = "0123456789"  # leading zero must survive (BK-06)


@pytest.fixture(scope="module")
def domain():
    return Domain(load_catalog(OFFICIAL_CATALOG_DIR))


@pytest.fixture
def draft(domain):
    return domain.build(MENON, "trip-menon")["trip"]


def test_BK14_every_item_booked_with_same_reference(domain, draft):
    booked = stamp_booked(draft, REF)
    assert booked_export_problems(booked, REF, draft) == []
    assert booked_trip_shape_errors(booked, REF) == []  # contract checker agrees
    assert all(i["booking"] == {"reference": REF} and i["status"] == "BOOKED" for i in booked["items"])
    assert domain.validate(booked, "confirm") == []  # booking metadata does not affect domain validity


def test_SY11_top_level_booked_only_is_rejected(draft):
    only_top = copy.deepcopy(draft)
    only_top["status"] = "BOOKED"
    problems = booked_export_problems(only_top, REF)
    assert len([p for p in problems if "is not BOOKED" in p]) == len(draft["items"])
    assert booked_trip_shape_errors(only_top, REF) != []


@pytest.mark.parametrize("tamper, ref", [
    (lambda t: t["items"][3].update(status="PLANNED"), REF),            # one item left PLANNED
    (lambda t: t["items"][0].update(booking={"reference": "9999999999"}), REF),  # different reference
    (lambda t: t["items"][0].update(booking={"reference": REF, "extra": 1}), REF),
    (lambda t: t["items"][0].update(booking=None), REF),
    (lambda t: t.update(status="DRAFT"), REF),
    (lambda t: None, "12345"),                                          # short reference
    (lambda t: None, 123456789),                                        # integer reference
    (lambda t: None, "012345678\n"),                                    # trailing newline
])
def test_booked_export_tampering_detected_by_both_checkers(draft, tamper, ref):
    booked = stamp_booked(draft, REF)  # stamped correctly; then one defect is introduced
    tamper(booked)
    assert booked_export_problems(booked, ref, draft) != []
    assert booked_trip_shape_errors(booked, ref) != []


def test_snapshot_must_equal_confirmed_draft(draft):
    booked = stamp_booked(draft, REF)
    booked["items"][2]["price_inr"] += 1
    booked["total_inr"] += 1
    assert booked_export_problems(booked, REF, draft) == [
        "booked export differs from the confirmed draft beyond the booking stamp"]


# ------------------------------------------------------------------ validate(mode="confirm")

def test_confirm_mode_accepts_valid_and_edited_drafts(domain, draft):
    assert domain.validate(draft, "confirm") == []
    visit = next(i for i in draft["items"] if i["role"] == "attraction_visit")
    edited = domain.change(draft, {"op": "remove_attraction", "item_id": visit["item_id"]})["trip"]
    assert domain.validate(edited, "confirm") == []  # zero-attraction day is fine after an edit (AT-15)
    assert domain.validate(edited, "initial") != []


@pytest.mark.parametrize("tamper, code", [
    (lambda t: (t["items"][2].update(price_inr=t["items"][2]["price_inr"] - 1),
                t.update(total_inr=t["total_inr"] - 1)), "PRICE_MISMATCH"),     # stale / client price
    (lambda t: t.update(budget_inr=t["total_inr"] - 1), "OVER_BUDGET"),
    (lambda t: (t["items"].remove(ret := next(i for i in t["items"] if i["role"] == "return_flight")),
                t.update(total_inr=t["total_inr"] - ret["price_inr"])), "FLIGHT_COUNT"),
    (lambda t: next(i for i in t["items"] if i["role"] == "hotel_stay").update(room_id="RM-KYO-004-B"),
     "ROOM_HOTEL_MISMATCH"),
])
def test_confirm_mode_rejects_invalid_drafts(domain, draft, tamper, code):
    bad = copy.deepcopy(draft)
    tamper(bad)
    assert code in {v["code"] for v in domain.validate(bad, "confirm")}


def test_confirm_mode_never_raises_on_garbage(domain):
    assert {v["code"] for v in domain.validate({"trip_id": "x"}, "confirm")} == {"TRIP_MALFORMED"}
    with pytest.raises(InvalidRequestError):
        domain.validate({}, "booked")
