"""Phase 2: persisted drafts (GET/reload), transaction helpers, judge export (draft + booked shape).
Trips are TEST-ONLY synthetic fixtures persisted in the disposable test DB (no catalog data)."""

from __future__ import annotations

import copy
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.contracts import booked_trip_shape_errors, trip_shape_errors
from app.db import make_sessionmaker
from app.services.export import ExportError, booked_export, draft_export, TRIP_KEY_ORDER
from app.services.packages import get_package, insert_draft, transaction
from tests.platform.conftest import make_client
from tests.platform.fixtures.contract_fixtures import sample_trip
from tests.platform.fixtures.fake_domain import make_trip

REQ = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10",
       "nights": 6, "adults": 2, "children_ages": [6], "budget_inr": 350000}
REF = "0123456789"


def persist(db, trip=None) -> tuple[str, dict]:
    pid = str(uuid.uuid4())
    trip = copy.deepcopy(trip) if trip else make_trip(REQ, pid)
    trip["trip_id"] = pid
    with transaction(make_sessionmaker(db)) as s:
        insert_draft(s, pid, REQ, trip)
    return pid, trip


def book(db, pid, trip, reference=REF, snapshot=None):
    snap = snapshot if snapshot is not None else booked_export(trip, reference)
    with db.begin() as c:
        c.execute(text("UPDATE app_packages SET status='BOOKED' WHERE id=:id"), {"id": pid})
        c.execute(text("INSERT INTO app_reservations (reference, package_id, snapshot) VALUES (:r, :p, CAST(:s AS JSONB))"),
                  {"r": reference, "p": pid, "s": __import__("json").dumps(snap)})


# ---------------------------------------------------------------- GET /api/packages/{id}

def test_get_saved_draft(client, db):
    pid, trip = persist(db)
    r = client.get(f"/api/packages/{pid}")
    assert r.status_code == 200
    assert r.headers["etag"] == '"1"' and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert (body["package_id"], body["version"], body["status"], body["reference"]) == (pid, 1, "DRAFT", None)
    assert body["trip"] == trip and body["created_at"] and body["updated_at"]


def test_get_accepts_uppercase_uuid(client, db):
    pid, _ = persist(db)
    assert client.get(f"/api/packages/{pid.upper()}").json()["package_id"] == pid


@pytest.mark.parametrize("bad", [str(uuid.uuid4()), "abc", "1' OR '1'='1", "x" * 5000, "%00"])
def test_get_unknown_or_malformed_is_404(client, db, bad):
    r = client.get(f"/api/packages/{bad}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert "Traceback" not in r.text and len(r.content) < 500


def test_build_then_get_roundtrip(client, db):
    b = client.post("/api/packages/build", json=REQ)
    assert b.status_code == 201 and b.headers["location"] == f"/api/packages/{b.json()['package_id']}"
    g = client.get(b.headers["location"]).json()
    assert g["trip"] == b.json()["trip"] and g["version"] == 1


def test_draft_survives_app_restart(db, test_db_url, fake_domain):  # BK-15 groundwork
    loader = lambda _d: (fake_domain, None)  # noqa: E731
    with make_client(test_db_url, loader) as c1:
        built = c1.post("/api/packages/build", json=REQ).json()
    with make_client(test_db_url, loader) as c2:  # fresh app + engine = backend restart
        again = c2.get(f"/api/packages/{built['package_id']}").json()
    assert again["trip"] == built["trip"] and again["version"] == 1 and again["status"] == "DRAFT"


def test_get_when_db_down_is_503(fake_domain):
    with make_client("postgresql+psycopg://n:x@127.0.0.1:1/bookingDB_test", lambda _d: (fake_domain, None)) as c:
        r = c.get(f"/api/packages/{uuid.uuid4()}")
    assert r.status_code == 503 and r.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------- transaction helpers

def test_transaction_rolls_back_on_error(db):
    pid = str(uuid.uuid4())
    with pytest.raises(RuntimeError), transaction(make_sessionmaker(db)) as s:
        insert_draft(s, pid, REQ, make_trip(REQ, pid))
        raise RuntimeError("fail before commit")
    with db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM app_packages")).scalar_one() == 0


def test_get_package_lock_blocks_concurrent_lockers(db):
    pid, _ = persist(db)
    with transaction(make_sessionmaker(db)) as s:
        get_package(s, pid, lock=True)  # SELECT ... FOR UPDATE held until commit
        with db.connect() as other, pytest.raises(OperationalError):
            other.execute(text("SELECT id FROM app_packages WHERE id=:id FOR UPDATE NOWAIT"), {"id": pid})
    with db.connect() as other:  # released after commit
        other.execute(text("SELECT id FROM app_packages WHERE id=:id FOR UPDATE NOWAIT"), {"id": pid})


# ---------------------------------------------------------------- export (judge JSON)

def test_draft_export_full_and_judge_shaped(client, db):  # SY-06/07/10/12 shape
    pid, trip = persist(db)
    r = client.get(f"/api/packages/{pid}/export")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == f'attachment; filename="trip-{pid}.json"'
    body = r.json()
    assert trip_shape_errors(body, draft=True) == []
    assert body["status"] == "DRAFT" and body["trip_id"] == pid
    assert len(body["items"]) == len(trip["items"])  # whole itinerary, not a 2-item sample
    assert [i["catalog_id"] for i in body["items"]] == [i["catalog_id"] for i in trip["items"]]
    assert all(i["status"] == "PLANNED" and i["booking"] is None and i["sources"] == [] for i in body["items"])
    assert all(i["room_id"] for i in body["items"] if i["type"] == "hotel")
    assert body["total_inr"] == sum(i["price_inr"] for i in body["items"])
    assert list(body)[:len(TRIP_KEY_ORDER)] == list(TRIP_KEY_ORDER)


def test_export_two_city_trip(client, db):
    pid, trip = persist(db, sample_trip())
    body = client.get(f"/api/packages/{pid}/export").json()
    assert trip_shape_errors(body, draft=True) == [] and body["items"][2]["room_id"] == trip["items"][2]["room_id"]


def test_export_refuses_corrupted_draft(client, db):
    pid, _ = persist(db)
    with db.begin() as c:  # e.g. a hotel item lost its room_id
        c.execute(text("UPDATE app_packages SET trip = jsonb_set(trip, '{items,2,room_id}', 'null') WHERE id=:id"), {"id": pid})
    r = client.get(f"/api/packages/{pid}/export")
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR" and "room_id" not in r.text


def test_booked_export_uses_immutable_snapshot(client, db):  # BK-06 / BK-14 shape groundwork
    pid, trip = persist(db)
    book(db, pid, trip)
    body = client.get(f"/api/packages/{pid}/export").json()
    assert booked_trip_shape_errors(body, REF) == []
    assert all(i["booking"] == {"reference": "0123456789"} for i in body["items"])
    g = client.get(f"/api/packages/{pid}").json()
    assert g["status"] == "BOOKED" and g["reference"] == "0123456789"


def test_booked_without_snapshot_or_mixed_snapshot_refused(client, db):
    pid, _ = persist(db)
    with db.begin() as c:
        c.execute(text("UPDATE app_packages SET status='BOOKED' WHERE id=:id"), {"id": pid})
    assert client.get(f"/api/packages/{pid}/export").status_code == 500
    pid2, trip2 = persist(db)
    mixed = booked_export(trip2, REF)
    mixed["items"][0]["status"] = "PLANNED"  # SY-11 style partial booking
    book(db, pid2, trip2, snapshot=mixed)
    assert client.get(f"/api/packages/{pid2}/export").status_code == 500


def test_export_unknown_is_404(client, db):
    assert client.get(f"/api/packages/{uuid.uuid4()}/export").status_code == 404


# ---------------------------------------------------------------- serializer units

def test_draft_export_normalizes_and_does_not_mutate():
    trip = make_trip(REQ, "t1")
    trip["items"][0].update(status="BOOKED", booking={"reference": REF})
    before = copy.deepcopy(trip)
    out = draft_export(trip)
    assert trip == before
    assert all(i["status"] == "PLANNED" and i["booking"] is None for i in out["items"])


@pytest.mark.parametrize("ref", ["123", 123456789, "01234567890", "０１２３４５６７８９"])
def test_booked_export_rejects_bad_reference(ref):
    with pytest.raises(ExportError):
        booked_export(make_trip(REQ, "t1"), ref)
