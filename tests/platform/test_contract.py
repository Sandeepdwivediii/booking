"""Phase 0 contract tests: the shared interface is importable, pure, self-consistent, and
its executable shape checks accept valid fixtures and reject known-bad exports."""

from __future__ import annotations

import copy
import importlib.util
import inspect
import json
import subprocess
import sys

import pytest

from app import contracts as c
from tests.platform.fixtures.contract_fixtures import (
    MENON_REQUEST, TRIP_ID, FakeDomain, built_result, no_package_result, over_budget_result, sample_trip,
)


# ---------------------------------------------------------------- purity / interface

def test_contracts_module_is_stdlib_only():
    code = ("import sys, app.contracts; "
            "bad=[m for m in ('fastapi','sqlalchemy','pydantic','psycopg','starlette') if m in sys.modules]; "
            "print(','.join(bad))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


EXPECTED_SIGNATURES = {
    "options": ["self"],
    "build": ["self", "request", "trip_id"],
    "validate": ["self", "trip", "mode"],
    "alternatives": ["self", "trip", "component", "filters"],
    "change": ["self", "trip", "change"],
}


def test_domain_port_signatures_are_frozen():
    for name, params in EXPECTED_SIGNATURES.items():
        assert list(inspect.signature(getattr(c.DomainPort, name)).parameters) == params


def test_fake_domain_satisfies_port_and_signatures():
    fake = FakeDomain()
    assert isinstance(fake, c.DomainPort)
    for name, params in EXPECTED_SIGNATURES.items():
        assert list(inspect.signature(getattr(FakeDomain, name)).parameters) == params


def test_object_missing_method_is_not_a_port():
    class Partial:
        def options(self): return {}
    assert not isinstance(Partial(), c.DomainPort)


def test_enums_and_roles_consistent():
    assert c.BUILD_STATUSES == {"BUILT", "OVER_BUDGET", "NO_PACKAGE"}
    assert c.VALIDATION_MODES == {"initial", "modified", "confirm"}
    assert set(c.ROLE_TYPE) == c.ITEM_ROLES
    assert set(c.ROLE_TYPE.values()) == c.ITEM_TYPES
    assert set(c.BuildRequest.__annotations__) == {
        "departure_city", "destination_country", "start_date", "nights", "adults", "children_ages", "budget_inr"}


def test_errors_hierarchy_and_status_map():
    for cls in (c.CatalogUnavailableError, c.InvalidRequestError):
        err = cls("boom", {"x": 1})
        assert isinstance(err, c.DomainError) and err.message == "boom" and err.details == {"x": 1}
        assert c.API_ERROR_STATUS[cls.code] in (422, 503)
    assert c.API_ERROR_STATUS["VERSION_CONFLICT"] == 409
    assert c.API_ERROR_STATUS["ALREADY_BOOKED"] == 409
    assert c.API_ERROR_STATUS["NOT_FOUND"] == 404


# ---------------------------------------------------------------- build results

def test_built_fixture_conforms_and_is_json():
    res = built_result()
    assert c.build_result_shape_errors(res, MENON_REQUEST, TRIP_ID) == []
    assert json.loads(json.dumps(res)) == res


def test_built_trip_id_must_match():
    assert any("trip_id" in e for e in c.build_result_shape_errors(built_result("other"), MENON_REQUEST, TRIP_ID))


def test_built_over_budget_rejected():
    req = dict(MENON_REQUEST, budget_inr=1000)
    assert "BUILT trip exceeds budget" in c.build_result_shape_errors(built_result(), req, TRIP_ID)


def test_over_budget_contract():  # PR-09 shape: cheapest 80000, budget 79999 -> shortfall 1
    req = dict(MENON_REQUEST, budget_inr=79_999)
    assert c.build_result_shape_errors(over_budget_result(79_999, 80_000), req, TRIP_ID) == []
    bad = over_budget_result(79_999, 80_000); bad["shortfall_inr"] = 0
    assert c.build_result_shape_errors(bad, req, TRIP_ID)
    equal = over_budget_result(80_000, 80_000)  # PR-08: equality is within budget, not OVER_BUDGET
    assert c.build_result_shape_errors(equal, dict(MENON_REQUEST, budget_inr=80_000), TRIP_ID)
    with_trip = over_budget_result(79_999, 80_000); with_trip["trip"] = sample_trip()
    assert "OVER_BUDGET must have trip null" in c.build_result_shape_errors(with_trip, req, TRIP_ID)


def test_no_package_contract():  # PR-14: never a fake price
    assert c.build_result_shape_errors(no_package_result(), MENON_REQUEST, TRIP_ID) == []
    bad = no_package_result(); bad["cheapest_total_inr"] = 1
    assert c.build_result_shape_errors(bad, MENON_REQUEST, TRIP_ID)
    bad = no_package_result(); bad["reason"] = ""
    assert c.build_result_shape_errors(bad, MENON_REQUEST, TRIP_ID)


# ---------------------------------------------------------------- trip export shape (SY-07/09/10/12)

def _mutate(fn):
    t = sample_trip(); fn(t); return c.trip_shape_errors(t)


@pytest.mark.parametrize("label, fn", [
    ("hotel without room_id", lambda t: t["items"][2].update(room_id=None)),
    ("flight with room_id", lambda t: t["items"][0].update(room_id="X")),
    ("BOOKED in draft", lambda t: t["items"][0].update(status="BOOKED")),
    ("booking in draft", lambda t: t["items"][0].update(booking={"reference": "0123456789"})),
    ("sources omitted", lambda t: t["items"][1].pop("sources")),
    ("float price", lambda t: t["items"][1].update(price_inr=13440.0)),
    ("bool quantity", lambda t: t["items"][1].update(quantity=True)),
    ("total mismatch", lambda t: t.update(total_inr=t["total_inr"] + 1)),
    ("duplicate item id", lambda t: t["items"][1].update(item_id="i1")),
    ("role/type mismatch", lambda t: t["items"][0].update(role="hotel_stay")),
    ("missing country", lambda t: t.pop("country")),
    ("children count", lambda t: t["traveler"].update(children=2)),
    ("empty items", lambda t: t.update(items=[], total_inr=0)),
])
def test_trip_shape_rejects(label, fn):
    assert _mutate(fn), label


def test_booked_trip_fails_draft_check():
    t = sample_trip()
    t["status"] = "BOOKED"
    for it in t["items"]:
        it.update(status="BOOKED", booking={"reference": "0123456789"})
    assert c.booked_trip_shape_errors(t, "0123456789") == []
    assert c.trip_shape_errors(t, draft=True)


# ---------------------------------------------------------------- fake port behaviour (test double)

def test_fake_build_three_outcomes():
    d = FakeDomain()
    for req in (MENON_REQUEST, dict(MENON_REQUEST, budget_inr=0),
                dict(MENON_REQUEST, nights=10, start_date="2027-04-11")):
        assert c.build_result_shape_errors(d.build(req, TRIP_ID), req, TRIP_ID) == []
    assert d.build(dict(MENON_REQUEST, budget_inr=0), TRIP_ID)["status"] == "OVER_BUDGET"  # IN-13


def test_fake_change_is_pure_and_conforms():
    d, trip = FakeDomain(), sample_trip()
    before = copy.deepcopy(trip)
    ok = d.change(trip, {"op": "remove_attraction", "item_id": "i4"})
    assert c.change_result_shape_errors(ok, trip) == []
    assert trip == before  # input not mutated
    bad = d.change(trip, {"op": "remove_attraction", "item_id": "nope"})
    assert c.change_result_shape_errors(bad, trip) == [] and bad["ok"] is False


def test_fake_validate_and_alternatives_conform():
    d, trip = FakeDomain(), sample_trip()
    assert c.violation_shape_errors(d.validate(trip, "confirm")) == []
    with pytest.raises(c.InvalidRequestError):
        d.validate(trip, "bogus")
    alts = d.alternatives(trip, "attraction", {"date": "2027-04-12"})
    assert alts and alts[0]["change"]["op"] in c.CHANGE_OPS
    assert alts[0]["price_diff_inr"] == alts[0]["new_total_inr"] - trip["total_inr"]


def test_change_result_checker_rejects_bad():
    orig = sample_trip()
    assert c.change_result_shape_errors({"ok": False, "trip": None, "violations": [], "adjustments": []}, orig)
    t = sample_trip("different")
    assert c.change_result_shape_errors({"ok": True, "trip": t, "violations": [], "adjustments": []}, orig)
    assert c.violation_shape_errors([{"code": "bad code", "message": "x", "item_id": None}])


# ---------------------------------------------------------------- compatibility with real domain (later)

@pytest.mark.skipif(importlib.util.find_spec("app.domain") is None, reason="app.domain not merged yet")
def test_real_domain_factory_matches_contract():
    from app.domain.adapter import create_domain
    assert list(inspect.signature(create_domain).parameters)[:1] == ["catalog_dir"]
    domain = create_domain("data/catalog")
    assert isinstance(domain, c.DomainPort)
    for name, params in EXPECTED_SIGNATURES.items():
        assert list(inspect.signature(getattr(type(domain), name)).parameters) == params
    opts = domain.options()
    assert set(opts) >= {"departure_cities", "countries", "rules"}
    assert {d["airport"] for d in opts["departure_cities"]} == {"BLR", "DEL", "BOM", "MAA"}
