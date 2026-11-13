"""Phase 3 transaction machinery on REAL PostgreSQL (disposable test DB) with a deterministic
TEST-ONLY DomainPort: row lock, exact If-Match, idempotency replay before version check,
atomicity under injected crashes, reference collisions, and real concurrent requests.
ED-*/BK-* ids noted here are machinery groundwork; they are formally re-run in Phases 5/6."""

from __future__ import annotations

import threading
import time
import uuid

import pytest
from sqlalchemy import text

from app.api.errors import ApiError
from app.contracts import REFERENCE_RE, booked_trip_shape_errors
from app.db import make_sessionmaker
from app.services import transactions as tx
from tests.platform.fixtures.db_helpers import count, package_row, persist
from tests.platform.fixtures.fake_domain import ApiFakeDomain

REMOVE = {"op": "remove_attraction", "item_id": "i4"}


def key() -> str:
    return f"k-{uuid.uuid4()}"


def patch(client, pid, change, version="1", k=None, **hdr):
    headers = {"If-Match": f'"{version}"', "Idempotency-Key": k or key(), **hdr}
    return client.patch(f"/api/packages/{pid}", json=change, headers={h: v for h, v in headers.items() if v is not None})


def confirm(client, pid, k=None):
    return client.post(f"/api/packages/{pid}/confirm", headers={"Idempotency-Key": k or key()})


# ---------------------------------------------------------------- PATCH over HTTP

def test_patch_success_increments_version_once(client, db):
    pid, trip = persist(db)
    before = package_row(db, pid)
    r = patch(client, pid, REMOVE)
    assert r.status_code == 200 and r.headers["etag"] == '"2"'
    body = r.json()
    assert body["version"] == 2 and all(i["item_id"] != "i4" for i in body["trip"]["items"])
    row = package_row(db, pid)
    assert row["version"] == 2 and row["trip"] == body["trip"] and row["total_inr"] == body["trip"]["total_inr"]
    assert row["updated_at"] > before["updated_at"]
    assert count(db, "app_idempotency_records", pid) == 1


@pytest.mark.parametrize("headers, status, code", [
    ({"If-Match": None}, 428, "PRECONDITION_REQUIRED"),
    ({"If-Match": "abc"}, 422, "VALIDATION_ERROR"),
    ({"If-Match": '"-1"'}, 422, "VALIDATION_ERROR"),
    ({"Idempotency-Key": None}, 422, "VALIDATION_ERROR"),
    ({"Idempotency-Key": "short"}, 422, "VALIDATION_ERROR"),
    ({"Idempotency-Key": "bad key with spaces!"}, 422, "VALIDATION_ERROR"),
])
def test_patch_header_validation(client, db, headers, status, code):
    pid, _ = persist(db)
    base = {"If-Match": '"1"', "Idempotency-Key": key()}
    base.update(headers)
    r = client.patch(f"/api/packages/{pid}", json=REMOVE, headers={h: v for h, v in base.items() if v is not None})
    assert r.status_code == status and r.json()["error"]["code"] == code
    assert package_row(db, pid)["version"] == 1


@pytest.mark.parametrize("value", ['W/"1"', "1", '"1"'])
def test_if_match_forms_accepted(client, db, value):
    pid, _ = persist(db)
    r = client.patch(f"/api/packages/{pid}", json=REMOVE, headers={"If-Match": value, "Idempotency-Key": key()})
    assert r.status_code == 200


@pytest.mark.parametrize("body", [{"op": "teleport"}, {"op": "remove_attraction"}, {"op": "remove_attraction", "item_id": "i4", "price_inr": 0},
                                  {"op": "add_attraction", "attraction_id": "X", "date": "2027-4-1"}, [], "x"])
def test_patch_body_validation(client, db, body):
    pid, _ = persist(db)
    r = patch(client, pid, body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert count(db, "app_idempotency_records") == 0


def test_patch_unknown_package_404(client, db):
    assert patch(client, str(uuid.uuid4()), REMOVE).status_code == 404


def test_stale_version_conflict_leaves_row_identical(client, db):  # ED-12/ED-19 groundwork
    pid, _ = persist(db)
    assert patch(client, pid, REMOVE).status_code == 200
    before = package_row(db, pid)
    r = patch(client, pid, {"op": "remove_attraction", "item_id": "i5"}, version="1")
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_CONFLICT"
    assert r.json()["error"]["details"] == {"current_version": 2}
    assert package_row(db, pid) == before


@pytest.mark.parametrize("change, code", [
    ({"op": "remove_attraction", "item_id": "i999"}, "UNKNOWN_ITEM"),
    ({"op": "replace_hotel", "item_id": "i3", "hotel_id": "FAKE-HTL-PRICEY", "room_id": "FAKE-ROOM-X"}, "OVER_BUDGET"),  # ED-11 groundwork
])
def test_rejected_change_leaves_row_identical(client, db, change, code):
    pid, _ = persist(db)
    before = package_row(db, pid)
    k = key()
    r = patch(client, pid, change, k=k)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_CHANGE"
    assert r.json()["error"]["details"][0]["code"] == code
    assert package_row(db, pid) == before  # incl. updated_at and version
    again = patch(client, pid, change, k=k)  # rejected outcome replays identically
    assert (again.status_code, again.json()) == (r.status_code, r.json())


def test_same_key_same_payload_replays_after_version_advanced(client, db):  # ED-14 groundwork
    pid, _ = persist(db)
    k = key()
    first = patch(client, pid, REMOVE, k=k)
    assert patch(client, pid, {"op": "remove_attraction", "item_id": "i5"}, version="2").status_code == 200
    replay = patch(client, pid, REMOVE, version="1", k=k)  # stale If-Match, but replay wins
    assert replay.status_code == 200 and replay.json() == first.json()
    assert package_row(db, pid)["version"] == 3


def test_same_key_different_payload_conflicts(client, db):  # ED-15 groundwork
    pid, _ = persist(db)
    k = key()
    assert patch(client, pid, REMOVE, k=k).status_code == 200
    before = package_row(db, pid)
    r = patch(client, pid, {"op": "remove_attraction", "item_id": "i5"}, version="2", k=k)
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert package_row(db, pid) == before


def test_add_attraction_and_replace_hotel(client, db):
    pid, _ = persist(db)
    r = patch(client, pid, {"op": "add_attraction", "attraction_id": "FAKE-ATT-NEW", "date": "2027-04-12", "start_time": "15:00"})
    assert r.status_code == 200 and any(i["catalog_id"] == "FAKE-ATT-NEW" for i in r.json()["trip"]["items"])
    r = patch(client, pid, {"op": "replace_hotel", "item_id": "i3", "hotel_id": "FAKE-HTL-ALT", "room_id": "FAKE-ROOM-ALT"}, version="2")
    assert r.status_code == 200 and r.json()["version"] == 3


# ---------------------------------------------------------------- CONFIRM / lookup over HTTP

def test_confirm_books_latest_version(client, db):  # BK-10 groundwork
    pid, _ = persist(db)
    edited = patch(client, pid, REMOVE).json()["trip"]
    r = confirm(client, pid)
    assert r.status_code == 201
    res = r.json()
    assert REFERENCE_RE.fullmatch(res["reference"]) and isinstance(res["reference"], str)
    assert booked_trip_shape_errors(res["trip"], res["reference"]) == []
    assert [i["catalog_id"] for i in res["trip"]["items"]] == [i["catalog_id"] for i in edited["items"]]
    assert package_row(db, pid)["status"] == "BOOKED"
    look = client.get(f"/api/reservations/{res['reference']}")
    assert look.status_code == 200 and look.json() == res
    assert client.get(f"/api/packages/{pid}").json()["reference"] == res["reference"]


def test_confirm_retries_return_same_reference(client, db):  # BK-02/BK-09 groundwork
    pid, _ = persist(db)
    k = key()
    first = confirm(client, pid, k)
    replay = confirm(client, pid, k)
    other = confirm(client, pid)
    assert first.status_code == replay.status_code == 201 and replay.json() == first.json()
    assert other.status_code == 200 and other.json()["reference"] == first.json()["reference"]
    assert count(db, "app_reservations") == 1


def test_patch_after_booked_rejected(client, db):  # ED-18 groundwork
    pid, _ = persist(db)
    snap = confirm(client, pid).json()
    r = patch(client, pid, REMOVE)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_BOOKED"
    assert client.get(f"/api/reservations/{snap['reference']}").json() == snap


def test_confirm_rejected_by_validation(client, db, fake_domain):  # BK-11 style groundwork
    pid, _ = persist(db)
    fake_domain.confirm_violations = [{"code": "HOTEL_NO_AVAILABILITY", "message": "Sold out on 2027-04-12.", "item_id": "i3"}]
    r = confirm(client, pid)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CONFIRM_REJECTED"
    assert package_row(db, pid)["status"] == "DRAFT" and count(db, "app_reservations") == 0


def test_confirm_requires_key_and_known_package(client, db):
    pid, _ = persist(db)
    assert client.post(f"/api/packages/{pid}/confirm").status_code == 422
    assert confirm(client, str(uuid.uuid4())).status_code == 404


@pytest.mark.parametrize("ref", ["9999999999", "123", "abcdefghij", "0123456789 ", "x" * 300])
def test_reservation_lookup_not_found(client, db, ref):  # BK-12/BK-13 groundwork
    r = client.get(f"/api/reservations/{ref}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_alternatives_route(client, db):
    pid, _ = persist(db)
    r = client.get(f"/api/packages/{pid}/alternatives", params={"component": "hotel", "item_id": "i3"})
    assert r.status_code == 200 and len(r.json()["alternatives"]) == 2
    assert client.get(f"/api/packages/{pid}/alternatives", params={"component": "spa"}).status_code == 422
    r = client.get(f"/api/packages/{pid}/alternatives", params={"component": "attraction"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_REQUEST"
    assert client.get(f"/api/packages/{pid}/alternatives", params={"component": "attraction", "date": "2027-04-16"}).json() == {"alternatives": []}
    confirm(client, pid)
    assert client.get(f"/api/packages/{pid}/alternatives", params={"component": "hotel", "item_id": "i3"}).status_code == 409


# ---------------------------------------------------------------- atomicity (injected crashes)

def _crash_at(monkeypatch, point):
    def cp(name):
        if name == point:
            raise RuntimeError(f"injected crash at {name}")
    monkeypatch.setattr(tx, "checkpoint", cp)


def test_crash_between_update_and_idempotency_insert_rolls_back(db, monkeypatch):  # ED-16 groundwork
    pid, _ = persist(db)
    before = package_row(db, pid)
    _crash_at(monkeypatch, "patch:after_update")
    with pytest.raises(RuntimeError):
        tx.edit_package(make_sessionmaker(db), ApiFakeDomain(), pid, 1, key(), REMOVE)
    assert package_row(db, pid) == before and count(db, "app_idempotency_records") == 0


@pytest.mark.parametrize("point", ["confirm:after_reservation_insert", "confirm:after_status_update"])  # BK-07/BK-08 groundwork
def test_crash_during_confirm_rolls_back_everything(db, monkeypatch, point):
    pid, _ = persist(db)
    before = package_row(db, pid)
    _crash_at(monkeypatch, point)
    factory, k = make_sessionmaker(db), key()
    with pytest.raises(RuntimeError):
        tx.confirm_package(factory, ApiFakeDomain(), pid, k)
    assert package_row(db, pid) == before
    assert count(db, "app_reservations") == 0 and count(db, "app_idempotency_records") == 0
    monkeypatch.setattr(tx, "checkpoint", lambda _n: None)
    status, body = tx.confirm_package(factory, ApiFakeDomain(), pid, k)  # still confirmable
    assert status == 201 and REFERENCE_RE.fullmatch(body["reference"])


def test_reference_collision_retries(db):  # BK-05 / BK-06 groundwork
    factory = make_sessionmaker(db)
    p1, _ = persist(db)
    p2, _ = persist(db)
    assert tx.confirm_package(factory, ApiFakeDomain(), p1, key(), rng=lambda: "0000000001")[1]["reference"] == "0000000001"
    refs = iter(["0000000001", "0000000001", "0000000002"])
    status, body = tx.confirm_package(factory, ApiFakeDomain(), p2, key(), rng=lambda: next(refs))
    assert status == 201 and body["reference"] == "0000000002"
    with db.connect() as c:
        assert c.execute(text("SELECT package_id FROM app_reservations WHERE reference='0000000001'")).scalar_one() == p1


def test_reference_exhaustion_writes_nothing(db):
    factory = make_sessionmaker(db)
    p1, _ = persist(db)
    p2, _ = persist(db)
    tx.confirm_package(factory, ApiFakeDomain(), p1, key(), rng=lambda: "5555555555")
    with pytest.raises(ApiError) as e:
        tx.confirm_package(factory, ApiFakeDomain(), p2, key(), rng=lambda: "5555555555")
    assert e.value.status == 503
    assert package_row(db, p2)["status"] == "DRAFT" and count(db, "app_reservations") == 1


def test_lock_timeout_returns_503_not_hang(db, monkeypatch):
    pid, _ = persist(db)
    monkeypatch.setattr(tx, "LOCK_TIMEOUT", "300ms")
    with db.connect() as holder:
        trans = holder.begin()
        holder.execute(text("SELECT id FROM app_packages WHERE id=:id FOR UPDATE"), {"id": pid})
        t0 = time.monotonic()
        from sqlalchemy.exc import OperationalError
        with pytest.raises(OperationalError):
            tx.edit_package(make_sessionmaker(db), ApiFakeDomain(), pid, 1, key(), REMOVE)
        assert time.monotonic() - t0 < 3
        trans.rollback()
    assert package_row(db, pid)["version"] == 1


# ---------------------------------------------------------------- real concurrency (threads + PostgreSQL row locks)

class CountingDomain(ApiFakeDomain):
    """Counts domain calls: proves losers are decided under the lock without planning on stale data."""

    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.change_calls = self.validate_calls = 0

    def change(self, trip, change):
        with self.lock:
            self.change_calls += 1
        return super().change(trip, change)

    def validate(self, trip, mode):
        with self.lock:
            self.validate_calls += 1
        return super().validate(trip, mode)


def _run_concurrently(fns):
    barrier = threading.Barrier(len(fns))
    results = [None] * len(fns)

    def worker(i, fn):
        barrier.wait()
        try:
            results[i] = fn()
        except ApiError as e:
            results[i] = (e.status, {"error": {"code": e.code}})

    threads = [threading.Thread(target=worker, args=(i, f)) for i, f in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


def test_two_tabs_same_version_one_wins(db):  # ED-12 groundwork
    pid, _ = persist(db)
    domain, factory = CountingDomain(), make_sessionmaker(db)
    domain.change_delay = 0.4  # first holder plans while holding the row lock
    res = _run_concurrently([
        lambda: tx.edit_package(factory, domain, pid, 1, key(), REMOVE),
        lambda: tx.edit_package(factory, domain, pid, 1, key(), {"op": "remove_attraction", "item_id": "i5"}),
    ])
    statuses = sorted(r[0] for r in res)
    assert statuses == [200, 409]
    assert next(r for r in res if r[0] == 409)[1]["error"]["code"] == "VERSION_CONFLICT"
    assert package_row(db, pid)["version"] == 2
    assert domain.change_calls == 1  # loser waited for the lock and saw the new version; it never planned


def test_same_key_concurrent_duplicates_apply_once(db):
    pid, _ = persist(db)
    domain, factory, k = CountingDomain(), make_sessionmaker(db), key()
    domain.change_delay = 0.3
    res = _run_concurrently([lambda: tx.edit_package(factory, domain, pid, 1, k, REMOVE)] * 2)
    assert res[0] == res[1] and res[0][0] == 200
    assert package_row(db, pid)["version"] == 2 and count(db, "app_idempotency_records") == 1
    assert domain.change_calls == 1  # second request replayed the stored response


def test_patch_vs_confirm_is_serializable(db):  # ED-13 groundwork
    for _ in range(3):
        pid, _ = persist(db)
        domain, factory = ApiFakeDomain(), make_sessionmaker(db)
        domain.change_delay = 0.2
        p, c = _run_concurrently([
            lambda: tx.edit_package(factory, domain, pid, 1, key(), REMOVE),
            lambda: tx.confirm_package(factory, domain, pid, key()),
        ])
        assert c[0] == 201
        final = package_row(db, pid)
        snap_ids = [i["catalog_id"] for i in c[1]["trip"]["items"]]
        assert snap_ids == [i["catalog_id"] for i in final["trip"]["items"]]  # booked == last committed draft
        if p[0] == 200:
            assert final["version"] == 2 and "FAKE-ATTRACTION_VISIT-4" not in snap_ids
        else:
            assert p[1]["error"]["code"] == "ALREADY_BOOKED" and final["version"] == 1


def test_twenty_concurrent_confirms_one_reservation(db):  # BK-01/BK-09 groundwork
    pid, _ = persist(db)
    domain, factory = CountingDomain(), make_sessionmaker(db)
    res = _run_concurrently([lambda: tx.confirm_package(factory, domain, pid, key()) for _ in range(20)])
    refs = {r[1]["reference"] for r in res}
    assert len(refs) == 1 and sorted(r[0] for r in res) == [200] * 19 + [201]
    assert count(db, "app_reservations") == 1
    assert domain.validate_calls == 1  # the other 19 waited on the row lock and found it BOOKED


def test_two_packages_confirm_concurrently_distinct(db):  # BK-04 groundwork
    p1, _ = persist(db)
    p2, _ = persist(db)
    domain, factory = ApiFakeDomain(), make_sessionmaker(db)
    a, b = _run_concurrently([lambda: tx.confirm_package(factory, domain, p1, key()),
                              lambda: tx.confirm_package(factory, domain, p2, key())])
    assert a[0] == b[0] == 201 and a[1]["reference"] != b[1]["reference"]
    assert {a[1]["package_id"], b[1]["package_id"]} == {p1, p2}


# ---------------------------------------------------------------- UI wiring (browser smoke run separately)

def test_ui_has_edit_confirm_and_lookup_wiring(client):
    page = client.get("/").text
    assert 'id="lookup-form"' in page and 'id="lookup-ref"' in page
    js = client.get("/static/app.js").text
    for needle in ('"If-Match"', '"Idempotency-Key"', "/alternatives?", "/confirm", "/api/reservations/",
                   "VERSION_CONFLICT", "ALREADY_BOOKED", "INVALID_CHANGE", "CONFIRM_REJECTED", "sessionStorage"):
        assert needle in js, needle
