"""Trip Rescue (Budget Rescue) RS-B01..B16 — REAL domain (production loader, original catalog) + REAL PostgreSQL.
Domain: Tanish's b360a5f `Domain.rescue(trip, target_budget_inr, locked_attraction_ids, max_changes)` — cheaper
validated variants of a saved draft; dates, home airport, country, city order/nights, travellers and locked
attractions are never changed. Platform: POST /api/packages/{id}/rescue (preview) and PATCH {"op":"apply_rescue"}.
Independent checks: raw-catalog oracle (prices, room-night availability, seats, timeline) + Tanish's validator."""

from __future__ import annotations

import re
import threading
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.errors import ApiError
from app.services import transactions as tx
from app.services.export import draft_export
from tests.platform.conftest import make_client
from tests.platform.fixtures.catalog_oracle import RawCatalog, check_export
from tests.platform.fixtures.contract_fixtures import CATALOG_DIR, REPO_ROOT
from tests.platform.fixtures.db_helpers import count, package_row
from tests.platform.test_phase4_integration import NO_PACKAGE_REQ

MENON = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 6,
         "adults": 2, "children_ages": [6], "budget_inr": 350000}
FIXED = ("trip_id", "traveler", "country", "start_date", "end_date", "budget_inr")


def key() -> str:
    return f"rs-{uuid.uuid4()}"


@pytest.fixture(scope="module")
def cat():
    return RawCatalog(CATALOG_DIR)


@pytest.fixture
def real(db, test_db_url):
    with make_client(test_db_url, None) as c:
        if not hasattr(c.app.state.runtime.domain, "rescue"):
            pytest.skip("BLOCKED: real domain without Budget Rescue")
        yield c


def dom(c):
    return c.app.state.runtime.domain


def patch(c, pid, change, version, k=None):
    return c.patch(f"/api/packages/{pid}", json=change, headers={"If-Match": f'"{version}"', "Idempotency-Key": k or key()})


def upgraded(c, flight=False, hotel=True) -> tuple[str, dict, int]:
    """A saved draft the traveller upgraded (pricier hotel and/or pricier outbound flight)."""
    b = c.post("/api/packages/build", json=MENON).json()
    pid, trip, v = b["package_id"], b["trip"], 1
    comps = [("hotel", {"item_id": next(i for i in trip["items"] if i["role"] == "hotel_stay")["item_id"]})] if hotel else []
    if flight:
        comps.append(("outbound_flight", {}))
    for comp, f in comps:
        alts = c.get(f"/api/packages/{pid}/alternatives", params={"component": comp, **f}).json()["alternatives"]
        r = patch(c, pid, max(alts, key=lambda a: a["price_diff_inr"])["change"], v)
        assert r.status_code == 200, r.text
        trip, v = r.json()["trip"], v + 1
    return pid, trip, v


def preview(c, pid, target, **kw):
    r = c.post(f"/api/packages/{pid}/rescue", json={"target_budget_inr": target, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def oracle_ok(c, trip, cat):
    exported = draft_export(trip)
    assert check_export(exported, cat) == [], check_export(exported, cat)[:3]
    assert dom(c).validate(trip, "modified") == []


# ------------------------------------------------------------------ failures & honest reasons

def test_RS_B01_no_flight_reason_and_no_fake_rescue(real, db):
    before = count(db, "app_packages")
    body = real.post("/api/packages/build", json=NO_PACKAGE_REQ).json()
    assert body["status"] == "NO_PACKAGE" and body["reason_code"] and body["reason"] and body["trip"] is None
    assert count(db, "app_packages") == before  # nothing to rescue, nothing fabricated
    r = real.post(f"/api/packages/{uuid.uuid4()}/rescue", json={"target_budget_inr": 1})
    assert r.status_code == 404
    js = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "customerText(body.reason)" in js  # the specific reason is shown, cleaned for customers


def test_RS_B02_over_budget_exact_savings_no_fake_discounts(real, db, cat):
    pid, trip, _ = upgraded(real)
    target = trip["total_inr"] - 30000
    body = preview(real, pid, target)
    assert body["status"] == "RESCUED" and 1 <= len(body["proposals"]) <= 3
    raw = dom(real).rescue(trip, target, [], 3)
    for p, rp in zip(body["proposals"], raw["proposals"]):
        assert p["proposed_total_inr"] <= target and p["savings_inr"] == trip["total_inr"] - p["proposed_total_inr"]
        oracle_ok(real, rp["trip"], cat)  # every price re-derived from the raw catalog: no invented discount
    over = real.post("/api/packages/build", json={**MENON, "budget_inr": 10000}).json()
    assert over["status"] == "OVER_BUDGET" and over["shortfall_inr"] == over["cheapest_total_inr"] - 10000


def test_RS_B03_rooms_available_every_night(real, db, cat):
    pid, trip, _ = upgraded(real)
    raw = dom(real).rescue(trip, trip["total_inr"] - 1, [], 3)
    hotel_changes = [p for p in raw["proposals"] if any(c["component"] == "hotel" for c in p["changes"])]
    assert hotel_changes, "no hotel rescue to check"
    for p in hotel_changes:
        for h in (i for i in p["trip"]["items"] if i["role"] == "hotel_stay"):
            oracle_ok(real, p["trip"], cat)  # HOTEL_AVAILABILITY checks rooms_left >= k for every night
            assert h["room_id"] in cat.rooms


def test_RS_B04_flight_change_repairs_transfers_legally(real, db, cat):
    pid, trip, _ = upgraded(real, flight=True, hotel=False)
    found = []
    for gap in (1, 20000, 60000, 100000):
        raw = dom(real).rescue(trip, trip["total_inr"] - gap, [], 3)
        found += [p for p in raw["proposals"] if any("flight" in c["component"] for c in p["changes"])]
    assert found, "no flight rescue on the official catalog"
    for p in found:  # dependent airport transfers re-selected only when legal; timeline re-checked independently
        oracle_ok(real, p["trip"], cat)
    impossible = dom(real).rescue(trip, 0, [], 3)
    assert impossible["status"] == "NO_RESCUE_FOUND" and impossible["proposals"] == [] and impossible["reason"]


def test_RS_B05_dates_cities_preserved_and_revalidated(real, db, cat):
    pid, trip, v = upgraded(real, flight=True)
    body = preview(real, pid, trip["total_inr"] - 20000)
    raw = dom(real).rescue(trip, trip["total_inr"] - 20000, [], 3)
    cities = lambda t: [i["city"] for i in sorted((i for i in t["items"] if i["role"] == "hotel_stay"), key=lambda i: i["start"])]  # noqa: E731
    for p in raw["proposals"]:
        assert all(p["trip"][k] == trip[k] for k in FIXED) and cities(p["trip"]) == cities(trip)
        oracle_ok(real, p["trip"], cat)
    r = patch(real, pid, body["proposals"][0]["change"], v)
    assert r.status_code == 200 and dom(real).validate(r.json()["trip"], "modified") == []


def test_RS_B06_exact_server_total_and_signed_difference(real, db):
    pid, trip, v = upgraded(real)
    body = preview(real, pid, trip["total_inr"] - 30000)
    for p in body["proposals"]:
        assert p["price_diff_inr"] == p["proposed_total_inr"] - trip["total_inr"] < 0
        assert p["price_diff_inr"] == sum(d["price_diff_inr"] for d in p["details"])
    chosen = body["proposals"][-1]
    r = patch(real, pid, chosen["change"], v)
    saved = r.json()["trip"]
    assert r.status_code == 200 and saved["total_inr"] == chosen["proposed_total_inr"] == sum(i["price_inr"] for i in saved["items"])
    assert package_row(db, pid)["total_inr"] == chosen["proposed_total_inr"]


def test_RS_B07_no_feasible_rescue_empty_state(real, db):
    pid, trip, v = upgraded(real)
    before = package_row(db, pid)
    body = preview(real, pid, 0)
    assert body["status"] == "NO_RESCUE_FOUND" and body["proposals"] == [] and body["reason"]
    assert preview(real, pid, trip["total_inr"])["status"] == "NO_CHANGES_NEEDED"
    assert package_row(db, pid) == before
    forced = {"op": "apply_rescue", "target_budget_inr": 0, "candidate_id": "hotel:i3:HTL-TYO-003:RM-TYO-003-C"}
    r = patch(real, pid, forced, v)  # nothing selectable: a guessed candidate is refused
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "RESCUE_UNAVAILABLE"
    assert package_row(db, pid) == before


# ------------------------------------------------------------------ transactions

def test_RS_B08_stale_suggestion_409(real, db):
    pid, trip, v = upgraded(real)
    p = preview(real, pid, trip["total_inr"] - 30000)["proposals"][0]
    att = next(i for i in trip["items"] if i["type"] == "attraction")
    assert patch(real, pid, {"op": "remove_attraction", "item_id": att["item_id"]}, v).status_code == 200  # other tab
    before = package_row(db, pid)
    r = patch(real, pid, p["change"], v)
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_CONFLICT"
    assert package_row(db, pid) == before


def test_RS_B09_retry_is_idempotent(real, db):
    pid, trip, v = upgraded(real)
    p = preview(real, pid, trip["total_inr"] - 30000)["proposals"][0]
    k = key()
    r1, r2 = patch(real, pid, p["change"], v, k), patch(real, pid, p["change"], v, k)
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    assert package_row(db, pid)["version"] == v + 1 and count(db, "app_reservations", pid) == 0


def test_RS_B10_two_tabs_different_rescues(real, db):
    pid, trip, v = upgraded(real)
    props = preview(real, pid, trip["total_inr"] - 1)["proposals"]
    assert len(props) >= 2
    rt, barrier, out = real.app.state.runtime, threading.Barrier(2), [None, None]

    def go(i):
        barrier.wait()
        try:
            out[i] = tx.edit_package(rt.sessionmaker, rt.domain, pid, v, key(), props[i]["change"])
        except ApiError as e:
            out[i] = (e.status, {"error": {"code": e.code}})

    ts = [threading.Thread(target=go, args=(i,)) for i in (0, 1)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert sorted(o[0] for o in out) == [200, 409]
    winner = next(o[1] for o in out if o[0] == 200)
    assert package_row(db, pid)["trip"] == winner["trip"] and package_row(db, pid)["version"] == v + 1


@pytest.mark.parametrize("attempt", range(3))
def test_RS_B11_rescue_races_confirmation(real, db, attempt):
    pid, trip, v = upgraded(real)
    p = preview(real, pid, trip["total_inr"] - 30000)["proposals"][0]
    rt, barrier, out = real.app.state.runtime, threading.Barrier(2), {}

    def edit():
        barrier.wait()
        try:
            out["edit"] = tx.edit_package(rt.sessionmaker, rt.domain, pid, v, key(), p["change"])
        except ApiError as e:
            out["edit"] = (e.status, {"error": {"code": e.code}})

    def confirm():
        barrier.wait()
        out["confirm"] = tx.confirm_package(rt.sessionmaker, rt.domain, pid, key())

    ts = [threading.Thread(target=edit), threading.Thread(target=confirm)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert out["confirm"][0] == 201 and count(db, "app_reservations", pid) == 1
    snap_total = out["confirm"][1]["trip"]["total_inr"]
    if out["edit"][0] == 200:
        assert snap_total == p["proposed_total_inr"]
    else:
        assert out["edit"] == (409, {"error": {"code": "ALREADY_BOOKED"}}) and snap_total == trip["total_inr"]
    assert package_row(db, pid)["status"] == "BOOKED"
    assert patch(real, pid, p["change"], v + 5).json()["error"]["code"] == "ALREADY_BOOKED"  # no post-booking edit
    with pytest.raises(DBAPIError), db.begin() as c:
        c.execute(text("UPDATE app_reservations SET snapshot = '{}'::jsonb WHERE package_id = :p"), {"p": pid})


@pytest.mark.parametrize("bad", [
    {"trip": {"items": []}}, {"proposed_total_inr": 1}, {"price_diff_inr": -999999}, {"target_budget_inr": -1},
    {"target_budget_inr": "100000"}, {"max_changes": 9}, {"candidate_id": ""}, {"locked_attraction_ids": "ATT-1"},
])
def test_RS_B12_manipulated_payload_422(real, db, bad):
    pid, trip, v = upgraded(real)
    p = preview(real, pid, trip["total_inr"] - 30000)["proposals"][0]
    before = package_row(db, pid)
    r = patch(real, pid, {**p["change"], **bad}, v)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert package_row(db, pid) == before
    for body in ({"target_budget_inr": 1, "locked_attraction_ids": ["ATT-NOPE-999"]}, {"target_budget_inr": 1, "extra": 1}):
        assert real.post(f"/api/packages/{pid}/rescue", json=body).status_code == 422
    tampered = {**p["change"], "candidate_id": p["change"]["candidate_id"] + "x"}
    r = patch(real, pid, tampered, v)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_CHANGE"
    assert package_row(db, pid) == before


def test_RS_B13_reload_export_confirm_consistent(real, db, cat):
    pid, trip, v = upgraded(real)
    p = preview(real, pid, trip["total_inr"] - 30000)["proposals"][0]
    saved = patch(real, pid, p["change"], v).json()["trip"]
    assert real.get(f"/api/packages/{pid}").json()["trip"] == saved
    exported = real.get(f"/api/packages/{pid}/export").json()
    assert check_export(exported, cat) == [] and exported["total_inr"] == p["proposed_total_inr"]
    conf = real.post(f"/api/packages/{pid}/confirm", headers={"Idempotency-Key": key()})
    assert conf.status_code == 201 and conf.json()["trip"]["total_inr"] == p["proposed_total_inr"]
    assert [i["catalog_id"] for i in conf.json()["trip"]["items"]] == [i["catalog_id"] for i in saved["items"]]
    assert re.search(conf.json()["reference"], real.get(f"/api/packages/{pid}/export").text)


def test_RS_B14_outdated_suggestions_cleared(real, db):
    pid, trip, v = upgraded(real)
    p = preview(real, pid, trip["total_inr"] - 30000)["proposals"][0]
    assert patch(real, pid, p["change"], v).status_code == 200
    fresh = preview(real, pid, trip["total_inr"] - 30000)
    assert fresh["version"] == v + 1 and all(q["candidate_id"] != p["candidate_id"] or q["proposed_total_inr"] != p["proposed_total_inr"]
                                             for q in fresh["proposals"])
    js = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'results.replaceChildren(el("p", "Looking for savings…", "hint"));  // older suggestions are cleared first' in js
    assert "if (body.version !== ctx.view.version) return explainError(ctx, 409" in js


def test_RS_B15_capability_unavailable_or_failing(client, db, real, monkeypatch):
    b = client.post("/api/packages/build", json=MENON).json()
    r = client.post(f"/api/packages/{b['package_id']}/rescue", json={"target_budget_inr": 1})
    assert r.status_code == 503 and r.json()["error"]["message"] == "Finding savings is not available right now."
    pid, trip, v = upgraded(real)
    before = package_row(db, pid)
    monkeypatch.setattr(dom(real), "rescue", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")), raising=False)
    r = real.post(f"/api/packages/{pid}/rescue", json={"target_budget_inr": 1})
    assert r.status_code == 500 and "boom" not in r.text and "Traceback" not in r.text
    forged = lambda t, *a, **k: {"status": "RESCUED", "original_total_inr": t["total_inr"], "proposals": [  # noqa: E731
        {"trip": {**t, "total_inr": 1}, "proposed_total_inr": 1, "savings_inr": t["total_inr"] - 1, "candidate_id": "x"}]}
    monkeypatch.setattr(dom(real), "rescue", forged, raising=False)
    assert real.post(f"/api/packages/{pid}/rescue", json={"target_budget_inr": 1}).status_code == 500
    assert patch(real, pid, {"op": "apply_rescue", "target_budget_inr": 1, "candidate_id": "x"}, v).status_code == 500
    assert package_row(db, pid) == before  # a fabricated price is never shown or saved


def test_RS_B16_ordinary_flows_unaffected(real, db):
    b = real.post("/api/packages/build", json=MENON)
    assert b.status_code == 201 and set(b.json()) == {"package_id", "version", "status", "trip"}
    pid, trip = b.json()["package_id"], b.json()["trip"]
    assert preview(real, pid, trip["total_inr"] - 1)["status"] == "NO_RESCUE_FOUND"  # cheapest build: nothing invented
    att = next(i for i in trip["items"] if i["type"] == "attraction")
    assert patch(real, pid, {"op": "remove_attraction", "item_id": att["item_id"]}, 1).status_code == 200
    assert real.post(f"/api/packages/{pid}/confirm", headers={"Idempotency-Key": key()}).status_code == 201
