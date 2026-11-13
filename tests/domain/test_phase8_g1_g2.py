"""Phase 8 (optional extension): G1 dependency-aware edits, G2 exact signed price differences.

Uses the Phase 5 synthetic world (base trip 252,000; see test_phase5_edits) and the official catalog.
Every result is also checked by the independent judge oracle where a trip is returned.
"""

from __future__ import annotations

import copy

import pytest

from app.contracts import alternatives_shape_errors, change_result_shape_errors
from app.domain.adapter import Domain
from app.domain.catalog import load_catalog
from app.domain.pricing import price_trip
from tests.domain.test_phase5_edits import _extend, REQ  # noqa: F401  (shared synthetic world)
from tests.domain_fixtures.catalog_builder import OFFICIAL_CATALOG_DIR, synthetic_catalog, write_catalog
from tests.domain_fixtures.judge_oracle import Raw, judge
from tests.domain_fixtures.oracle import INR


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    data = synthetic_catalog()
    _extend(data)
    data["flights"].append(dict(data["flights"][0], flight_id="FL-NIGHT", departure_local="2027-04-10T12:00:00",
                                arrival_local="2027-04-10T23:30:00", price_per_seat=INR(25000)))
    data["transfers"][0].update(service_until="00:30")  # NRT taxi: a 23:30 landing -> 00:30 next-day pickup
    root = write_catalog(tmp_path_factory.mktemp("g"), data)
    plain = Domain(load_catalog(write_catalog(tmp_path_factory.mktemp("p"), synthetic_catalog())))
    trip = plain.build(REQ, "trip-1")["trip"]
    return Domain(load_catalog(root)), Raw(root), trip


def run(domain, trip, change):
    before = copy.deepcopy(trip)
    result = domain.change(trip, change)
    assert trip == before and change_result_shape_errors(result, trip) == []
    return result


def item(trip, iid):
    return next(i for i in trip["items"] if i["item_id"] == iid)


# ------------------------------------------------------------------ G1

def test_G1_A01_attraction_overlap_rejected_with_affected_item(world):
    domain, _, trip = world
    r = run(domain, trip, {"op": "add_attraction", "attraction_id": "ATT-K2", "date": "2027-04-13",
                           "start_time": "10:30"})
    assert r["ok"] is False and [v["code"] for v in r["violations"]] == ["ATTRACTION_OVERLAP"]
    assert r["violations"][0]["item_id"]  # names the conflicting visit
    ok = run(domain, trip, {"op": "add_attraction", "attraction_id": "ATT-K2", "date": "2027-04-13"})
    visits = sorted((i["start"], i["end"]) for i in ok["trip"]["items"]
                    if i["role"] == "attraction_visit" and i["start"].startswith("2027-04-13"))
    assert all(a[1] <= b[0] for a, b in zip(visits, visits[1:]))  # repaired slot: conflict-free


def test_G1_A02_inbound_flight_change_recalculates_pickup(world):
    domain, raw, trip = world
    r = run(domain, trip, {"op": "replace_flight", "item_id": "i1", "flight_id": "FL-1B"})  # lands 07:00
    assert r["ok"] and item(r["trip"], "i2")["start"] == "2027-04-10T08:00"
    assert any("Arrival airport transfer" in a for a in r["adjustments"])
    late = run(domain, trip, {"op": "replace_flight", "item_id": "i1", "flight_id": "FL-NIGHT"})  # lands 23:30
    assert late["ok"] and item(late["trip"], "i2")["start"] == "2027-04-11T00:30"  # old 11:30 would be invalid
    for t in (r["trip"], late["trip"]):
        assert judge(raw, t, initial=False) == []


def test_G1_A03_return_flight_change_repairs_or_rejects(world):
    domain, raw, trip = world
    r = run(domain, trip, {"op": "replace_flight", "item_id": "i9", "flight_id": "FL-2-EARLY"})  # departs 01:00
    assert r["ok"] and item(r["trip"], "i8")["start"] == "2027-04-13T20:30"
    assert any("Departure airport transfer" in a for a in r["adjustments"]) and judge(raw, r["trip"], False) == []
    busy = run(domain, trip, {"op": "add_attraction", "attraction_id": "ATT-K2", "date": "2027-04-13",
                              "start_time": "21:00"})["trip"]
    rejected = run(domain, busy, {"op": "replace_flight", "item_id": "i9", "flight_id": "FL-2-EARLY"})
    assert rejected["ok"] is False and {v["code"] for v in rejected["violations"]} == {"ATTRACTION_TIMELINE"}


def test_G1_A04_hotel_area_change_rechecks_transfer(world):
    domain, raw, trip = world
    moved = run(domain, trip, {"op": "replace_hotel", "item_id": "i3", "hotel_id": "HTL-T3", "room_id": "RM-T3-A"})
    assert moved["ok"] and item(moved["trip"], "i2")["catalog_id"] == "TR-NRT-SJK"
    assert judge(raw, moved["trip"], False) == []
    unreachable = run(domain, trip, {"op": "replace_hotel", "item_id": "i3", "hotel_id": "HTL-T2",
                                     "room_id": "RM-T2-A"})
    assert {v["code"] for v in unreachable["violations"]} == {"NO_AIRPORT_TRANSFER"}


def test_G1_A05_late_arrival_check_in_warning(world):
    domain, raw, trip = world
    late = run(domain, trip, {"op": "replace_flight", "item_id": "i1", "flight_id": "FL-NIGHT"})
    hotel = item(late["trip"], "i3")
    assert late["ok"] and hotel["warnings"] and "after the check-in date" in hotel["warnings"][0]
    assert any(a.startswith("Check-in:") for a in late["adjustments"])
    assert judge(raw, late["trip"], False) == []  # display warning only, never a rejection
    back = run(domain, late["trip"], {"op": "replace_flight", "item_id": "i1", "flight_id": "FL-1"})
    assert back["ok"] and item(back["trip"], "i3")["warnings"] == []  # warning cleared when resolved


def test_G1_A06_no_affected_items_no_impact(world):
    domain, _, trip = world
    for change in ({"op": "remove_attraction", "item_id": "i4"},
                   {"op": "add_attraction", "attraction_id": "ATT-K2", "date": "2027-04-13"}):
        r = run(domain, trip, change)
        assert r["ok"] and r["adjustments"] == []
        unchanged = [i for i in trip["items"] if i["role"] != "attraction_visit"]
        assert [i for i in r["trip"]["items"] if i["role"] != "attraction_visit"] == unchanged
        assert all(i["warnings"] == [] for i in r["trip"]["items"])


# ------------------------------------------------------------------ G2

def test_G2_A01_signed_deltas_higher_lower_unchanged(world):
    domain, _, trip = world
    alts = domain.alternatives(trip, "hotel", {"item_id": "i3"}) + domain.alternatives(trip, "outbound_flight", {})
    alts += domain.alternatives(trip, "attraction", {"date": "2027-04-13"})
    deltas = {a["price_diff_inr"] for a in alts}
    assert any(d > 0 for d in deltas) and any(d < 0 for d in deltas) and 0 in deltas
    for a in alts:
        assert isinstance(a["price_diff_inr"], int) and a["price_diff_inr"] == a["new_total_inr"] - trip["total_inr"]


def test_G2_A02_delta_includes_dependent_transfer(world):
    domain, _, trip = world
    alt = next(a for a in domain.alternatives(trip, "hotel", {"item_id": "i3"}) if a["catalog_id"] == "HTL-T3")
    hotel_only = alt["price_inr"] - item(trip, "i3")["price_inr"]  # 14,000 - 24,000
    transfer = 10000 - 13000  # NRT taxi (1 vehicle) -> Shinjuku taxi (2 vehicles of 2)
    assert alt["price_diff_inr"] == hotel_only + transfer != hotel_only
    applied = domain.change(trip, alt["change"])["trip"]
    assert applied["total_inr"] == price_trip(applied, domain.catalog)["total_inr"] == alt["new_total_inr"]


def test_G2_A03_infeasible_or_unpriceable_options_excluded(world):
    domain, _, trip = world
    listed = {(a["catalog_id"], a["room_id"]) for a in domain.alternatives(trip, "hotel", {"item_id": "i3"})}
    assert not listed & {("HTL-T2", "RM-T2-A"), ("HTL-T1", "RM-T1-S"), ("HTL-T1", "RM-T1-F")}
    flights = {a["catalog_id"] for a in domain.alternatives(trip, "outbound_flight", {})}
    assert not flights & {"FL-LATE", "FL-KIX-IN", "FL-FEW"}
    assert domain.alternatives(trip, "attraction", {"date": "2027-04-14"}) == []
    r = domain.change(trip, {"op": "replace_hotel", "item_id": "i3", "hotel_id": "HTL-NOPE", "room_id": "RM-X"})
    assert r["ok"] is False and r["trip"] is None  # no fabricated price for unknown catalog IDs


# ------------------------------------------------------------------ official catalog

def test_official_alternative_deltas_match_judged_edits():
    domain = Domain(load_catalog(OFFICIAL_CATALOG_DIR))
    raw = Raw(OFFICIAL_CATALOG_DIR)
    req = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 6,
           "adults": 2, "children_ages": [6], "budget_inr": 350000}
    trip = domain.build(req, "menon")["trip"]
    hotel = next(i for i in trip["items"] if i["role"] == "hotel_stay")
    for component, filters in (("hotel", {"item_id": hotel["item_id"], "limit": 50}), ("outbound_flight", {})):
        alts = domain.alternatives(trip, component, filters)
        assert alternatives_shape_errors(alts, component) == []
        for a in alts:
            r = domain.change(trip, a["change"])
            assert r["ok"] and r["trip"]["total_inr"] - trip["total_inr"] == a["price_diff_inr"]
            assert judge(raw, r["trip"], initial=False) == []
