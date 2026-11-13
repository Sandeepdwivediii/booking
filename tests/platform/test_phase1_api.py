"""Phase 1 API foundation: build persistence, options, error envelope, static form.
Domain = TEST-ONLY fakes (mocked)."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text

from app.contracts import InvalidRequestError
from tests.platform.conftest import make_client
from tests.platform.fixtures.fake_domain import ApiFakeDomain

VALID = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10",
         "nights": 6, "adults": 2, "children_ages": [6], "budget_inr": 350000}


def rows(db):
    with db.connect() as c:
        return c.execute(text("SELECT id, status, version, request, trip, total_inr, budget_inr FROM app_packages")).all()


def test_built_is_persisted_as_draft_v1(client, db):
    r = client.post("/api/packages/build", json=VALID)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "DRAFT" and body["version"] == 1
    [row] = rows(db)
    assert row.id == body["package_id"] == body["trip"]["trip_id"]
    assert (row.status, row.version) == ("DRAFT", 1)
    assert row.request == VALID and row.trip == body["trip"]
    assert row.total_inr == body["trip"]["total_inr"] and row.budget_inr == VALID["budget_inr"]


def test_each_build_gets_new_uuid(client, db):
    ids = {client.post("/api/packages/build", json=VALID).json()["package_id"] for _ in range(3)}
    assert len(ids) == 3 and len(rows(db)) == 3


def test_no_package_not_persisted(client, db):
    r = client.post("/api/packages/build", json={**VALID, "nights": 9})
    assert r.status_code == 200 and r.json()["status"] == "NO_PACKAGE" and r.json()["reason"]
    assert rows(db) == []


def test_options_from_domain(client):
    r = client.get("/api/catalog/options")
    assert r.status_code == 200
    assert {d["airport"] for d in r.json()["departure_cities"]} == {"BLR", "DEL", "BOM", "MAA"}


class _BadBuilt(ApiFakeDomain):
    def build(self, request, trip_id):
        res = super().build(request, trip_id)
        res["trip"]["end_date"] = "2027-04-15"  # contract violation
        return res


class _Crashing(ApiFakeDomain):
    def build(self, request, trip_id):
        raise RuntimeError("secret internal detail")


class _Rejecting(ApiFakeDomain):
    def build(self, request, trip_id):
        raise InvalidRequestError("domain rejected request")


@pytest.mark.parametrize("domain_cls, status, code", [
    (_BadBuilt, 500, "INTERNAL_ERROR"), (_Crashing, 500, "INTERNAL_ERROR"), (_Rejecting, 422, "INVALID_REQUEST"),
])
def test_domain_failures_use_envelope_and_persist_nothing(db, test_db_url, domain_cls, status, code):
    with make_client(test_db_url, lambda _d: (domain_cls(), None)) as c:
        r = c.post("/api/packages/build", json=VALID)
    assert r.status_code == status and r.json()["error"]["code"] == code
    assert "secret internal detail" not in r.text and "Traceback" not in r.text
    assert rows(db) == []


def test_unknown_route_envelope(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_static_form_has_seven_fields(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    for field in VALID:
        assert f'name="{field}"' in r.text or field == "children_ages"
    js = client.get("/static/app.js")
    assert js.status_code == 200 and "children_ages" in js.text
    assert not re.search(r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write", js.text)  # XSS
    assert client.get("/static/styles.css").status_code == 200
