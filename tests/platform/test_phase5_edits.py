"""Phase 5 acceptance: ED-09..ED-19, PR-18, SY-18 on REAL PostgreSQL.

Every test runs twice:
  [fake] TEST-ONLY deterministic DomainPort  -> proves the platform machinery
  [real] production loader -> Tanish's app.domain on the original catalog
Real cases that need a successful real change()/alternatives() skip as
"BLOCKED: ... Tanish Phase 5" until those are implemented, then run automatically.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.errors import ApiError
from app.services import transactions as tx
from tests.platform.conftest import make_client
from tests.platform.fixtures.db_helpers import count, package_row
from tests.platform.fixtures.fake_domain import ApiFakeDomain

KEYS = ["departure_city", "destination_country", "start_date", "nights", "adults", "children_ages", "budget_inr"]
MENON = dict(zip(KEYS, ["BLR", "Japan", "2027-04-10", 6, 2, [6], 350000]))
BLOCKED = "BLOCKED: real DomainPort.change()/alternatives() not implemented yet (Tanish Phase 5)"


@dataclass
class Env:
    kind: str
    client: TestClient

    @property
    def domain(self):
        return self.client.app.state.runtime.domain

    @property
    def factory(self):
        return self.client.app.state.runtime.sessionmaker


@pytest.fixture(params=["fake", "real"])
def env(request, db, test_db_url):
    loader = (lambda _d: (ApiFakeDomain(), None)) if request.param == "fake" else None
    with make_client(test_db_url, loader) as c:
        e = Env(request.param, c)
        if e.kind == "real" and type(e.domain).__module__ != "app.domain.adapter":
            pytest.skip("BLOCKED: real domain adapter not available")
        yield e


def key() -> str:
    return f"p5-{uuid.uuid4()}"


def build(env, **over) -> dict:
    r = env.client.post("/api/packages/build", json={**MENON, **over})
    assert r.status_code == 201, r.text
    return r.json()


def require_real_edits(env, trip: dict) -> None:
    if env.kind != "real":
        return
    try:
        env.domain.change(trip, {"op": "remove_attraction", "item_id": "__probe__"})
        env.domain.alternatives(trip, "outbound_flight", {})
    except NotImplementedError:
        pytest.skip(BLOCKED)


def patch(env, pid, change, version, k=None):
    return env.client.patch(f"/api/packages/{pid}", json=change,
                            headers={"If-Match": f'"{version}"', "Idempotency-Key": k or key()})


def alternatives(env, pid, component, **filters) -> list[dict]:
    r = env.client.get(f"/api/packages/{pid}/alternatives", params={"component": component, **filters})
    assert r.status_code == 200, r.text
    return r.json()["alternatives"]


def first_item(trip, **match):
    return next(i for i in trip["items"] if all(i.get(k) == v for k, v in match.items()))


def assert_legal(env, trip):
    assert trip["total_inr"] == sum(i["price_inr"] for i in trip["items"])
    assert env.domain.validate(trip, "modified") == []


# ---------------------------------------------------------------- ED-09 / ED-10 (valid edits)

def test_ED_09_valid_hotel_then_flight_change(env, db):
    b = build(env)
    require_real_edits(env, b["trip"])
    pid, hotel = b["package_id"], first_item(b["trip"], type="hotel")
    alts = alternatives(env, pid, "hotel", item_id=hotel["item_id"])
    ok = [a for a in alts if a["price_diff_inr"] is None or a["price_diff_inr"] <= MENON["budget_inr"] - b["trip"]["total_inr"]]
    assert ok, "no in-budget hotel alternative"
    r = patch(env, pid, ok[0]["change"], 1)
    assert r.status_code == 200 and r.json()["version"] == 2
    t = r.json()["trip"]
    assert first_item(t, type="hotel")["catalog_id"] == ok[0]["catalog_id"]
    assert_legal(env, t)
    flight_alts = alternatives(env, pid, "outbound_flight")
    if flight_alts:
        r2 = patch(env, pid, flight_alts[0]["change"], 2)
        assert r2.status_code in (200, 422)
        if r2.status_code == 200:
            assert r2.json()["version"] == 3
            assert_legal(env, r2.json()["trip"])
    row = package_row(db, pid)
    assert row["trip"] == env.client.get(f"/api/packages/{pid}").json()["trip"]
    assert row["total_inr"] == row["trip"]["total_inr"]


def test_ED_10_remove_attraction_reprices_and_versions(env, db):
    b = build(env)
    require_real_edits(env, b["trip"])
    pid, att = b["package_id"], first_item(b["trip"], type="attraction")
    r = patch(env, pid, {"op": "remove_attraction", "item_id": att["item_id"]}, 1)
    assert r.status_code == 200 and r.json()["version"] == 2
    t = r.json()["trip"]
    assert att["catalog_id"] not in {i["catalog_id"] for i in t["items"]}
    assert t["total_inr"] == b["trip"]["total_inr"] - att["price_inr"]
    assert_legal(env, t)
    assert package_row(db, pid)["version"] == 2


# ---------------------------------------------------------------- ED-11 (over budget)

def test_ED_11_edit_exceeding_budget_rejected(env, db):
    big = build(env, budget_inr=100_000_000)
    require_real_edits(env, big["trip"])
    hotel = first_item(big["trip"], type="hotel")
    pricier = [a for a in alternatives(env, big["package_id"], "hotel", item_id=hotel["item_id"])
               if a["price_inr"] > hotel["price_inr"]]
    if not pricier:
        pytest.skip("no pricier hotel alternative in catalog for this trip")
    tight = build(env, budget_inr=big["trip"]["total_inr"])  # same deterministic trip, zero slack
    before = package_row(db, tight["package_id"])
    r = patch(env, tight["package_id"], max(pricier, key=lambda a: a["price_inr"])["change"], 1)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_CHANGE"
    assert package_row(db, tight["package_id"]) == before


def _store_over_budget(db, pid, budget):
    with db.begin() as c:
        c.execute(text("UPDATE app_packages SET budget_inr=:b, trip = jsonb_set(trip, '{budget_inr}', CAST(:j AS JSONB)) "
                       "WHERE id=:id"), {"b": budget, "j": json.dumps(budget), "id": pid})


def test_ED_11_over_budget_draft_is_never_confirmed(env, db):
    b = build(env)
    _store_over_budget(db, b["package_id"], b["trip"]["total_inr"] - 1)
    r = env.client.post(f"/api/packages/{b['package_id']}/confirm", headers={"Idempotency-Key": key()})
    assert r.status_code == 422 and r.json()["error"]["code"] == "CONFIRM_REJECTED"
    assert any(v["code"] == "OVER_BUDGET" for v in r.json()["error"]["details"])
    assert package_row(db, b["package_id"])["status"] == "DRAFT" and count(db, "app_reservations") == 0


# ---------------------------------------------------------------- ED-12 / ED-13 (real concurrency)

def _concurrently(*fns):
    barrier, out = threading.Barrier(len(fns)), [None] * len(fns)

    def run(i, fn):
        barrier.wait()
        try:
            out[i] = fn()
        except ApiError as e:
            out[i] = (e.status, {"error": {"code": e.code}})

    ts = [threading.Thread(target=run, args=(i, f)) for i, f in enumerate(fns)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    return out


def _two_removals(trip):
    atts = [i for i in trip["items"] if i["type"] == "attraction"]
    return ({"op": "remove_attraction", "item_id": atts[0]["item_id"]},
            {"op": "remove_attraction", "item_id": atts[1]["item_id"]})


def test_ED_12_two_tabs_same_version_no_lost_update(env, db):
    b = build(env)
    require_real_edits(env, b["trip"])
    c1, c2 = _two_removals(b["trip"])
    res = _concurrently(lambda: tx.edit_package(env.factory, env.domain, b["package_id"], 1, key(), c1),
                        lambda: tx.edit_package(env.factory, env.domain, b["package_id"], 1, key(), c2))
    assert sorted(r[0] for r in res) == [200, 409]
    assert next(r for r in res if r[0] == 409)[1]["error"]["code"] == "VERSION_CONFLICT"
    winner = next(r for r in res if r[0] == 200)[1]
    row = package_row(db, b["package_id"])
    assert row["version"] == 2 and row["trip"] == winner["trip"]


def test_ED_13_patch_and_confirm_serializable(env, db):
    b = build(env)
    require_real_edits(env, b["trip"])
    c1, _ = _two_removals(b["trip"])
    p, c = _concurrently(lambda: tx.edit_package(env.factory, env.domain, b["package_id"], 1, key(), c1),
                         lambda: tx.confirm_package(env.factory, env.domain, b["package_id"], key()))
    assert c[0] == 201
    final = package_row(db, b["package_id"])
    assert [i["catalog_id"] for i in c[1]["trip"]["items"]] == [i["catalog_id"] for i in final["trip"]["items"]]
    assert (p[0], final["version"]) in ((200, 2), (409, 1))
    if p[0] == 409:
        assert p[1]["error"]["code"] == "ALREADY_BOOKED"
    assert patch(env, b["package_id"], c1, final["version"]).json()["error"]["code"] == "ALREADY_BOOKED"


# ---------------------------------------------------------------- ED-14 / ED-15 / ED-16 / ED-17 (idempotency, atomicity)

def test_ED_14_same_key_same_payload_replays_after_version_advanced(env, db):
    b = build(env)
    require_real_edits(env, b["trip"])
    c1, c2 = _two_removals(b["trip"])
    k = key()
    first = patch(env, b["package_id"], c1, 1, k)
    assert first.status_code == 200 and patch(env, b["package_id"], c2, 2).status_code == 200
    replay = patch(env, b["package_id"], c1, 1, k)
    assert (replay.status_code, replay.json()) == (200, first.json())
    assert package_row(db, b["package_id"])["version"] == 3 and count(db, "app_idempotency_records") == 2


def test_ED_15_same_key_different_payload_conflicts(env, db):
    b = build(env)
    require_real_edits(env, b["trip"])
    c1, c2 = _two_removals(b["trip"])
    k = key()
    assert patch(env, b["package_id"], c1, 1, k).status_code == 200
    before = package_row(db, b["package_id"])
    r = patch(env, b["package_id"], c2, 2, k)
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert package_row(db, b["package_id"]) == before


def test_ED_16_crash_between_update_and_idempotency_insert(env, db, monkeypatch):
    b = build(env)
    require_real_edits(env, b["trip"])
    before = package_row(db, b["package_id"])

    def crash(name):
        if name == "patch:after_update":
            raise RuntimeError("injected")
    monkeypatch.setattr(tx, "checkpoint", crash)
    k, c1 = key(), _two_removals(b["trip"])[0]
    with pytest.raises(RuntimeError):
        tx.edit_package(env.factory, env.domain, b["package_id"], 1, k, c1)
    assert package_row(db, b["package_id"]) == before and count(db, "app_idempotency_records") == 0
    monkeypatch.setattr(tx, "checkpoint", lambda _n: None)
    assert tx.edit_package(env.factory, env.domain, b["package_id"], 1, k, c1)[0] == 200  # retry succeeds once


def test_ED_17_two_keys_sequential_edits_both_persist(env, db):
    b = build(env)
    require_real_edits(env, b["trip"])
    c1, c2 = _two_removals(b["trip"])
    assert patch(env, b["package_id"], c1, 1).json()["version"] == 2
    r = patch(env, b["package_id"], c2, 2)
    assert r.status_code == 200 and r.json()["version"] == 3
    assert_legal(env, r.json()["trip"])
    assert count(db, "app_idempotency_records") == 2


# ---------------------------------------------------------------- ED-18 / ED-19 (no domain change needed)

def test_ED_18_patch_after_confirmed_rejected_snapshot_unchanged(env, db):
    b = build(env)
    booked = env.client.post(f"/api/packages/{b['package_id']}/confirm", headers={"Idempotency-Key": key()})
    assert booked.status_code == 201
    att = first_item(b["trip"], type="attraction")
    r = patch(env, b["package_id"], {"op": "remove_attraction", "item_id": att["item_id"]}, 1)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_BOOKED"
    assert env.client.get(f"/api/reservations/{booked.json()['reference']}").json() == booked.json()


def test_ED_19_stale_version_rechecked_inside_row_lock(env, db):
    b = build(env)
    pid = b["package_id"]
    att = first_item(b["trip"], type="attraction")
    holding = threading.Event()

    def other_tab_commits_v2_while_locked():
        with db.connect() as conn:
            trans = conn.begin()
            conn.execute(text("SELECT id FROM app_packages WHERE id=:id FOR UPDATE"), {"id": pid})
            holding.set()
            threading.Event().wait(0.8)  # stale PATCH arrives meanwhile and must wait for the lock
            conn.execute(text("UPDATE app_packages SET version = 2, updated_at = now() WHERE id=:id"), {"id": pid})
            trans.commit()

    t = threading.Thread(target=other_tab_commits_v2_while_locked)
    t.start()
    holding.wait(5)
    r = patch(env, pid, {"op": "remove_attraction", "item_id": att["item_id"]}, 1)  # read v1 before; arrives late
    t.join(10)
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_CONFLICT"
    assert r.json()["error"]["details"] == {"current_version": 2}
    row = package_row(db, pid)
    assert row["version"] == 2 and row["trip"] == b["trip"]


# ---------------------------------------------------------------- PR-18 / SY-18 (never trust the client)

@pytest.mark.parametrize("forged", [
    {"price_inr": 1}, {"total_inr": 1}, {"trip": {"total_inr": 1}}, {"discount": 0.5}, {"version": 99},
])
def test_PR_18_client_amounts_in_patch_rejected(env, db, forged):
    b = build(env)
    att = first_item(b["trip"], type="attraction")
    r = patch(env, b["package_id"], {"op": "remove_attraction", "item_id": att["item_id"], **forged}, 1)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert package_row(db, b["package_id"])["trip"] == b["trip"]


def test_PR_18_totals_after_edit_are_server_derived(env, db):
    b = build(env)
    require_real_edits(env, b["trip"])
    att = first_item(b["trip"], type="attraction")
    t = patch(env, b["package_id"], {"op": "remove_attraction", "item_id": att["item_id"]}, 1).json()["trip"]
    assert t["total_inr"] == sum(i["price_inr"] for i in t["items"]) == package_row(db, b["package_id"])["total_inr"]


def test_SY_18_forged_hidden_ids_rejected_on_patch(env, db):
    b = build(env)
    require_real_edits(env, b["trip"])
    hotel = first_item(b["trip"], type="hotel")
    before = package_row(db, b["package_id"])
    for change in ({"op": "replace_hotel", "item_id": hotel["item_id"], "hotel_id": "HTL-FAKE-999", "room_id": "RM-FAKE"},
                   {"op": "remove_attraction", "item_id": "i999"},
                   {"op": "add_attraction", "attraction_id": "ATT-FAKE-1", "date": b["trip"]["start_date"]}):
        r = patch(env, b["package_id"], change, 1)
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_CHANGE", change
    assert package_row(db, b["package_id"]) == before


def test_SY_18_tampered_stored_price_caught_at_confirm(env, db):
    if env.kind == "fake":
        pytest.skip("fake validate() does not re-price from the catalog; covered by [real]")
    b = build(env)
    forged = json.loads(json.dumps(b["trip"]))  # coherent forgery: cheaper item AND matching total
    cut = forged["items"][0]["price_inr"] - 1
    forged["items"][0]["price_inr"] = 1
    forged["total_inr"] -= cut
    with db.begin() as c:  # e.g. a devtools-forged discount that somehow reached storage
        c.execute(text("UPDATE app_packages SET trip = CAST(:t AS JSONB), total_inr = :tot WHERE id=:id"),
                  {"t": json.dumps(forged), "tot": forged["total_inr"], "id": b["package_id"]})
    r = env.client.post(f"/api/packages/{b['package_id']}/confirm", headers={"Idempotency-Key": key()})
    assert r.status_code == 422 and r.json()["error"]["code"] == "CONFIRM_REJECTED"
    assert {v["code"] for v in r.json()["error"]["details"]} & {"PRICE_MISMATCH", "TOTAL_MISMATCH"}
    assert count(db, "app_reservations") == 0
