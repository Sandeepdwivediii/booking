"""Phase 1 adversarial input tests IN-01..IN-16 against POST /api/packages/build.
Domain is the TEST-ONLY ApiFakeDomain (mocked); these verify platform validation, not planning."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

VALID = {
    "departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10",
    "nights": 6, "adults": 2, "children_ages": [6], "budget_inr": 350000,
}
FIELDS = list(VALID)


def post(client, body):
    raw = body if isinstance(body, (str, bytes)) else json.dumps(body)
    return client.post("/api/packages/build", content=raw, headers={"Content-Type": "application/json"})


def package_count(db) -> int:
    with db.connect() as c:
        return c.execute(text("SELECT count(*) FROM app_packages")).scalar_one()


def assert_422(resp, field=None, db=None):
    assert resp.status_code == 422, resp.text
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert "Traceback" not in resp.text
    if field:
        assert any(field in d["loc"] for d in err["details"]), err["details"]
    if db is not None:
        assert package_count(db) == 0


def req(**over):
    return {**VALID, **over}


# ---------------------------------------------------------------- P0

def test_IN_01_nights_below_minimum(client, db, fake_domain):
    r = post(client, req(nights=2))
    assert_422(r, "nights", db)
    assert "between 3 and 10" in r.json()["error"]["details"][0]["msg"]
    assert fake_domain.calls == []  # domain never called with invalid input


def test_IN_02_nights_above_maximum(client, db):
    assert_422(post(client, req(nights=11)), "nights", db)


@pytest.mark.parametrize("nights", [3, 10])
def test_IN_03_nights_boundaries_accepted(client, db, nights):
    r = post(client, req(nights=nights, start_date="2027-04-10"))
    assert r.status_code == 201, r.text
    assert r.json()["trip"]["end_date"] == ("2027-04-13" if nights == 3 else "2027-04-20")


def test_IN_04_start_before_first_night(client, db):
    r = post(client, req(start_date="2027-03-31"))
    assert_422(r, "start_date", db)
    assert "2027-04-01" in r.json()["error"]["details"][0]["msg"]


def test_IN_05_return_after_last_departure(client, db):
    r = post(client, req(start_date="2027-04-17", nights=4))
    assert_422(r, "start_date", db)
    assert "2027-04-21" in r.json()["error"]["details"][0]["msg"]


def test_IN_06_window_boundary_valid(client, db):
    r = post(client, req(start_date="2027-04-10", nights=10))
    assert r.status_code == 201, r.text
    assert r.json()["trip"]["end_date"] == "2027-04-20"


def test_IN_07_unknown_departure_city(client, db):
    assert_422(post(client, req(departure_city="XYZ")), "departure_city", db)


def test_IN_08_unknown_country(client, db):
    assert_422(post(client, req(destination_country="France")), "destination_country", db)


@pytest.mark.parametrize("ages", [[-1], [18], [None], [6, None]])
def test_IN_09_invalid_child_ages(client, db, ages):
    assert_422(post(client, req(children_ages=ages)), "children_ages", db)


def test_IN_10_child_age_boundaries_counted(client, db, fake_domain):
    r = post(client, req(children_ages=[0, 17]))
    assert r.status_code == 201, r.text
    assert fake_domain.calls[-1]["children_ages"] == [0, 17]
    trip = r.json()["trip"]
    assert trip["traveler"]["children"] == 2 and trip["traveler"]["child_ages"] == [0, 17]
    assert all(i["quantity"] == 4 for i in trip["items"] if i["type"] in ("flight", "attraction"))


@pytest.mark.parametrize("adults", [0, -1])
def test_IN_11_adults_must_be_positive(client, db, adults):
    assert_422(post(client, req(adults=adults)), "adults", db)


def test_IN_12_negative_budget(client, db):
    assert_422(post(client, req(budget_inr=-100)), "budget_inr", db)


# ---------------------------------------------------------------- P1

def test_IN_13_zero_budget_is_over_budget_not_no_package(client, db):
    r = post(client, req(budget_inr=0))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "OVER_BUDGET" and body["cheapest_total_inr"] > 0
    assert body["shortfall_inr"] == body["cheapest_total_inr"]
    assert package_count(db) == 0  # no fake DRAFT persisted


@pytest.mark.parametrize("field, value", [
    ("nights", "six"), ("nights", "6"), ("nights", 6.0), ("nights", True),
    ("budget_inr", "abc"), ("budget_inr", 1000.5), ("adults", 1.5), ("adults", "2"),
    ("children_ages", [{"age": 6}]), ("children_ages", ["6"]), ("children_ages", 6),
    ("start_date", "10/04/2027"), ("start_date", "2027-02-30"), ("start_date", "2027-W15-1"), ("start_date", 20270410),
    ("departure_city", ["BLR"]), ("destination_country", None),
])
def test_IN_14_type_errors_never_coerced(client, db, field, value):
    assert_422(post(client, req(**{field: value})), field, db)


@pytest.mark.parametrize("missing", FIELDS)
def test_IN_15_missing_required_field(client, db, missing):
    body = {k: v for k, v in VALID.items() if k != missing}
    r = post(client, body)
    assert_422(r, missing, db)
    assert any(d["type"] == "missing" for d in r.json()["error"]["details"])


@pytest.mark.parametrize("extra", [{"price": 1}, {"country": "Japan"}, {"end_date": "2027-04-30"},
                                   {"total_inr": 1}, {"trip_id": "x"}])
def test_IN_15_client_injected_fields_rejected(client, db, fake_domain, extra):
    r = post(client, {**VALID, **extra})
    assert_422(r, next(iter(extra)), db)
    assert fake_domain.calls == []


@pytest.mark.parametrize("body", ["[]", "{}", "null", '"text"', "{not json"])
def test_IN_15_empty_or_malformed_body(client, db, body):
    assert_422(post(client, body), db=db)


def test_IN_15_empty_children_array_is_valid(client, db):
    assert post(client, req(children_ages=[])).status_code == 201


# ---------------------------------------------------------------- P2

@pytest.mark.parametrize("field, value", [
    ("departure_city", "B" * 5000),
    ("destination_country", "J" * 5000),
    ("departure_city", "BLR'; DROP TABLE app_packages;--"),
    ("destination_country", "Japan' OR '1'='1"),
    ("children_ages", [1] * 21),
    ("budget_inr", 10**30),
    ("adults", 10**18),
    ("nights", -10**12),
])
def test_IN_16_hostile_values(client, db, field, value):
    r = post(client, req(**{field: value}))
    assert_422(r, field, db)
    assert len(r.content) < 4000  # raw input is never echoed back
    with db.connect() as c:  # SQL injection had no effect
        assert c.execute(text("SELECT to_regclass('app_packages')")).scalar_one() is not None


def test_IN_16_oversized_body_rejected_early(client, db, fake_domain):
    r = post(client, json.dumps(req(children_ages=[1] * 40_000)))
    assert_422(r, db=db)
    assert fake_domain.calls == []


def test_IN_16_very_large_valid_budget_ok(client, db):
    assert post(client, req(budget_inr=100_000_000)).status_code == 201
