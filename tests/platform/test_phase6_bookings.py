"""Phase 6 acceptance (M7): BK-01..BK-13, BK-16, SY-16 on REAL PostgreSQL with the REAL domain
(production loader, original catalog). Concurrency runs over real HTTP against a live uvicorn
server. BK-10/BK-11 need a real successful/rejected domain edit -> [real] BLOCKED until Tanish's
Phase 5; their [fake] variants only prove platform machinery."""

from __future__ import annotations

import copy
import dataclasses
import json
import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.contracts import REFERENCE_RE, booked_trip_shape_errors
from app.main import create_app
from app.services import transactions as tx
from tests.platform.conftest import make_client
from tests.platform.fixtures.catalog_oracle import RawCatalog, check_export
from tests.platform.fixtures.contract_fixtures import CATALOG_DIR
from tests.platform.fixtures.db_helpers import count, package_row
from tests.platform.fixtures.fake_domain import ApiFakeDomain

KEYS = ["departure_city", "destination_country", "start_date", "nights", "adults", "children_ages", "budget_inr"]
MENON = dict(zip(KEYS, ["BLR", "Japan", "2027-04-10", 6, 2, [6], 350000]))
BLOCKED = "BLOCKED: real DomainPort.change() not implemented yet (Tanish Phase 5)"


def key() -> str:
    return f"p6-{uuid.uuid4()}"


@pytest.fixture
def real(db, test_db_url):
    with make_client(test_db_url, None) as c:  # production loader -> Tanish's domain
        if type(c.app.state.runtime.domain).__module__ != "app.domain.adapter":
            pytest.skip("BLOCKED: real domain adapter not available")
        yield c


@pytest.fixture(scope="module")
def live(test_db_url, test_engine):
    """Real uvicorn server (threadpool, real sockets) with the production loader."""
    app = create_app(dataclasses.replace(get_settings(), database_url=test_db_url))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert type(app.state.runtime.domain).__module__ == "app.domain.adapter"  # real domain, no fake
    assert httpx.get(f"http://127.0.0.1:{port}/api/health").json()["status"] == "ready"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    th.join(10)


def build(client_or_url, req=MENON) -> dict:
    if isinstance(client_or_url, str):
        r = httpx.post(f"{client_or_url}/api/packages/build", json=req, timeout=30)
    else:
        r = client_or_url.post("/api/packages/build", json=req)
    assert r.status_code == 201, r.text
    return r.json()


def confirm(client, pid, k=None):
    return client.post(f"/api/packages/{pid}/confirm", headers={"Idempotency-Key": k or key()})


def http_burst(url: str, requests: list[tuple[str, str]]) -> list[httpx.Response]:
    """Fire (package_id, idempotency_key) confirms simultaneously over real HTTP."""
    barrier, out = threading.Barrier(len(requests)), [None] * len(requests)

    def go(i, pid, k):
        with httpx.Client(timeout=60) as c:
            barrier.wait()
            out[i] = c.post(f"{url}/api/packages/{pid}/confirm", headers={"Idempotency-Key": k})

    ts = [threading.Thread(target=go, args=(i, p, k)) for i, (p, k) in enumerate(requests)]
    [t.start() for t in ts]
    [t.join(90) for t in ts]
    return out


# ---------------------------------------------------------------- BK-01 / BK-04 / BK-09 (concurrency over real HTTP)

def test_BK_01_double_click_same_key_and_new_key(live, db):
    pid = build(live)["package_id"]
    k = key()
    rs = http_burst(live, [(pid, k), (pid, k), (pid, key())])  # double click (same key) + another tab
    refs = {r.json()["reference"] for r in rs}
    assert all(r.status_code in (200, 201) for r in rs) and len(refs) == 1
    assert REFERENCE_RE.fullmatch(refs.pop())
    assert count(db, "app_reservations", pid) == 1


def test_BK_01_twenty_concurrent_confirms(live, db):
    pid = build(live)["package_id"]
    shared = key()
    rs = http_burst(live, [(pid, shared if i % 4 == 0 else key()) for i in range(20)])
    assert all(r.status_code in (200, 201) for r in rs), [r.status_code for r in rs]
    assert len({r.json()["reference"] for r in rs}) == 1
    created = [r.json() for r in rs if r.status_code == 201]  # first commit + same-key replays only
    assert created and all(b == created[0] for b in created)
    assert all(r.json()["trip"] == created[0]["trip"] for r in rs)  # 200s return the same reservation
    assert count(db, "app_reservations") == 1 and package_row(db, pid)["status"] == "BOOKED"


def test_BK_04_two_packages_confirm_concurrently(live, db):
    p1, p2 = build(live)["package_id"], build(live)["package_id"]
    a, b = http_burst(live, [(p1, key()), (p2, key())])
    assert a.status_code == b.status_code == 201
    assert a.json()["reference"] != b.json()["reference"]
    assert {a.json()["package_id"], b.json()["package_id"]} == {p1, p2}
    with db.connect() as c:
        rows = c.execute(text("SELECT package_id, count(*) FROM app_reservations GROUP BY package_id")).all()
    assert sorted(r[1] for r in rows) == [1, 1]


def test_BK_09_distinct_keys_one_reservation(real, db):
    pid = build(real)["package_id"]
    rs = [confirm(real, pid) for _ in range(4)]
    assert [r.status_code for r in rs] == [201, 200, 200, 200]
    assert len({r.json()["reference"] for r in rs}) == 1 and count(db, "app_reservations", pid) == 1


# ---------------------------------------------------------------- BK-02 / BK-03 (retries, restart)

def test_BK_02_lost_response_retry_returns_same_booking(real):
    pid = build(real)["package_id"]
    k = key()
    first = confirm(real, pid, k)  # pretend the network dropped this response
    retry = confirm(real, pid, k)
    assert (retry.status_code, retry.json()) == (first.status_code, first.json())


def test_BK_03_retry_after_backend_restart(test_db_url, db):
    k = key()
    with make_client(test_db_url, None) as c1:
        pid = build(c1)["package_id"]
        first = confirm(c1, pid, k).json()
    with make_client(test_db_url, None) as c2:  # fresh app, engine, catalog: restart
        again = confirm(c2, pid, k)
        other = confirm(c2, pid)
        look = c2.get(f"/api/reservations/{first['reference']}")
    assert again.json() == first and other.json()["reference"] == first["reference"]
    assert look.status_code == 200 and look.json() == first


# ---------------------------------------------------------------- BK-05 / BK-06 (reference generation)

def test_BK_05_duplicate_generated_reference_retries(real, db, monkeypatch):
    p1, p2 = build(real)["package_id"], build(real)["package_id"]
    monkeypatch.setattr(tx, "random_reference", lambda: "4444444444")
    first = confirm(real, p1).json()
    seq = iter(["4444444444", "4444444444", "5555555555"])
    monkeypatch.setattr(tx, "random_reference", lambda: next(seq))
    second = confirm(real, p2)
    assert second.status_code == 201 and second.json()["reference"] == "5555555555"
    assert real.get("/api/reservations/4444444444").json() == first  # never overwritten


def test_BK_06_leading_zero_reference_preserved(real, db, monkeypatch):
    monkeypatch.setattr(tx, "random_reference", lambda: "0012345678")
    pid = build(real)["package_id"]
    res = confirm(real, pid)
    assert res.status_code == 201 and res.json()["reference"] == "0012345678"
    assert '"reference":"0012345678"' in res.text.replace(" ", "")  # JSON string, not a number
    with db.connect() as c:
        assert c.execute(text("SELECT reference FROM app_reservations")).scalar_one() == "0012345678"
    assert real.get("/api/reservations/0012345678").status_code == 200  # URL keeps the zeros
    assert real.get("/api/reservations/12345678").status_code == 404     # no int conversion
    exp = real.get(f"/api/packages/{pid}/export").json()
    assert {i["booking"]["reference"] for i in exp["items"]} == {"0012345678"}
    assert real.get(f"/api/packages/{pid}").json()["reference"] == "0012345678"


# ---------------------------------------------------------------- BK-07 / BK-08 (crash -> full rollback)

@pytest.mark.parametrize("point", ["confirm:after_reservation_insert", "confirm:after_status_update"])
def test_BK_07_08_crash_before_commit_rolls_back(real, db, monkeypatch, point):
    pid = build(real)["package_id"]
    before = package_row(db, pid)

    def crash(name):
        if name == point:
            raise RuntimeError("injected crash")
    monkeypatch.setattr(tx, "checkpoint", crash)
    r = confirm(real, pid)
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert package_row(db, pid) == before
    assert count(db, "app_reservations") == 0 and count(db, "app_idempotency_records") == 0
    monkeypatch.setattr(tx, "checkpoint", lambda _n: None)
    assert confirm(real, pid).status_code == 201  # original DRAFT still confirmable


# ---------------------------------------------------------------- BK-10 / BK-11 (need real edits)

@pytest.fixture(params=["fake", "real"])
def env_client(request, db, test_db_url):
    loader = (lambda _d: (ApiFakeDomain(), None)) if request.param == "fake" else None
    with make_client(test_db_url, loader) as c:
        yield request.param, c


def _require_real_change(kind, client, trip):
    if kind == "real":
        try:
            client.app.state.runtime.domain.change(trip, {"op": "remove_attraction", "item_id": "__probe__"})
        except NotImplementedError:
            pytest.skip(BLOCKED)


def test_BK_10_confirm_books_latest_edited_version(env_client, db):
    kind, c = env_client
    b = build(c)
    _require_real_change(kind, c, b["trip"])
    att = next(i for i in b["trip"]["items"] if i["type"] == "attraction")
    edited = c.patch(f"/api/packages/{b['package_id']}", json={"op": "remove_attraction", "item_id": att["item_id"]},
                     headers={"If-Match": '"1"', "Idempotency-Key": key()})
    assert edited.status_code == 200
    res = confirm(c, b["package_id"]).json()
    assert [i["catalog_id"] for i in res["trip"]["items"]] == [i["catalog_id"] for i in edited.json()["trip"]["items"]]


def test_BK_11_confirm_after_rejected_edit_books_last_valid_draft(env_client, db):
    kind, c = env_client
    b = build(c)
    _require_real_change(kind, c, b["trip"])
    r = c.patch(f"/api/packages/{b['package_id']}",
                json={"op": "add_attraction", "attraction_id": "FAKE-OR-UNKNOWN-ATT", "date": b["trip"]["end_date"]},
                headers={"If-Match": '"1"', "Idempotency-Key": key()})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_CHANGE"  # domain-rejected candidate
    res = confirm(c, b["package_id"]).json()
    assert [i["catalog_id"] for i in res["trip"]["items"]] == [i["catalog_id"] for i in b["trip"]["items"]]


def test_BK_11_platform_rejected_edits_never_reach_booking(real, db):
    """Supplementary [real]: edits rejected by the platform (forged amount, stale version) are not booked."""
    b = build(real)
    pid = b["package_id"]
    att = next(i for i in b["trip"]["items"] if i["type"] == "attraction")
    forged = real.patch(f"/api/packages/{pid}", json={"op": "remove_attraction", "item_id": att["item_id"], "price_inr": 0},
                        headers={"If-Match": '"1"', "Idempotency-Key": key()})
    stale = real.patch(f"/api/packages/{pid}", json={"op": "remove_attraction", "item_id": att["item_id"]},
                       headers={"If-Match": '"7"', "Idempotency-Key": key()})
    assert forged.status_code == 422 and stale.status_code == 409
    res = confirm(real, pid).json()
    assert res["trip"]["items"] == [dict(i, status="BOOKED", booking={"reference": res["reference"]})
                                    for i in b["trip"]["items"]]


# ---------------------------------------------------------------- BK-12 / BK-13 / BK-16

def test_BK_12_unknown_reference_not_found(real, db):
    r = real.get("/api/reservations/9876543210")
    assert r.status_code == 404 and r.json() == {"error": {"code": "NOT_FOUND", "message": "Reservation not found.",
                                                           "details": None}}


@pytest.mark.parametrize("ref", ["123456789", "12345678901", "12345abcde", "0012345678%20", "%20%20%20",
                                 "1234567890.0", "-123456789", "１２３４５６７８９０", "x" * 2000, "' OR '1'='1"])
def test_BK_13_malformed_reference_not_found(real, db, ref):
    r = real.get(f"/api/reservations/{ref}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert "Traceback" not in r.text and len(r.content) < 300


def test_BK_16_no_confirmable_draft_after_no_budget_fit(real, db):
    over = real.post("/api/packages/build", json={**MENON, "budget_inr": 1000})
    none = real.post("/api/packages/build", json=dict(zip(KEYS, ["DEL", "Japan", "2027-04-01", 8, 4, [3, 9], 900000])))
    assert over.json()["status"] == "OVER_BUDGET" and none.json()["status"] == "NO_PACKAGE"
    assert "package_id" not in over.json() and "package_id" not in none.json()
    assert count(db, "app_packages") == 0
    r = confirm(real, str(uuid.uuid4()))
    assert r.status_code == 404 and count(db, "app_reservations") == 0


# ---------------------------------------------------------------- SY-16 (API side; browser side in smoke script)

def test_SY_16_refresh_after_booking_shows_persisted_read_only_state(real, db):
    b = build(real)
    pid = b["package_id"]
    ref = confirm(real, pid).json()["reference"]
    g = real.get(f"/api/packages/{pid}").json()
    assert g["status"] == "BOOKED" and g["reference"] == ref
    att = next(i for i in b["trip"]["items"] if i["type"] == "attraction")
    assert real.patch(f"/api/packages/{pid}", json={"op": "remove_attraction", "item_id": att["item_id"]},
                      headers={"If-Match": f'"{g["version"]}"', "Idempotency-Key": key()}).json()["error"]["code"] == "ALREADY_BOOKED"
    assert real.get(f"/api/packages/{pid}/alternatives", params={"component": "hotel", "item_id": "i3"}).status_code == 409


# ---------------------------------------------------------------- immutable booked snapshot

def test_booked_snapshot_immutable_at_database_level(real, db):
    pid = build(real)["package_id"]
    res = confirm(real, pid).json()
    for sql in ("UPDATE app_reservations SET snapshot = '{}' WHERE reference = :r",
                "DELETE FROM app_reservations WHERE reference = :r",
                "UPDATE app_packages SET status = 'DRAFT' WHERE id = :p",
                "UPDATE app_packages SET trip = '{}' WHERE id = :p"):
        with pytest.raises(DBAPIError), db.begin() as c:
            c.execute(text(sql), {"r": res["reference"], "p": pid})
    assert real.get(f"/api/reservations/{res['reference']}").json() == res


def test_booked_export_real_catalog_consistent(real, db):
    pid = build(real)["package_id"]
    res = confirm(real, pid).json()
    exp = real.get(f"/api/packages/{pid}/export").json()
    assert exp == res["trip"] and booked_trip_shape_errors(exp, res["reference"]) == []
    assert real.app.state.runtime.domain.validate(exp, "confirm") == []
    as_draft = copy.deepcopy(exp)
    for i in as_draft["items"]:
        i["status"], i["booking"] = "PLANNED", None
    assert check_export(as_draft, RawCatalog(CATALOG_DIR)) == []
    assert json.loads(real.get(f"/api/packages/{pid}/export").content) == exp  # stable across requests
