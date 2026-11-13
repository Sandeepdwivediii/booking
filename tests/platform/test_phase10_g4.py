"""Phase 10 (optional extension) G4 — Cheapest / Fastest / Best-rated recommendations. REAL domain (production
loader, original catalog) and REAL PostgreSQL, plus one fake-domain case. Interface: Tanish's d7e9da7
`Domain.recommend(request, trip_id)`; platform routes POST /api/packages/recommend (preview, no persistence) and
POST /api/packages/recommend/select {request, choice} (server recomputes; saves only the chosen package)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import uuid

import pytest

from tests.platform.conftest import make_client
from tests.platform.fixtures.contract_fixtures import CATALOG_DIR, REPO_ROOT
from tests.platform.fixtures.db_helpers import count
from tests.platform.test_phase4_integration import NO_PACKAGE_REQ

MENON = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 6,
         "adults": 2, "children_ages": [6], "budget_inr": 350000}
LABELS = {"cheapest", "fastest", "best_rated"}


def key() -> str:
    return f"p10-{uuid.uuid4()}"


@pytest.fixture
def real(db, test_db_url):
    with make_client(test_db_url, None) as c:
        if not hasattr(c.app.state.runtime.domain, "recommend"):
            pytest.skip("BLOCKED: real domain without the G4 extension")
        yield c


def preview(c, req=MENON) -> dict:
    r = c.post("/api/packages/recommend", json=req)
    assert r.status_code == 200, r.text
    return r.json()


def select(c, choice, req=MENON, k=None):
    return c.post("/api/packages/recommend/select", json={"request": req, "choice": choice},
                  headers={"Idempotency-Key": k or key()})


def by_label(body) -> dict:
    return {r["label"]: r for r in body["recommendations"]}


def same_package(a, b) -> bool:  # identical itinerary apart from the per-package ids
    strip = lambda t: [{k: v for k, v in i.items() if k != "item_id"} for i in t["items"]]  # noqa: E731
    return strip(a) == strip(b) and a["total_inr"] == b["total_inr"]


def _raw(name):
    return json.loads((CATALOG_DIR / f"{name}.json").read_text(encoding="utf-8"))


def oracle_metrics(trip) -> tuple[int, int]:
    """Independent re-derivation from the raw catalog: (flight + intercity minutes, star-nights)."""
    flights = {f["flight_id"]: f for f in _raw("flights")}
    transfers = {t["transfer_id"]: t for t in _raw("transfers")}
    hotels = {h["hotel_id"]: h for h in _raw("hotels")}
    from datetime import date
    minutes = sum(flights[i["catalog_id"]]["duration_minutes"] for i in trip["items"] if i["type"] == "flight")
    minutes += sum(transfers[i["catalog_id"]]["duration_minutes"] for i in trip["items"] if i["role"] == "intercity_transfer")
    stars = sum(hotels[i["catalog_id"]]["stars"] * (date.fromisoformat(i["end"]) - date.fromisoformat(i["start"])).days
                for i in trip["items"] if i["role"] == "hotel_stay")
    return minutes, stars


def test_G4_B01_three_grounded_categories(real, db):
    before = count(db, "app_packages")
    body = preview(real)
    recs = by_label(body)
    assert body["status"] == "BUILT" and set(recs) == LABELS and len(body["recommendations"]) == 3
    for r in recs.values():  # metrics are grounded in the raw catalog, not invented
        assert (r["travel_minutes"], r["star_nights"]) == oracle_metrics(r["trip"])
        assert r["total_inr"] == r["trip"]["total_inr"] == sum(i["price_inr"] for i in r["trip"]["items"])
        assert r["total_inr"] <= MENON["budget_inr"]
        assert real.app.state.runtime.domain.validate(r["trip"], "initial") == []
    built = real.post("/api/packages/build", json=MENON).json()["trip"]
    assert same_package(recs["cheapest"]["trip"], built)  # cheapest == the normal build
    assert recs["fastest"]["travel_minutes"] == min(r["travel_minutes"] for r in recs.values())
    assert recs["best_rated"]["star_nights"] == max(r["star_nights"] for r in recs.values())
    assert count(db, "app_packages") == before + 1  # only the explicit /build above persisted
    js = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert all(s in js for s in ('"Cheapest"', '"Fastest"', '"Best-rated"', "r.travel_minutes", "r.average_stars"))


def test_G4_B02_select_persists_only_chosen(real, db):
    recs = by_label(preview(real))
    before = count(db, "app_packages")
    r = select(real, "best_rated")
    assert r.status_code == 201
    body = r.json()
    assert body["version"] == 1 and body["status"] == "DRAFT" and body["trip"]["trip_id"] == body["package_id"]
    assert same_package(body["trip"], recs["best_rated"]["trip"])
    assert count(db, "app_packages") == before + 1  # no phantom drafts for the other two


def test_G4_B03_reload_export_confirm_selected(real, db):
    sel = select(real, "fastest").json()
    pid, trip = sel["package_id"], sel["trip"]
    got = real.get(f"/api/packages/{pid}").json()
    assert got["trip"] == trip and got["version"] == 1
    assert real.get(f"/api/packages/{pid}/export").status_code == 200
    conf = real.post(f"/api/packages/{pid}/confirm", headers={"Idempotency-Key": key()})
    assert conf.status_code == 201
    snap = conf.json()["trip"]
    assert [i["catalog_id"] for i in snap["items"]] == [i["catalog_id"] for i in trip["items"]]
    assert snap["total_inr"] == trip["total_inr"]
    assert real.get(f"/api/reservations/{conf.json()['reference']}").json()["trip"] == snap
    assert re.search(conf.json()["reference"], real.get(f"/api/packages/{pid}/export").text)


@pytest.mark.parametrize("body", [
    {"request": MENON, "choice": "luxury"},
    {"request": MENON, "choice": "cheapest", "total_inr": 1},
    {"request": MENON, "choice": "cheapest", "trip": {"items": []}},
    {"request": MENON, "choice": "cheapest", "package_id": str(uuid.uuid4())},
    {"request": {**MENON, "price_inr": 1}, "choice": "cheapest"},
    {"request": MENON, "choice": ["cheapest"]},
])
def test_G4_B04_tampered_selection_rejected(real, db, body):
    before = count(db, "app_packages")
    r = real.post("/api/packages/recommend/select", json=body, headers={"Idempotency-Key": key()})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert count(db, "app_packages") == before


def test_G4_B04_fabricated_domain_output_never_persisted(real, db, monkeypatch):
    dom = real.app.state.runtime.domain
    genuine = dom.recommend

    def forged(request, trip_id):
        out = genuine(request, trip_id)
        rec = next(r for r in out["recommendations"] if r["label"] == "fastest")
        rec["trip"]["items"][0]["price_inr"] = 1  # fabricated price
        return out

    monkeypatch.setattr(dom, "recommend", forged, raising=False)
    before = count(db, "app_packages")
    assert real.post("/api/packages/recommend", json=MENON).status_code == 500
    assert select(real, "fastest").status_code == 500
    assert count(db, "app_packages") == before


def test_G4_B05_one_package_wins_several_categories(real, db):
    cheapest_total = preview(real)["recommendations"][0]["total_inr"]
    tight = {**MENON, "budget_inr": cheapest_total}
    body = preview(real, tight)
    recs = by_label(body)
    assert set(recs) == LABELS
    winners = [l for l in LABELS if same_package(recs[l]["trip"], recs["cheapest"]["trip"])]
    assert len(winners) >= 2  # truthful: the same package legitimately wins several labels
    r = select(real, winners[-1], tight)
    assert r.status_code == 201 and same_package(r.json()["trip"], recs["cheapest"]["trip"])
    node = shutil.which("node")
    if node:  # the page groups it into one card carrying every label it won (no invented options)
        js = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        fn = re.search(r"function groupRecommendations\(recs\) \{.*?\n\}", js, re.S).group(0)
        script = fn + f";const g=groupRecommendations({json.dumps(body['recommendations'])});" \
                      "console.log(JSON.stringify(g.map(x=>x.labels)))"
        groups = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)
        assert sum(len(g) for g in groups) == 3 and len(groups) == 4 - len(winners)
        assert sorted(next(g for g in groups if "cheapest" in g)) == sorted(winners)


@pytest.mark.parametrize("req, status", [({**MENON, "budget_inr": 10000}, "OVER_BUDGET"), (NO_PACKAGE_REQ, "NO_PACKAGE")])
def test_G4_B06_honest_no_recommendation(real, db, req, status):
    before = count(db, "app_packages")
    body = preview(real, req)
    assert body["status"] == status and body["recommendations"] == [] and body["trip"] is None and body["reason"]
    r = select(real, "cheapest", req)
    assert r.status_code == 200 and r.json()["status"] == status and "package_id" not in r.json()
    assert count(db, "app_packages") == before


def test_G4_B07_repeat_and_race_selection(real, db):
    before = count(db, "app_packages")
    k = key()
    a, b = select(real, "fastest", k=k), select(real, "fastest", k=k)
    assert (a.status_code, b.status_code) == (201, 200) and a.json()["package_id"] == b.json()["package_id"]
    assert count(db, "app_packages") == before + 1
    k2, out = key(), [None] * 4
    barrier = threading.Barrier(4)

    def go(i):
        barrier.wait()
        out[i] = select(real, "best_rated", k=k2)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert sorted(r.status_code for r in out).count(201) == 1 and all(r.status_code in (200, 201) for r in out)
    assert len({r.json()["package_id"] for r in out}) == 1
    assert count(db, "app_packages") == before + 2
    assert real.post("/api/packages/recommend/select", json={"request": MENON, "choice": "cheapest"}).status_code == 422


def test_G4_B08_single_build_flow_unchanged(real, db):
    r = real.post("/api/packages/build", json=MENON)
    assert r.status_code == 201 and set(r.json()) == {"package_id", "version", "status", "trip"}
    assert real.post("/api/packages/build", json={**MENON, "choice": "fastest"}).status_code == 422  # build stays strict
    pid = r.json()["package_id"]
    att = next(i for i in r.json()["trip"]["items"] if i["type"] == "attraction")
    p = real.patch(f"/api/packages/{pid}", json={"op": "remove_attraction", "item_id": att["item_id"]},
                   headers={"If-Match": '"1"', "Idempotency-Key": key()})
    assert p.status_code == 200
    assert real.post(f"/api/packages/{pid}/confirm", headers={"Idempotency-Key": key()}).status_code == 201


def test_G4_domain_without_extension_503(client, db):
    before = count(db, "app_packages")
    assert client.post("/api/packages/recommend", json=MENON).status_code == 503
    r = client.post("/api/packages/recommend/select", json={"request": MENON, "choice": "cheapest"},
                    headers={"Idempotency-Key": key()})
    assert r.status_code == 503 and r.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert count(db, "app_packages") == before
