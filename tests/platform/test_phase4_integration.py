"""Phase 4 REAL integration: production loader -> Tanish's app.domain adapter on the ORIGINAL catalog,
real PostgreSQL test DB. Build -> persist -> reload (incl. restart) -> export, cross-checked by
(1) Tanish's validator, (2) my independent attraction oracle, (3) my independent catalog oracle.
No fake domain anywhere in this module."""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest
from sqlalchemy import text

from app.contracts import booked_trip_shape_errors, build_result_shape_errors, trip_shape_errors
from tests.platform.conftest import make_client
from tests.platform.fixtures.attraction_oracle import check_attractions, load_catalog_attractions
from tests.platform.fixtures.catalog_oracle import RawCatalog, check_export
from tests.platform.fixtures.contract_fixtures import CATALOG_DIR, REPO_ROOT

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "app" / "domain" / "adapter.py").exists(), reason="BLOCKED: domain adapter not merged")

KEYS = ["departure_city", "destination_country", "start_date", "nights", "adults", "children_ages", "budget_inr"]
MENON = dict(zip(KEYS, ["BLR", "Japan", "2027-04-10", 6, 2, [6], 350000]))
BUILT_REQUESTS = {
    "menon_japan": MENON,
    "del_uae_solo": dict(zip(KEYS, ["DEL", "United Arab Emirates", "2027-04-05", 4, 1, [], 200000])),
    "bom_japan_infant_teen": dict(zip(KEYS, ["BOM", "Japan", "2027-04-12", 3, 2, [0, 17], 400000])),
    "maa_uae_10n_two_cities": dict(zip(KEYS, ["MAA", "United Arab Emirates", "2027-04-10", 10, 2, [5], 1000000])),
}
NO_PACKAGE_REQ = dict(zip(KEYS, ["DEL", "Japan", "2027-04-01", 8, 4, [3, 9], 900000]))


@pytest.fixture(scope="module")
def real(test_db_url, test_engine):
    with make_client(test_db_url, None) as c:  # None -> production load_domain (no fake fallback)
        yield c


@pytest.fixture(scope="module")
def raw_catalog():
    return RawCatalog(CATALOG_DIR)


@pytest.fixture(scope="module")
def raw_attractions():
    return load_catalog_attractions(CATALOG_DIR)


def domain(client):
    return client.app.state.runtime.domain


def build_reload_export(client, req):
    b = client.post("/api/packages/build", json=req)
    assert b.status_code == 201, b.text
    pid = b.json()["package_id"]
    g = client.get(f"/api/packages/{pid}")
    e = client.get(f"/api/packages/{pid}/export")
    assert g.status_code == 200 and e.status_code == 200
    return b.json(), g.json(), e.json()


# ---------------------------------------------------------------- production wiring

def test_production_loader_uses_real_domain(real):
    d = domain(real)
    assert type(d).__module__ == "app.domain.adapter"
    h = real.get("/api/health")
    assert h.status_code == 200 and h.json()["status"] == "ready"


def test_no_production_code_references_test_fakes():
    for path in (REPO_ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(m.startswith("tests") for m in mods), path
        assert "FakeDomain" not in path.read_text(encoding="utf-8"), path


def test_options_from_real_catalog(real):
    o = real.get("/api/catalog/options").json()
    assert {d["airport"] for d in o["departure_cities"]} == {"BLR", "DEL", "BOM", "MAA"}
    assert {c["name"] for c in o["countries"]} == {"Japan", "United Arab Emirates"}
    assert o["rules"]["first_night"] == "2027-04-01" and o["rules"]["last_departure"] == "2027-04-20"


# ---------------------------------------------------------------- Build -> Persist -> Reload -> Export (gate 2)

@pytest.mark.parametrize("name", list(BUILT_REQUESTS))
def test_real_build_persist_reload_export(real, db, raw_catalog, raw_attractions, name):
    req = BUILT_REQUESTS[name]
    built, got, exported = build_reload_export(real, req)
    pid = built["package_id"]
    assert got["trip"] == built["trip"] and got["version"] == 1 and got["status"] == "DRAFT"
    assert exported["trip_id"] == pid == built["trip"]["trip_id"]
    assert trip_shape_errors(exported, draft=True) == []
    assert {k: v for k, v in exported.items() if k != "status"} == built["trip"]  # export = stored trip
    with db.connect() as c:
        row = c.execute(text("SELECT total_inr, budget_inr, request FROM app_packages WHERE id=:id"), {"id": pid}).one()
    assert (row.total_inr, row.budget_inr, row.request) == (exported["total_inr"], req["budget_inr"], req)
    # three independent checks of the exported judge JSON
    assert domain(real).validate(exported, "initial") == []          # Tanish's validator
    assert domain(real).validate(exported, "confirm") == []
    assert check_attractions(exported, raw_attractions, "initial") == []  # my attraction oracle
    assert check_export(exported, raw_catalog) == []                  # my catalog/price/time oracle


def test_real_two_city_trip_has_intercity_transfer(real):
    _, _, e = build_reload_export(real, BUILT_REQUESTS["maa_uae_10n_two_cities"])
    hotels = [i for i in e["items"] if i["type"] == "hotel"]
    inter = [i for i in e["items"] if i["role"] == "intercity_transfer"]
    assert len(hotels) == 2 and len(inter) == 1 and inter[0]["city"] == hotels[1]["city"]


def test_real_build_is_deterministic(real):
    a = real.post("/api/packages/build", json=MENON).json()["trip"]
    b = real.post("/api/packages/build", json=MENON).json()["trip"]
    strip = lambda t: {k: v for k, v in t.items() if k != "trip_id"}  # noqa: E731
    assert strip(a) == strip(b)


def test_BK_15_restart_keeps_real_draft(test_db_url, test_engine, raw_catalog):
    with make_client(test_db_url, None) as c1:
        built = c1.post("/api/packages/build", json=MENON).json()
        exp1 = c1.get(f"/api/packages/{built['package_id']}/export").content
    with make_client(test_db_url, None) as c2:  # new process-equivalent: fresh app, engine, catalog load
        g = c2.get(f"/api/packages/{built['package_id']}").json()
        exp2 = c2.get(f"/api/packages/{built['package_id']}/export").content
    assert g["trip"] == built["trip"] and g["version"] == 1 and exp1 == exp2


# ---------------------------------------------------------------- real OVER_BUDGET / NO_PACKAGE (M8, M2)

def test_real_over_budget_reports_exact_cheapest_and_persists_nothing(real, db):  # IN-13 real
    cheapest = real.post("/api/packages/build", json=MENON).json()["trip"]["total_inr"]
    with db.connect() as c:
        before = c.execute(text("SELECT count(*) FROM app_packages")).scalar_one()
    for budget in (0, cheapest - 1):
        req = {**MENON, "budget_inr": budget}
        r = real.post("/api/packages/build", json=req)
        body = r.json()
        assert r.status_code == 200 and body["status"] == "OVER_BUDGET"
        assert body["cheapest_total_inr"] == cheapest and body["shortfall_inr"] == cheapest - budget
        assert body["reason"] and body["suggestion"] and build_result_shape_errors(body, req, "x") == []
    assert real.post("/api/packages/build", json={**MENON, "budget_inr": cheapest}).status_code == 201  # PR-08 boundary
    with db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM app_packages")).scalar_one() == before + 1


def test_real_no_package_has_reason_and_persists_nothing(real, db):
    with db.connect() as c:
        before = c.execute(text("SELECT count(*) FROM app_packages")).scalar_one()
    body = real.post("/api/packages/build", json=NO_PACKAGE_REQ).json()
    assert body["status"] == "NO_PACKAGE" and body["reason_code"] and body["reason"]
    with db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM app_packages")).scalar_one() == before


@pytest.mark.parametrize("req", [
    {**MENON, "nights": 3}, {**MENON, "nights": 10, "start_date": "2027-04-10"},  # IN-03 / IN-06 real
    {**MENON, "children_ages": [0, 17]},                                            # IN-10 real
])
def test_real_valid_inputs_accepted(real, req):
    r = real.post("/api/packages/build", json=req)
    assert r.status_code in (200, 201) and r.json().get("status") in ("DRAFT", "OVER_BUDGET", "NO_PACKAGE")
    if r.status_code == 201:
        t = r.json()["trip"]
        travelers = req["adults"] + len(req["children_ages"])
        assert all(i["quantity"] == travelers for i in t["items"] if i["type"] in ("flight", "attraction"))


def test_real_invalid_input_rejected_before_domain(real):  # IN-07 real
    r = real.post("/api/packages/build", json={**MENON, "departure_city": "XYZ"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------- Phase 4 adversarial export cases (SY-*)
# Each starts from a REAL exported trip, applies the faulty scenario, and asserts it is caught.

@pytest.fixture(scope="module")
def real_export(real):
    return build_reload_export(real, MENON)[2]


def _codes(trip, cat):
    return {c for c, _, _ in check_export(trip, cat)}


def test_SY_06_two_item_sample_export_fails(real_export, raw_catalog, real):
    t = copy.deepcopy(real_export)
    t["items"] = t["items"][:2]
    t["total_inr"] = sum(i["price_inr"] for i in t["items"])
    assert {"FLIGHT_COUNT", "HOTEL_NIGHTS_NOT_EXACT"} <= _codes(t, raw_catalog)
    assert domain(real).validate(t, "initial") != []


def test_SY_07_hotel_without_room_id_fails(raw_catalog, db, real):
    t = copy.deepcopy(build_reload_export(real, MENON)[2])  # fresh row (db fixture truncates per test)
    hotel = next(i for i in t["items"] if i["type"] == "hotel")
    hotel["room_id"] = None
    assert "UNKNOWN_HOTEL_OR_ROOM" in _codes(t, raw_catalog) and trip_shape_errors(t, draft=False)
    pid = t["trip_id"]  # a stored draft that lost room_id is never exported
    with db.begin() as c:
        c.execute(text("UPDATE app_packages SET trip = CAST(:t AS JSONB) WHERE id=:id"),
                  {"t": __import__("json").dumps({k: v for k, v in t.items() if k != "status"}), "id": pid})
    assert real.get(f"/api/packages/{pid}/export").status_code == 500


@pytest.mark.parametrize("field, delta", [("quantity", -1), ("price_inr", 1)])
def test_SY_08_flight_wrong_quantity_or_price_fails(real_export, raw_catalog, field, delta):
    t = copy.deepcopy(real_export)
    f = next(i for i in t["items"] if i["type"] == "flight")
    f[field] += delta
    assert "FLIGHT_PRICE_OR_QTY" in _codes(t, raw_catalog)


def test_SY_09_per_vehicle_transfer_with_people_quantity_fails(real_export, raw_catalog):
    t = copy.deepcopy(real_export)
    travelers = t["traveler"]["adults"] + t["traveler"]["children"]
    pv = [i for i in t["items"] if i["type"] == "transfer"
          and raw_catalog.transfers[i["catalog_id"]]["pricing"] == "PER_VEHICLE"]
    if not pv:  # make one from the catalog so the scenario is always exercised
        pv_id = next(k for k, v in raw_catalog.transfers.items() if v["pricing"] == "PER_VEHICLE")
        tr = next(i for i in t["items"] if i["type"] == "transfer")
        tr["catalog_id"] = pv_id
        pv = [tr]
    pv[0]["quantity"] = travelers  # people instead of vehicles
    assert "TRANSFER_PRICE_OR_QTY" in _codes(t, raw_catalog)


def test_SY_10_draft_with_booked_item_fails_and_export_normalizes(raw_catalog, db, real):
    t = copy.deepcopy(build_reload_export(real, MENON)[2])
    t["items"][0]["status"], t["items"][0]["booking"] = "BOOKED", {"reference": "0123456789"}
    assert "DRAFT_ITEM_SHAPE" in _codes(t, raw_catalog) and trip_shape_errors(t, draft=True)
    pid = t["trip_id"]
    with db.begin() as c:
        c.execute(text("UPDATE app_packages SET trip = CAST(:t AS JSONB) WHERE id=:id"),
                  {"t": __import__("json").dumps({k: v for k, v in t.items() if k != "status"}), "id": pid})
    e = real.get(f"/api/packages/{pid}/export").json()
    assert all(i["status"] == "PLANNED" and i["booking"] is None for i in e["items"])


def test_SY_12_missing_sources_fails(real_export):
    t = copy.deepcopy(real_export)
    del t["items"][1]["sources"]
    assert trip_shape_errors(t, draft=True)
    assert all(i["sources"] == [] for i in real_export["items"])


@pytest.mark.parametrize("typ", ["flight", "hotel", "transfer", "attraction"])
def test_SY_13_invented_catalog_id_fails(real_export, raw_catalog, real, typ):
    t = copy.deepcopy(real_export)
    next(i for i in t["items"] if i["type"] == typ)["catalog_id"] = f"INVENTED-{typ}"
    assert any(c.startswith("UNKNOWN_") for c in _codes(t, raw_catalog))
    assert any(v["code"] == "UNKNOWN_CATALOG_ID" for v in domain(real).validate(t, "initial"))


def test_SY_15_cross_zone_timeline_in_real_time(real_export, raw_catalog):
    t = copy.deepcopy(real_export)
    assert "TIMELINE_ORDER" not in _codes(t, raw_catalog)  # real export is chronological in UTC
    out_f = next(i for i in t["items"] if i["role"] == "outbound_flight")
    arr_t = next(i for i in t["items"] if i["role"] == "arrival_transfer")
    assert "FLIGHT_DURATION_TZ" not in _codes(t, raw_catalog)  # IST departure + duration == JST arrival
    t["items"].remove(out_f)
    t["items"].insert(t["items"].index(arr_t) + 1, out_f)  # wrong order
    assert "TIMELINE_ORDER" in _codes(t, raw_catalog)


# ---------------------------------------------------------------- real confirm (integration smoke; formal BK-* in Phase 6)

def test_real_confirm_books_validated_snapshot(real, raw_catalog):
    built = real.post("/api/packages/build", json=MENON).json()
    r = real.post(f"/api/packages/{built['package_id']}/confirm", headers={"Idempotency-Key": "phase4-confirm-0001"})
    assert r.status_code == 201
    res = r.json()
    assert booked_trip_shape_errors(res["trip"], res["reference"]) == []
    assert domain(real).validate(res["trip"], "confirm") == []  # validator ignores booking metadata
    as_draft = copy.deepcopy(res["trip"])
    for i in as_draft["items"]:
        i["status"], i["booking"] = "PLANNED", None
    assert check_export(as_draft, raw_catalog) == []
    assert real.get(f"/api/reservations/{res['reference']}").json() == res


def test_real_edits_report_not_available_until_phase5(real):
    pid = real.post("/api/packages/build", json=MENON).json()["package_id"]
    alt = real.get(f"/api/packages/{pid}/alternatives", params={"component": "outbound_flight"})
    ch = real.patch(f"/api/packages/{pid}", json={"op": "remove_attraction", "item_id": "i1"},
                    headers={"If-Match": '"1"', "Idempotency-Key": "phase4-edit-0001"})
    for r in (alt, ch):
        assert r.status_code == 503 and r.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert real.get(f"/api/packages/{pid}").json()["version"] == 1


# ---------------------------------------------------------------- cross-lane sweep (both independent oracles)

def test_real_build_sweep_passes_independent_oracles(real, raw_catalog, raw_attractions):
    import itertools
    from datetime import date, timedelta

    from app.services.export import draft_export

    d, built, multi = domain(real), 0, 0
    for home, country, start, nights, (adults, kids) in itertools.product(
            ["BLR", "DEL", "BOM", "MAA"], ["Japan", "United Arab Emirates"], ["2027-04-01", "2027-04-10"],
            [3, 6, 10], [(1, []), (2, [0, 17]), (5, [2])]):
        if date.fromisoformat(start) + timedelta(days=nights) > date(2027, 4, 20):
            continue
        req = dict(zip(KEYS, [home, country, start, nights, adults, kids, 10_000_000]))
        res = d.build(req, "sweep")
        assert build_result_shape_errors(res, req, "sweep") == [], req
        if res["status"] == "BUILT":
            built += 1
            multi += sum(i["type"] == "hotel" for i in res["trip"]["items"]) > 1
            e = draft_export(res["trip"])
            assert check_export(e, raw_catalog) == [], req
            assert check_attractions(e, raw_attractions, "initial") == [], req
    assert built >= 40 and multi >= 10
