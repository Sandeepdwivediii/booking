"""Budget Rescue (domain): BR-D01..BR-D14.

Synthetic world = base Tokyo(2) -> Kyoto(2) package (total 252,000; see test_phase3_planner) priced against a
catalog that additionally offers:
  HTL-T9  Ginza 4*, family room 5,000/night  -> Tokyo stay 10,000 (-14,000), penalty 0
  HTL-T8  Ginza 3*, family room 4,000/night  -> 8,000 (-16,000), penalty (4-3)*2 = 2
  HTL-TS  Shinjuku 4*, 4,500/night -> 9,000 (-15,000) + Shinjuku taxi 2 x 5,000 instead of 13,000 (-3,000) = -18,000
  HTL-T10 Ginza 4*, 1,000/night but sold out on 2027-04-11            -> never usable (room-night availability)
  FL-1D   cheaper outbound 24,000/seat (-3,000)        FL-1C 20,000/seat, only 2 seats -> never usable
  ATT-T2  free Tokyo sight (replaces ATT-T1, -3,000)   ATT-T3 free but closed 2027-04-11 -> never usable
"""

from __future__ import annotations

import copy
import hashlib

import pytest

from app.contracts import InvalidRequestError
from app.domain.catalog import CATALOG_FILES, load_catalog
from app.domain.planner import build_trip
from app.domain.pricing import price_trip
from app.domain.rescue import rescue
from app.domain.validator import validate_trip
from tests.domain_fixtures.catalog_builder import (
    OFFICIAL_CATALOG_DIR, OFFICIAL_SHA256, set_availability, synthetic_catalog, write_catalog,
)
from tests.domain_fixtures.judge_oracle import Raw, judge
from tests.domain_fixtures.oracle import INR

REQ = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 4,
       "adults": 2, "children_ages": [6], "budget_inr": 10 ** 7}
BASE = 252000


def _world(d):
    hotel, family = d["hotels"][0], d["rooms"][1]  # HTL-T1 (Ginza, 4*), RM-K1-A (2A+2C)
    d["hotels"] += [dict(hotel, hotel_id="HTL-T9", name="Ginza Nine", stars=4),
                    dict(hotel, hotel_id="HTL-T8", name="Ginza Eight", stars=3),
                    dict(hotel, hotel_id="HTL-TS", name="Shinjuku Stay", area="Shinjuku", stars=4),
                    dict(hotel, hotel_id="HTL-T10", name="Ginza Ten", stars=4)]
    for hid, price in (("HTL-T9", 5000), ("HTL-T8", 4000), ("HTL-TS", 4500), ("HTL-T10", 1000)):
        rid = "RM-" + hid[4:] + "-A"
        d["rooms"].append(dict(family, room_id=rid, hotel_id=hid, price_per_night=INR(price)))
        set_availability(d, rid, "2027-04-10", [2, 0] if hid == "HTL-T10" else [2, 2])
    d["transfers"].append(dict(d["transfers"][0], transfer_id="TR-NRT-SJK", name="NRT taxi Shinjuku", area="Shinjuku",
                               max_passengers=2, price=INR(5000)))
    out = d["flights"][0]
    d["flights"] += [dict(out, flight_id="FL-1D", arrival_local="2027-04-10T10:00:00", price_per_seat=INR(24000)),
                     dict(out, flight_id="FL-1C", arrival_local="2027-04-10T10:00:00", price_per_seat=INR(20000),
                          seats_left=2)]
    t1 = d["attractions"][0]
    d["attractions"] += [dict(t1, attraction_id="ATT-T2", name="Free Garden", price_per_person=INR(0), closed_dates=[]),
                         dict(t1, attraction_id="ATT-T3", name="Closed Shrine", price_per_person=INR(0),
                              closed_dates=["2027-04-11"])]
    for a in d["attractions"][-2:]:
        a.pop("child_price", None)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    data = synthetic_catalog()
    _world(data)
    root = write_catalog(tmp_path_factory.mktemp("rescue"), data)
    cat = load_catalog(root)
    plain = load_catalog(write_catalog(tmp_path_factory.mktemp("plain"), synthetic_catalog()))
    trip = build_trip(plain, REQ, "trip-br")["trip"]
    assert trip["total_inr"] == BASE and validate_trip(cat, trip, "modified") == []
    return cat, Raw(root), trip


def run(cat, trip, target, locked=(), max_changes=3):
    before = copy.deepcopy(trip)
    result = rescue(cat, trip, target, list(locked), max_changes)
    assert trip == before  # BR-D11: input never mutated
    return result


def ids(proposal):
    """Changed components as a sorted list (output order is flights, hotels, attractions)."""
    return sorted(((c["component"], c["new_catalog_id"]) for c in proposal["changes"]), key=str)


def assert_preserved(original, proposal, locked):
    new = proposal["trip"]
    for k in ("trip_id", "traveler", "country", "start_date", "end_date", "budget_inr"):
        assert new[k] == original[k]
    segs = lambda t: [(h["city"], h["start"], h["end"]) for h in t["items"] if h["role"] == "hotel_stay"]  # noqa: E731
    assert segs(new) == segs(original)  # city order and nights unchanged
    home = lambda t: [i["name"].split()[-3] for i in t["items"] if i["role"] == "outbound_flight"]  # noqa: E731
    assert home(new) == home(original)
    for a in locked:
        old = [(i["catalog_id"], i["start"], i["end"]) for i in original["items"] if i["catalog_id"] == a]
        assert old and old == [(i["catalog_id"], i["start"], i["end"]) for i in new["items"] if i["catalog_id"] == a]


# ------------------------------------------------------------------ BR-D01 exact pricing

def test_BR_D01_exact_integer_pricing_and_breakdown(world):
    cat, raw, trip = world
    r = run(cat, trip, 240000)
    assert r["status"] == "RESCUED" and r["proposals"]
    for p in r["proposals"]:
        new = p["trip"]
        priced = price_trip(new, cat)
        assert priced["errors"] == [] and new["total_inr"] == priced["total_inr"] == p["proposed_total_inr"]
        assert all(isinstance(i["price_inr"], int) for i in new["items"])
        assert p["original_total_inr"] == BASE and p["savings_inr"] == BASE - p["proposed_total_inr"]
        diffs = sum(c["price_diff_inr"] for c in p["changes"]) + sum(d["price_diff_inr"] for d in p["dependent_adjustments"])
        assert diffs == p["proposed_total_inr"] - BASE
        assert judge(raw, new, initial=False) == []


# ------------------------------------------------------------------ BR-D02..D04 locked attractions

def test_BR_D02_locked_attraction_never_removed_or_substituted(world):
    cat, _, trip = world
    free = run(cat, trip, 232000)
    assert any(("attraction", "ATT-T2") in ids(p) or ("attraction", None) in ids(p) for p in free["proposals"])
    locked = run(cat, trip, 232000, locked=["ATT-T1"])
    assert locked["status"] == "RESCUED" and locked["locked_attraction_ids"] == ["ATT-T1"]
    for p in locked["proposals"]:
        assert all(c["old_catalog_id"] != "ATT-T1" for c in p["changes"])
        assert_preserved(trip, p, ["ATT-T1"])
    assert [ids(p) for p in locked["proposals"]] == [sorted([("hotel", "HTL-TS"), ("outbound_flight", "FL-1D")], key=str)]


@pytest.mark.parametrize("locked", [["ATT-NOPE"], ["ATT-T1", "att-t1"]])
def test_BR_D03_unknown_locked_ids_rejected(world, locked):
    cat, _, trip = world
    with pytest.raises(InvalidRequestError) as exc:
        run(cat, trip, 240000, locked=locked)
    assert "unknown" in exc.value.message


def test_BR_D04_locked_attraction_absent_from_trip_rejected(world):
    cat, _, trip = world
    with pytest.raises(InvalidRequestError) as exc:
        run(cat, trip, 240000, locked=["ATT-T2"])  # exists in the catalog, not in this itinerary
    assert "not in this itinerary" in exc.value.message


# ------------------------------------------------------------------ BR-D05 minimal change + ranking

def test_BR_D05_fewest_changes_then_quality_then_closest_to_target(world):
    cat, _, trip = world
    r = run(cat, trip, 240000)
    assert [ids(p) for p in r["proposals"]] == [[("hotel", "HTL-T9")], [("hotel", "HTL-TS")], [("hotel", "HTL-T8")]]
    assert [p["number_of_changes"] for p in r["proposals"]] == [1, 1, 1]
    assert [p["preference_penalty"] for p in r["proposals"]] == [0, 0, 2]  # star loss ranks the 3* hotel last
    assert [p["proposed_total_inr"] for p in r["proposals"]] == [238000, 234000, 236000]
    assert r["optimality"] == "OPTIMAL_WITHIN_SEARCH_SPACE" and not r["search"]["cap_reached"]


# ------------------------------------------------------------------ BR-D06..D08 feasibility filters

def test_BR_D06_hotel_must_cover_every_night(world):
    cat, _, trip = world
    for target in (240000, 232000, 225000):
        for p in run(cat, trip, target, max_changes=4)["proposals"]:
            assert all(c["new_catalog_id"] != "HTL-T10" for c in p["changes"])  # sold out on 2027-04-11
            hotel = next(i for i in p["trip"]["items"] if i["role"] == "hotel_stay" and i["city"] == "Tokyo")
            assert (hotel["start"], hotel["end"]) == ("2027-04-10", "2027-04-12")


def test_BR_D07_flight_seat_capacity(world):
    cat, _, trip = world
    r = run(cat, trip, 232000, max_changes=2)
    assert all(c["new_catalog_id"] != "FL-1C" for p in r["proposals"] for c in p["changes"])  # 2 seats < 3 travellers
    assert any(("outbound_flight", "FL-1D") in ids(p) for p in r["proposals"])


def test_BR_D08_attraction_replacement_respects_day_and_hours(world):
    cat, raw, trip = world
    r = run(cat, trip, 232000)
    replaced = [p for p in r["proposals"] if ("attraction", "ATT-T2") in ids(p)]
    assert replaced and all(c["new_catalog_id"] != "ATT-T3" for p in r["proposals"] for c in p["changes"])
    visit = next(i for i in replaced[0]["trip"]["items"] if i["catalog_id"] == "ATT-T2")
    assert visit["start"].startswith("2027-04-11") and visit["start"] >= "2027-04-11T09:00"  # same day, legal slot
    assert judge(raw, replaced[0]["trip"], initial=False) == []


# ------------------------------------------------------------------ BR-D09 cross-component dependency

def test_BR_D09_dependent_transfer_reselected_and_priced(world):
    cat, _, trip = world
    p = next(p for p in run(cat, trip, 240000)["proposals"] if ids(p) == [("hotel", "HTL-TS")])
    assert p["proposed_total_inr"] == BASE - 15000 - 3000
    assert p["dependent_adjustments"] == [{
        "component": "arrival_transfer", "item_id": "i2", "old_catalog_id": "TR-NRT-GNZ", "new_catalog_id": "TR-NRT-SJK",
        "old_start": "2027-04-10T11:30", "new_start": "2027-04-10T11:30", "price_diff_inr": -3000}]
    assert p["number_of_changes"] == 1  # dependent re-selection is reported, not counted as a change


# ------------------------------------------------------------------ BR-D10 deterministic ranking

def test_BR_D10_deterministic_ranking(world):
    cat, _, trip = world
    first, second = run(cat, trip, 232000), run(cat, trip, 232000)
    assert first == second
    keys = [(p["number_of_changes"], p["preference_penalty"], 232000 - p["proposed_total_inr"], p["candidate_id"])
            for p in first["proposals"]]
    assert keys == sorted(keys) and len({k[3] for k in keys}) == len(keys)
    assert [ids(p) for p in first["proposals"]] == [sorted(x, key=str) for x in (
        [("hotel", "HTL-TS"), ("outbound_flight", "FL-1D")],
        [("attraction", "ATT-T2"), ("hotel", "HTL-TS")],
        [("attraction", None), ("hotel", "HTL-TS")])]


# ------------------------------------------------------------------ BR-D11 immutability

def test_BR_D11_trip_and_catalog_immutable(world):
    cat, _, trip = world
    snapshot = (copy.deepcopy(trip), dict(cat.source_sha256), cat.counts(), dict(cat.availability))
    run(cat, trip, 232000)
    assert (trip, dict(cat.source_sha256), cat.counts(), dict(cat.availability)) == snapshot
    hashes = {n: hashlib.sha256((OFFICIAL_CATALOG_DIR / f"{n}.json").read_bytes()).hexdigest() for n in CATALOG_FILES}
    assert hashes == OFFICIAL_SHA256


# ------------------------------------------------------------------ BR-D12, D13 impossibility and limits

def test_BR_D12_impossible_target_is_explicit_no_rescue(world):
    cat, _, trip = world
    r = run(cat, trip, 1000)
    assert r["status"] == "NO_RESCUE_FOUND" and r["proposals"] == [] and "INR 1,000" in r["reason"]
    already = run(cat, trip, BASE)
    assert already["status"] == "NO_CHANGES_NEEDED" and already["proposals"] == []


def test_BR_D13_max_changes_is_a_hard_limit(world):
    cat, _, trip = world
    one = run(cat, trip, 232000, max_changes=1)  # best single change reaches 234,000
    assert one["status"] == "NO_RESCUE_FOUND"
    two = run(cat, trip, 232000, max_changes=2)
    assert two["status"] == "RESCUED" and all(p["number_of_changes"] <= 2 for p in two["proposals"])
    for bad in (0, 5, True, "2"):
        with pytest.raises(InvalidRequestError):
            run(cat, trip, 232000, max_changes=bad)
    for bad in (-1, 1.5, None):
        with pytest.raises(InvalidRequestError):
            run(cat, trip, bad)


# ------------------------------------------------------------------ BR-D14 official catalog end to end

def test_BR_D14_official_catalog_rescues_are_valid(world):
    cat = load_catalog(OFFICIAL_CATALOG_DIR)
    raw = Raw(OFFICIAL_CATALOG_DIR)
    req = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-03", "nights": 8,
           "adults": 2, "children_ages": [6], "budget_inr": 600000}
    trip = build_trip(cat, req, "official")["trip"]
    from app.domain.edits import apply_change, list_alternatives
    for hotel in [i for i in trip["items"] if i["role"] == "hotel_stay"]:  # upgrade every stay -> pricey draft
        alts = list_alternatives(cat, trip, "hotel", {"item_id": hotel["item_id"], "limit": 50})
        trip = apply_change(cat, trip, max(alts, key=lambda a: a["price_diff_inr"])["change"])["trip"]
    locked = [next(i["catalog_id"] for i in trip["items"] if i["role"] == "attraction_visit")]
    target = trip["total_inr"] - 40000
    r = run(cat, trip, target, locked=locked)
    assert r["status"] == "RESCUED" and 1 <= len(r["proposals"]) <= 3
    for p in r["proposals"]:
        assert p["target_achieved"] and p["proposed_total_inr"] <= target
        assert validate_trip(cat, p["trip"], "modified") == [] and judge(raw, p["trip"], initial=False) == []
        assert_preserved(trip, p, locked)
    assert run(cat, trip, 1000)["status"] == "NO_RESCUE_FOUND"


def test_booked_or_invalid_drafts_rejected(world):
    cat, _, trip = world
    booked = copy.deepcopy(trip)
    for i in booked["items"]:
        i.update(status="BOOKED", booking={"reference": "0123456789"})
    with pytest.raises(InvalidRequestError):
        run(cat, booked, 240000)
    tampered = copy.deepcopy(trip)
    tampered["items"][0]["price_inr"] -= 1
    tampered["total_inr"] -= 1
    with pytest.raises(InvalidRequestError):
        run(cat, tampered, 240000)
