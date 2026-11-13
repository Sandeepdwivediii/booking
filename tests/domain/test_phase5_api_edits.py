"""Phase 5 integration: real PATCH / alternatives routes + real DomainPort + disposable PostgreSQL.

Requires the platform (Sandeep's Phase 3+: PATCH, alternatives) and TEST_DATABASE_URL; otherwise the
whole module is reported BLOCKED (explicit skip). Fixtures are shared with the Phase 4 round trip.
"""

from __future__ import annotations

import importlib.util
import uuid

import pytest

if importlib.util.find_spec("app.main") is None:
    pytest.skip("BLOCKED: platform API (app.main) is not on this branch; run on the integration candidate",
                allow_module_level=True)

from app.contracts import trip_shape_errors  # noqa: E402
from app.domain.pricing import price_trip  # noqa: E402
from tests.domain.test_phase4_api_roundtrip import (  # noqa: E402,F401  (fixtures)
    MENON, UAE, client, domain, engine, package_rows, test_url,
)


def patch(client, pid, version, change, key=None):
    return client.patch(f"/api/packages/{pid}", json=change,
                        headers={"If-Match": f'"{version}"', "Idempotency-Key": key or f"k-{uuid.uuid4()}"})


def built(client, req):
    r = client.post("/api/packages/build", json=req)
    assert r.status_code == 201, r.text
    return r.json()["package_id"], r.json()["trip"]


def assert_export_valid(client, domain, pid):
    exported = client.get(f"/api/packages/{pid}/export").json()
    assert trip_shape_errors(exported, draft=True) == []
    assert domain.validate(exported, "modified") == []
    priced = price_trip(exported, domain.catalog)
    assert exported["total_inr"] == priced["total_inr"]
    return exported


def test_flight_alternative_patch_persists_new_version(client, engine, domain):
    pid, trip = built(client, MENON)
    alts = client.get(f"/api/packages/{pid}/alternatives", params={"component": "outbound_flight"})
    assert alts.status_code == 200
    choice = alts.json()["alternatives"][0]
    r = patch(client, pid, 1, choice["change"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 2 and r.headers["etag"] == '"2"'
    assert body["trip"]["total_inr"] == choice["new_total_inr"] == trip["total_inr"] + choice["price_diff_inr"]
    assert body["trip"]["trip_id"] == pid
    (row,) = package_rows(engine)
    assert row["version"] == 2 and row["total_inr"] == choice["new_total_inr"] and row["trip"] == body["trip"]
    exported = assert_export_valid(client, domain, pid)
    out = next(i for i in exported["items"] if i["role"] == "outbound_flight")
    assert out["catalog_id"] == choice["catalog_id"]


def test_hotel_alternative_patch_reports_adjustments(client, domain):
    pid, trip = built(client, UAE)
    hotel = next(i for i in trip["items"] if i["role"] == "hotel_stay")
    alts = client.get(f"/api/packages/{pid}/alternatives", params={"component": "hotel", "item_id": hotel["item_id"]})
    assert alts.status_code == 200 and alts.json()["alternatives"]
    choice = alts.json()["alternatives"][-1]
    r = patch(client, pid, 1, choice["change"])
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["adjustments"], list)
    assert r.json()["trip"]["total_inr"] == choice["new_total_inr"]
    assert_export_valid(client, domain, pid)


def test_attraction_add_then_remove_via_api(client, domain):
    pid, trip = built(client, MENON)
    day = trip["start_date"][:8] + "12"
    alts = client.get(f"/api/packages/{pid}/alternatives", params={"component": "attraction", "date": day}).json()
    choice = alts["alternatives"][0]
    assert choice["change"]["start_time"]
    r = patch(client, pid, 1, choice["change"])
    assert r.status_code == 200, r.text
    added = next(i for i in r.json()["trip"]["items"] if i["catalog_id"] == choice["catalog_id"])
    r2 = patch(client, pid, 2, {"op": "remove_attraction", "item_id": added["item_id"]})
    assert r2.status_code == 200 and r2.json()["version"] == 3
    assert r2.json()["trip"]["total_inr"] == trip["total_inr"]
    assert_export_valid(client, domain, pid)


@pytest.mark.parametrize("change, code", [
    ({"op": "remove_attraction", "item_id": "i999"}, "ITEM_NOT_FOUND"),  # ED-08
    ({"op": "replace_flight", "item_id": "i1", "flight_id": "FL-NOPE"}, "UNKNOWN_CATALOG_ID"),
    ({"op": "add_attraction", "attraction_id": "ATT-NAR-001", "date": "2027-04-12"}, "ATTRACTION_WRONG_CITY"),  # ED-05
])
def test_rejected_change_leaves_package_unchanged(client, engine, change, code):
    pid, trip = built(client, MENON)
    r = patch(client, pid, 1, change)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_CHANGE"
    assert code in {v["code"] for v in r.json()["error"]["details"]}
    (row,) = package_rows(engine)
    assert row["version"] == 1 and row["trip"] == trip and row["total_inr"] == trip["total_inr"]


def test_idempotent_replay_and_stale_version(client, engine):
    pid, trip = built(client, MENON)
    visit = next(i for i in trip["items"] if i["role"] == "attraction_visit")
    change = {"op": "remove_attraction", "item_id": visit["item_id"]}
    first = patch(client, pid, 1, change, key="replay-key-1")
    again = patch(client, pid, 1, change, key="replay-key-1")
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    assert package_rows(engine)[0]["version"] == 2
    stale = patch(client, pid, 1, {"op": "remove_attraction", "item_id": visit["item_id"]})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
