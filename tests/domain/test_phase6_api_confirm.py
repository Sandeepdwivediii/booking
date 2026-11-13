"""Phase 6 integration: real confirm / reservation / booked export + real DomainPort + disposable PostgreSQL.

Audits the platform's BOOKED output with the independent oracle (BK-14, SY-11), snapshot immutability,
and that confirm validates the LATEST persisted draft. BLOCKED (explicit skip) without the platform.
"""

from __future__ import annotations

import importlib.util
import uuid

import pytest

if importlib.util.find_spec("app.main") is None:
    pytest.skip("BLOCKED: platform API (app.main) is not on this branch; run on the integration candidate",
                allow_module_level=True)

from sqlalchemy import text  # noqa: E402

from tests.domain.test_phase4_api_roundtrip import (  # noqa: E402,F401  (fixtures)
    MENON, client, domain, engine, new_client, package_rows, test_url,
)
from tests.domain.test_phase5_api_edits import built, patch  # noqa: E402
from tests.domain_fixtures.booked_oracle import booked_export_problems  # noqa: E402


def confirm(client, pid, key=None):
    return client.post(f"/api/packages/{pid}/confirm", headers={"Idempotency-Key": key or f"c-{uuid.uuid4()}"})


def reservation_count(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM app_reservations")).scalar_one()


def test_BK14_SY11_booked_export_every_item_stamped(client, domain):
    pid, draft = built(client, MENON)
    r = confirm(client, pid)
    assert r.status_code == 201, r.text
    ref = r.json()["reference"]
    assert isinstance(ref, str) and len(ref) == 10 and ref.isdigit()
    exported = client.get(f"/api/packages/{pid}/export").json()
    assert booked_export_problems(exported, ref, draft) == []
    assert booked_export_problems(r.json()["trip"], ref, draft) == []
    assert domain.validate(exported, "confirm") == []  # booked snapshot is still a valid package


def test_snapshot_immutable_after_confirmation(client, engine, test_url):
    pid, draft = built(client, MENON)
    first = confirm(client, pid, key="confirm-key-0001").json()
    visit = next(i for i in draft["items"] if i["role"] == "attraction_visit")
    blocked = patch(client, pid, 1, {"op": "remove_attraction", "item_id": visit["item_id"]})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "ALREADY_BOOKED"
    retry = confirm(client, pid, key="confirm-key-0001")
    other_key = confirm(client, pid)
    assert retry.json() == first and other_key.json()["reference"] == first["reference"]
    assert reservation_count(engine) == 1
    with new_client(test_url) as restarted:  # survives backend restart
        looked_up = restarted.get(f"/api/reservations/{first['reference']}").json()
        assert looked_up["trip"] == first["trip"]
        assert restarted.get(f"/api/packages/{pid}/export").json() == first["trip"]


def test_BK10_confirm_books_latest_edited_version(client, domain):
    pid, draft = built(client, MENON)
    visit = next(i for i in draft["items"] if i["role"] == "attraction_visit")
    edited = patch(client, pid, 1, {"op": "remove_attraction", "item_id": visit["item_id"]})
    assert edited.status_code == 200
    booked = confirm(client, pid).json()
    assert booked_export_problems(booked["trip"], booked["reference"], edited.json()["trip"]) == []
    assert all(i["item_id"] != visit["item_id"] for i in booked["trip"]["items"])


def test_BK11_rejected_edit_then_confirm_books_last_valid_draft(client):
    pid, draft = built(client, MENON)
    bad = patch(client, pid, 1, {"op": "remove_attraction", "item_id": "i999"})
    assert bad.status_code == 422
    booked = confirm(client, pid).json()
    assert booked_export_problems(booked["trip"], booked["reference"], draft) == []


def test_invalid_stored_draft_is_rejected_at_confirm(client, engine):
    """A stale/tampered persisted draft (e.g. price edited in the DB) must fail validate(mode='confirm')."""
    pid, draft = built(client, MENON)
    with engine.begin() as conn:  # disposable test DB only
        conn.execute(text("UPDATE app_packages SET trip = jsonb_set(trip, '{items,0,price_inr}', '1') "
                          "WHERE id = :id"), {"id": pid})
    r = confirm(client, pid)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CONFIRM_REJECTED"
    codes = {v["code"] for v in r.json()["error"]["details"]}
    assert codes & {"PRICE_MISMATCH", "TRIP_MALFORMED"}
    assert reservation_count(engine) == 0 and package_rows(engine)[0]["status"] == "DRAFT"


def test_unknown_reference_not_found(client):
    assert client.get("/api/reservations/0000000000").status_code == 404
