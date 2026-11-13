"""Phase 8 (optional extension) G1/G2 acceptance on the REAL domain (production loader, original catalog)
and REAL PostgreSQL. No contract change: uses TripItem.warnings, ChangeResult.adjustments and
Alternative.price_diff_inr/new_total_inr from contract v1.1.0."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import uuid

import pytest

from app.api.errors import ApiError
from app.services import transactions as tx
from tests.platform.conftest import make_client
from tests.platform.fixtures.contract_fixtures import CATALOG_DIR, REPO_ROOT
from tests.platform.fixtures.db_helpers import package_row

MENON = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 6,
         "adults": 2, "children_ages": [6], "budget_inr": 350000}


def key() -> str:
    return f"p8-{uuid.uuid4()}"


@pytest.fixture
def real(db, test_db_url):
    with make_client(test_db_url, None) as c:
        if type(c.app.state.runtime.domain).__module__ != "app.domain.adapter":
            pytest.skip("BLOCKED: real domain adapter not available")
        yield c


def build(c, **over) -> dict:
    r = c.post("/api/packages/build", json={**MENON, **over})
    assert r.status_code == 201, r.text
    return r.json()


def alts(c, pid, component, **f) -> list[dict]:
    r = c.get(f"/api/packages/{pid}/alternatives", params={"component": component, **f})
    assert r.status_code == 200, r.text
    return r.json()["alternatives"]


def patch(c, pid, change, version, k=None):
    return c.patch(f"/api/packages/{pid}", json=change, headers={"If-Match": f'"{version}"', "Idempotency-Key": k or key()})


def hotel_of(trip):
    return next(i for i in trip["items"] if i["type"] == "hotel")


def domain(c):
    return c.app.state.runtime.domain


# ---------------------------------------------------------------- G1 — dependent bookings

def test_G1_B01_unresolved_dependent_conflict_rejected_atomically(real, db):
    b = build(real)
    out = next(i for i in b["trip"]["items"] if i["role"] == "outbound_flight")
    flights = json.loads((CATALOG_DIR / "flights.json").read_text(encoding="utf-8"))
    conflict = None
    for f in flights:  # a real flight whose arrival leaves no legal airport transfer / seats
        if f["from"] == "BLR" and f["arrival_local"][:10] == "2027-04-10" and f["flight_id"] != out["catalog_id"]:
            change = {"op": "replace_flight", "item_id": out["item_id"], "flight_id": f["flight_id"]}
            res = domain(real).change(b["trip"], change)
            if not res["ok"] and any(v["item_id"] for v in res["violations"]):
                conflict = change
                break
    assert conflict, "catalog offers no conflicting outbound flight for this trip"
    before = package_row(db, b["package_id"])
    r = patch(real, b["package_id"], conflict, 1)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_CHANGE"
    assert all(v["message"] for v in r.json()["error"]["details"])
    assert any(v["item_id"] for v in r.json()["error"]["details"])  # names the affected booking
    assert package_row(db, b["package_id"]) == before                 # JSON, total, version, updated_at


def test_G1_B02_dependent_effects_reported_and_trip_stays_valid(real, db):
    b = build(real)
    options = alts(real, b["package_id"], "hotel", item_id=hotel_of(b["trip"])["item_id"])
    affected = None
    for a in options:
        res = domain(real).change(b["trip"], a["change"])
        if res["ok"] and res["adjustments"]:
            affected = a
            break
    assert affected, "no hotel alternative with dependent effects"
    r = patch(real, b["package_id"], affected["change"], 1)
    assert r.status_code == 200
    body = r.json()
    assert body["adjustments"] and any("transfer" in a.lower() or "check-in" in a.lower() for a in body["adjustments"])
    assert domain(real).validate(body["trip"], "modified") == []  # never an invalid itinerary
    assert package_row(db, b["package_id"])["trip"] == body["trip"]


def _warnings(trip):
    return {(i["catalog_id"], w) for i in trip["items"] for w in i["warnings"]}


def test_G1_B03_switching_alternative_drops_stale_warning(real, db):
    b = build(real)
    pid, base_hotel = b["package_id"], hotel_of(b["trip"])
    first = next(a for a in alts(real, pid, "hotel", item_id=base_hotel["item_id"])
                 if domain(real).change(b["trip"], a["change"])["adjustments"])
    t1 = patch(real, pid, first["change"], 1).json()["trip"]
    w1 = _warnings(t1)
    back = next(a for a in alts(real, pid, "hotel", item_id=hotel_of(t1)["item_id"]) if a["catalog_id"] == base_hotel["catalog_id"])
    t2 = patch(real, pid, back["change"], 2).json()["trip"]
    assert not any(cid == first["catalog_id"] for cid, _ in _warnings(t2))  # warnings of the old candidate are gone
    assert w1 != _warnings(t2)                                               # recomputed for the current choice
    current = {i["catalog_id"] for i in t2["items"]}
    assert all(cid in current for cid, _ in _warnings(t2))                   # only about items in the current trip
    # Note for Tanish: initial build omits the edit-time check-in note for the same hotel (consistency gap, not a rule break).
    js = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert re.search(r"async function showAlternatives\([^)]*\) \{\s*const panel = ctx.panel;\s*packageMessage\(ctx, null\);", js)


def test_G1_B04_two_tabs_dependent_edits_one_commits(real, db):
    b = build(real)
    pid = b["package_id"]
    options = alts(real, pid, "hotel", item_id=hotel_of(b["trip"])["item_id"])[:2]
    rt = real.app.state.runtime
    barrier, out = threading.Barrier(2), [None, None]

    def go(i):
        barrier.wait()
        try:
            out[i] = tx.edit_package(rt.sessionmaker, rt.domain, pid, 1, key(), options[i]["change"])
        except ApiError as e:
            out[i] = (e.status, {"error": {"code": e.code}})

    ts = [threading.Thread(target=go, args=(i,)) for i in (0, 1)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert sorted(o[0] for o in out) == [200, 409]
    winner = next(o[1] for o in out if o[0] == 200)
    row = package_row(db, pid)
    assert row["version"] == 2 and row["trip"] == winner["trip"]


def test_G1_B05_safe_edit_not_blocked_or_warned(real, db):
    b = build(real)
    att = next(i for i in b["trip"]["items"] if i["type"] == "attraction")
    r = patch(real, b["package_id"], {"op": "remove_attraction", "item_id": att["item_id"]}, 1)
    assert r.status_code == 200 and r.json()["adjustments"] == []
    before = {k for k in _warnings(b["trip"]) if k[0] != att["catalog_id"]}
    assert _warnings(r.json()["trip"]) == before  # no fabricated warning


# ---------------------------------------------------------------- G2 — price differences

def _check_diffs(c, pid, trip, component, **f):
    listed = alts(c, pid, component, **f)
    for a in listed:
        assert a["new_total_inr"] is not None and a["price_diff_inr"] == a["new_total_inr"] - trip["total_inr"]
    for a in listed[:5]:  # the preview equals what the server would actually save
        res = domain(c).change(trip, a["change"])
        assert res["ok"] and res["trip"]["total_inr"] == a["new_total_inr"]
    return listed


def test_G2_B01_signed_differences_against_saved_choice(real, db):
    b = build(real)
    pid = b["package_id"]
    listed = _check_diffs(real, pid, b["trip"], "hotel", item_id=hotel_of(b["trip"])["item_id"])
    signs = {(a["price_diff_inr"] > 0) - (a["price_diff_inr"] < 0) for a in listed}
    pricey = max(listed, key=lambda a: a["price_diff_inr"])
    t2 = patch(real, pid, pricey["change"], 1).json()["trip"]
    listed2 = _check_diffs(real, pid, t2, "hotel", item_id=hotel_of(t2)["item_id"])
    signs |= {(a["price_diff_inr"] > 0) - (a["price_diff_inr"] < 0) for a in listed2}
    assert signs == {1, 0, -1}, signs  # costlier, equal and cheaper all occur on real data
    node = shutil.which("node")
    if node:  # UI label for each sign (same function the page uses)
        js = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        fn = re.search(r"function signedDiff\(diff\) \{.*?\n\}", js, re.S).group(0)
        script = 'const inr=(n)=>"₹"+Number(n).toLocaleString("en-IN");' + fn + ';console.log(JSON.stringify([signedDiff(1500),signedDiff(-250),signedDiff(0)]))'
        labels = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True, encoding="utf-8").stdout)
        assert labels == ["+₹1,500", "−₹250", "same price"]


@pytest.mark.parametrize("forged", [{"price_diff_inr": -99999}, {"total_inr": 1}, {"new_total_inr": 1}])
def test_G2_B02_forged_client_prices_ignored(real, db, forged):
    b = build(real)
    a = alts(real, b["package_id"], "hotel", item_id=hotel_of(b["trip"])["item_id"])[0]
    before = package_row(db, b["package_id"])
    r = patch(real, b["package_id"], {**a["change"], **forged}, 1)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert package_row(db, b["package_id"]) == before
    ok = patch(real, b["package_id"], a["change"], 1).json()["trip"]
    assert ok["total_inr"] == a["new_total_inr"] == sum(i["price_inr"] for i in ok["items"])  # server-derived


def test_G2_B03_stale_listed_option_cannot_overwrite_newer_draft(real, db):
    b = build(real)
    pid = b["package_id"]
    listed_v1 = alts(real, pid, "hotel", item_id=hotel_of(b["trip"])["item_id"])
    att = next(i for i in b["trip"]["items"] if i["type"] == "attraction")
    v2 = patch(real, pid, {"op": "remove_attraction", "item_id": att["item_id"]}, 1).json()  # another tab
    before = package_row(db, pid)
    r = patch(real, pid, listed_v1[0]["change"], 1)  # stale preview
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_CONFLICT"
    assert package_row(db, pid) == before
    _check_diffs(real, pid, v2["trip"], "hotel", item_id=hotel_of(v2["trip"])["item_id"])  # repriced vs v2
    js = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'case "VERSION_CONFLICT":\n      ctx.panel.hidden = true;' in js  # UI drops the stale option list


def test_G2_B04_over_budget_alternative_never_saved(real, db):
    big = build(real)
    hotel = hotel_of(big["trip"])
    pricier = max(alts(real, big["package_id"], "hotel", item_id=hotel["item_id"]), key=lambda a: a["price_diff_inr"])
    assert pricier["price_diff_inr"] > 0
    tight = build(real, budget_inr=big["trip"]["total_inr"])
    listed = alts(real, tight["package_id"], "hotel", item_id=hotel["item_id"])
    assert all(a["new_total_inr"] <= tight["trip"]["budget_inr"] for a in listed)  # no over-budget offer
    before = package_row(db, tight["package_id"])
    r = patch(real, tight["package_id"], pricier["change"], 1)  # forced over-budget change
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_CHANGE"
    assert any(v["code"] == "OVER_BUDGET" for v in r.json()["error"]["details"])
    assert package_row(db, tight["package_id"]) == before
    assert real.post(f"/api/packages/{tight['package_id']}/confirm", headers={"Idempotency-Key": key()}).status_code == 201
