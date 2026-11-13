"""Phase 9 (optional extension) G3 — change cities and rebuild the package. REAL domain (production loader,
original catalog) and REAL PostgreSQL, plus one fake-domain case. Interface: Tanish's 0d7c931 proposal —
PATCH body {"op":"change_city_plan","cities":[...],"nights":[...]} and GET /api/packages/{id}/city-plans."""

from __future__ import annotations

import json
import re
import threading
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.errors import ApiError
from app.services import transactions as tx
from tests.platform.conftest import make_client
from tests.platform.fixtures.contract_fixtures import CATALOG_DIR, REPO_ROOT
from tests.platform.fixtures.db_helpers import count, package_row

MENON = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 6,
         "adults": 2, "children_ages": [6], "budget_inr": 350000}


def key() -> str:
    return f"p9-{uuid.uuid4()}"


@pytest.fixture
def real(db, test_db_url):
    with make_client(test_db_url, None) as c:
        if not hasattr(c.app.state.runtime.domain, "rebuild_city_plan"):
            pytest.skip("BLOCKED: real domain without the G3 extension")
        yield c


def build(c, **over) -> dict:
    r = c.post("/api/packages/build", json={**MENON, **over})
    assert r.status_code == 201, r.text
    return r.json()


def plans(c, pid) -> dict:
    r = c.get(f"/api/packages/{pid}/city-plans")
    assert r.status_code == 200, r.text
    return r.json()


def patch(c, pid, change, version, k=None):
    return c.patch(f"/api/packages/{pid}", json=change, headers={"If-Match": f'"{version}"', "Idempotency-Key": k or key()})


def other_plan(body, n=0) -> dict:
    return [p for p in body["plans"] if not p["current"] and p["within_budget"]][n]


def plain(trip) -> dict:  # booking only adds status=BOOKED and the booking reference
    t = {k: v for k, v in trip.items() if k != "status"}
    t["items"] = [{k: v for k, v in i.items() if k not in ("status", "booking")} for i in trip["items"]]
    return t


def hotel_cities(trip) -> list[str]:
    return [i["city"] for i in sorted((i for i in trip["items"] if i["role"] == "hotel_stay"), key=lambda i: i["start"])]


def test_G3_B01_catalog_city_choices_and_approved_shape(real, db):
    b = build(real)
    body = plans(real, b["package_id"])
    raw = json.loads((CATALOG_DIR / "destinations.json").read_text(encoding="utf-8"))
    japan = next(c for c in raw["countries"] if c["name"] == "Japan")
    cities = {c["name"] for c in japan["cities"]}
    links = {(l["from_city"], l["to_city"]) for l in japan["links"]}
    assert body["version"] == 1 and sum(p["current"] for p in body["plans"]) == 1
    assert body["current"] == {"cities": hotel_cities(b["trip"]), "nights": [6]}
    for p in body["plans"]:  # independent oracle over the raw catalog
        assert set(p["cities"]) <= cities and len(set(p["cities"])) == len(p["cities"])
        assert all(pair in links for pair in zip(p["cities"], p["cities"][1:]))
        assert sum(p["nights"]) == 6 and p["price_diff_inr"] == p["total_inr"] - b["trip"]["total_inr"]
        assert p["change"] == {"op": "change_city_plan", "cities": p["cities"], "nights": p["nights"]}
    js = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "/city-plans" in js and "applyChange(ctx, p.change)" in js  # UI submits the server-built shape unchanged
    chosen = other_plan(body)
    r = patch(real, b["package_id"], chosen["change"], 1)
    assert r.status_code == 200 and r.json()["version"] == 2
    assert hotel_cities(r.json()["trip"]) == chosen["cities"] and r.json()["trip"]["total_inr"] == chosen["total_inr"]
    assert real.app.state.runtime.domain.validate(r.json()["trip"], "modified") == []


def test_G3_B02_idempotent_replay(real, db):
    b = build(real)
    pid, k = b["package_id"], key()
    change = other_plan(plans(real, pid))["change"]
    r1, r2 = patch(real, pid, change, 1, k), patch(real, pid, change, 1, k)
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    assert package_row(db, pid)["version"] == 2
    assert count(db, "app_idempotency_records", pid) == 1
    r3 = patch(real, pid, other_plan(plans(real, pid), 1)["change"], 1, k)  # same key, other body
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT" and package_row(db, pid)["version"] == 2


def test_G3_B03_two_tabs_one_commits(real, db):
    b = build(real)
    pid, rt = b["package_id"], real.app.state.runtime
    body = plans(real, pid)
    changes = [other_plan(body, 0)["change"], other_plan(body, 1)["change"]]
    barrier, out = threading.Barrier(2), [None, None]

    def go(i):
        barrier.wait()
        try:
            out[i] = tx.edit_package(rt.sessionmaker, rt.domain, pid, 1, key(), changes[i])
        except ApiError as e:
            out[i] = (e.status, {"error": {"code": e.code}})

    ts = [threading.Thread(target=go, args=(i,)) for i in (0, 1)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert sorted(o[0] for o in out) == [200, 409]
    assert next(o[1] for o in out if o[0] == 409)["error"]["code"] == "VERSION_CONFLICT"
    winner = next(o[1] for o in out if o[0] == 200)
    row = package_row(db, pid)
    assert row["version"] == 2 and row["trip"] == winner["trip"]


@pytest.mark.parametrize("attempt", range(3))
def test_G3_B04_city_change_races_confirmation(real, db, attempt):
    b = build(real)
    pid, rt = b["package_id"], real.app.state.runtime
    change = other_plan(plans(real, pid))["change"]
    barrier, out = threading.Barrier(2), {}

    def edit():
        barrier.wait()
        try:
            out["edit"] = tx.edit_package(rt.sessionmaker, rt.domain, pid, 1, key(), change)
        except ApiError as e:
            out["edit"] = (e.status, {"error": {"code": e.code}})

    def confirm():
        barrier.wait()
        out["confirm"] = tx.confirm_package(rt.sessionmaker, rt.domain, pid, key())

    ts = [threading.Thread(target=edit), threading.Thread(target=confirm)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert out["confirm"][0] == 201
    snap = out["confirm"][1]["trip"]
    if out["edit"][0] == 200:  # change committed first, booking carries the new itinerary
        assert plain(snap) == plain(out["edit"][1]["trip"]) and hotel_cities(snap) == change["cities"]
    else:  # booking won; change refused, original itinerary booked
        assert out["edit"] == (409, {"error": {"code": "ALREADY_BOOKED"}}) and plain(snap) == plain(b["trip"])
    row = package_row(db, pid)
    assert row["status"] == "BOOKED" and plain(row["trip"]) == plain(snap) and count(db, "app_reservations", pid) == 1
    with pytest.raises(DBAPIError), db.begin() as c:  # snapshot immutable at the DB level
        c.execute(text("UPDATE app_reservations SET snapshot = '{}'::jsonb WHERE package_id = :p"), {"p": pid})


@pytest.mark.parametrize("bad, code", [
    ({"cities": ["Paris"], "nights": [6]}, "INVALID_CHANGE"),
    ({"cities": ["Tokyo", "Osaka"], "nights": [3, 4]}, "INVALID_CHANGE"),
    ({"cities": ["Tokyo", "Tokyo"], "nights": [3, 3]}, "INVALID_CHANGE"),
    ({"cities": ["Tokyo"], "nights": [0]}, "VALIDATION_ERROR"),
    ({"cities": ["Tokyo"], "nights": ["6"]}, "VALIDATION_ERROR"),
    ({"cities": ["Tokyo"], "nights": [6], "total_inr": 1}, "VALIDATION_ERROR"),
])
def test_G3_B05_invalid_plan_preserves_row(real, db, bad, code):
    b = build(real)
    before = package_row(db, b["package_id"])
    r = patch(real, b["package_id"], {"op": "change_city_plan", **bad}, 1)
    assert r.status_code == 422 and r.json()["error"]["code"] == code
    if code == "INVALID_CHANGE":
        assert r.json()["error"]["details"] and all(v["message"] for v in r.json()["error"]["details"])
    assert package_row(db, b["package_id"]) == before  # trip, total, version, updated_at


def test_G3_B05_over_budget_plan_preserves_row(real, db):
    tight = build(real, budget_inr=build(real)["trip"]["total_inr"])
    pid = tight["package_id"]
    body = plans(real, pid)
    pricey = next(p for p in body["plans"] if not p["within_budget"])
    before = package_row(db, pid)
    r = patch(real, pid, pricey["change"], 1)
    assert r.status_code == 422 and any(v["code"] == "OVER_BUDGET" for v in r.json()["error"]["details"])
    assert package_row(db, pid) == before


def test_G3_B05_domain_without_extension_503(client, db, fake_domain):
    b = client.post("/api/packages/build", json=MENON)
    assert b.status_code == 201
    pid = b.json()["package_id"]
    before = package_row(db, pid)
    r = patch(client, pid, {"op": "change_city_plan", "cities": ["Tokyo"], "nights": [6]}, 1)
    assert r.status_code == 503 and r.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert client.get(f"/api/packages/{pid}/city-plans").status_code == 503
    assert package_row(db, pid) == before


def test_G3_B06_reload_export_confirm_consistent(real, db):
    b = build(real)
    pid = b["package_id"]
    chosen = other_plan(plans(real, pid))
    trip = patch(real, pid, chosen["change"], 1).json()["trip"]
    got = real.get(f"/api/packages/{pid}").json()
    assert got["trip"] == trip and got["version"] == 2
    old_ids = {i["item_id"] for i in b["trip"]["items"]} - {i["item_id"] for i in trip["items"]}
    exp = real.get(f"/api/packages/{pid}/export")
    assert exp.status_code == 200 and not any(oid in exp.text for oid in old_ids if len(oid) > 6)
    assert hotel_cities(trip) == chosen["cities"]
    conf = real.post(f"/api/packages/{pid}/confirm", headers={"Idempotency-Key": key()})
    assert conf.status_code == 201 and plain(conf.json()["trip"]) == plain(trip)
    ref = conf.json()["reference"]
    assert real.get(f"/api/reservations/{ref}").json()["trip"] == conf.json()["trip"]
    booked = real.get(f"/api/packages/{pid}/export")
    assert booked.status_code == 200 and re.search(ref, booked.text)


def test_G3_B07_change_city_after_booked(real, db):
    b = build(real)
    pid = b["package_id"]
    change = other_plan(plans(real, pid))["change"]
    ref = real.post(f"/api/packages/{pid}/confirm", headers={"Idempotency-Key": key()}).json()["reference"]
    snap = real.get(f"/api/reservations/{ref}").json()
    r = patch(real, pid, change, 1)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_BOOKED"
    assert real.get(f"/api/packages/{pid}/city-plans").status_code == 409
    assert real.get(f"/api/reservations/{ref}").json() == snap
