"""Phase 4 integration: real FastAPI app + real DomainPort + disposable PostgreSQL.

Build -> persist -> reload (incl. app restart) -> judge export, then the exported JSON is re-checked
by the real domain validator and repriced from the catalog. Requires the platform (app.main) and a
disposable TEST_DATABASE_URL; without them every test is reported BLOCKED (explicit skip reason).
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import importlib.util

import pytest

if importlib.util.find_spec("app.main") is None:
    pytest.skip("BLOCKED: platform API (app.main) is not on this branch; run on the integration candidate",
                allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import get_settings, get_test_database_url  # noqa: E402
from app.contracts import build_result_shape_errors, trip_shape_errors  # noqa: E402
from app.db import create_schema, make_engine  # noqa: E402
from app.domain.adapter import create_domain  # noqa: E402
from app.domain.catalog import CATALOG_FILES  # noqa: E402
from app.domain.pricing import price_trip  # noqa: E402
from app.domain.rules import transfer_departure_ok  # noqa: E402
from app.domain.timeutil import parse_local  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.domain_fixtures.catalog_builder import OFFICIAL_CATALOG_DIR, OFFICIAL_SHA256  # noqa: E402

TABLES = ("app_idempotency_records", "app_reservations", "app_packages")
MENON = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 6,
         "adults": 2, "children_ages": [6], "budget_inr": 350000}
UAE = {"departure_city": "BOM", "destination_country": "United Arab Emirates", "start_date": "2027-04-10",
       "nights": 4, "adults": 2, "children_ages": [6], "budget_inr": 350000}
NO_PACKAGE = {"departure_city": "MAA", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 6,
              "adults": 2, "children_ages": [6], "budget_inr": 350000}


@pytest.fixture(scope="module")
def test_url():
    url = get_test_database_url()
    u, app_db = make_url(url), make_url(get_settings().database_url)
    if not u.database or "test" not in u.database.lower() or (u.host, u.port, u.database) == (
            app_db.host, app_db.port, app_db.database):
        pytest.skip(f"BLOCKED: refusing non-disposable test database {u.database!r}")
    engine = make_engine(url)
    try:
        create_schema(engine)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"BLOCKED: disposable test database unavailable ({type(exc).__name__})")
    yield url
    engine.dispose()


@pytest.fixture
def engine(test_url):
    eng = make_engine(test_url)
    with eng.begin() as conn:  # guarded disposable test DB only
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    yield eng
    eng.dispose()


def new_client(test_url) -> TestClient:
    settings = dataclasses.replace(get_settings(), database_url=test_url)
    return TestClient(create_app(settings), raise_server_exceptions=False)  # REAL domain loader


@pytest.fixture
def client(engine, test_url):
    with new_client(test_url) as c:
        yield c


@pytest.fixture(scope="module")
def domain():
    return create_domain(str(OFFICIAL_CATALOG_DIR))


def package_rows(engine) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(
            "SELECT id, status, version, request, trip, total_inr, budget_inr FROM app_packages"))]


def assert_trip_consistent(domain, trip: dict, request: dict, trip_id: str) -> None:
    """Exported/persisted trip is valid, repriced exactly from the catalog and matches the request."""
    assert trip["trip_id"] == trip_id
    assert domain.validate(trip, "initial") == []
    priced = price_trip(trip, domain.catalog)
    assert priced["errors"] == []
    for item in trip["items"]:
        assert (item["quantity"], item["price_inr"]) == priced["items"][item["item_id"]]
        assert item["status"] == "PLANNED" and item["booking"] is None and item["sources"] == []
    assert trip["total_inr"] == priced["total_inr"] == sum(i["price_inr"] for i in trip["items"])
    assert trip["start_date"] == request["start_date"] and trip["budget_inr"] == request["budget_inr"]
    assert trip["country"] == request["destination_country"]
    assert trip["traveler"]["home_airport"] == request["departure_city"]


# ------------------------------------------------------------------ readiness

def test_health_ready_with_real_domain_and_db(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["checks"] == {"database": "ok", "catalog": "ok"}


def test_options_served_from_real_domain(client, domain):
    r = client.get("/api/catalog/options")
    assert r.status_code == 200 and r.json() == domain.options()


# ------------------------------------------------------------------ BUILT round trips

@pytest.mark.parametrize("req", [MENON, UAE], ids=["japan_menon", "uae"])
def test_build_persist_reload_export_roundtrip(client, engine, domain, test_url, req):
    expected = domain.build(req, "probe")
    assert expected["status"] == "BUILT"

    r = client.post("/api/packages/build", json=req)
    assert r.status_code == 201, r.text
    body = r.json()
    pid, trip = body["package_id"], body["trip"]
    assert body["version"] == 1 and body["status"] == "DRAFT" and r.headers["etag"] == '"1"'
    assert build_result_shape_errors({**expected, "trip": trip}, req, pid) == []
    assert_trip_consistent(domain, trip, req, pid)
    # Same plan as a direct domain call (only trip_id differs): API adds/changes nothing.
    assert trip["total_inr"] == expected["trip"]["total_inr"]
    assert [i["catalog_id"] for i in trip["items"]] == [i["catalog_id"] for i in expected["trip"]["items"]]

    # PostgreSQL row: id == trip_id, DRAFT v1, JSONB round trip is lossless, totals denormalized.
    (row,) = package_rows(engine)
    assert row["id"] == pid and row["status"] == "DRAFT" and row["version"] == 1
    assert row["trip"] == trip and row["request"] == req
    assert row["total_inr"] == trip["total_inr"] and row["budget_inr"] == req["budget_inr"]

    # Reload.
    g = client.get(f"/api/packages/{pid}")
    assert g.status_code == 200 and g.json()["trip"] == trip and g.headers["etag"] == '"1"'

    # Judge export: draft shape, unchanged content apart from top-level status, valid per real validator.
    e = client.get(f"/api/packages/{pid}/export")
    assert e.status_code == 200
    exported = e.json()
    assert exported["status"] == "DRAFT" and trip_shape_errors(exported, draft=True) == []
    assert {k: v for k, v in exported.items() if k != "status"} == trip
    assert_trip_consistent(domain, exported, req, pid)

    # Backend restart: a fresh app instance serves the identical persisted package and export.
    with new_client(test_url) as again:
        assert again.get(f"/api/packages/{pid}").json()["trip"] == trip
        assert again.get(f"/api/packages/{pid}/export").json() == exported


def test_overnight_pickup_survives_roundtrip_and_is_legal(client, domain):
    pid = client.post("/api/packages/build", json=MENON).json()["package_id"]
    exported = client.get(f"/api/packages/{pid}/export").json()
    out = next(i for i in exported["items"] if i["role"] == "outbound_flight")
    pickup = next(i for i in exported["items"] if i["role"] == "arrival_transfer")
    landed, start = parse_local(out["end"]), parse_local(pickup["start"])
    t = domain.catalog.transfers[pickup["catalog_id"]]
    assert (start - landed).total_seconds() >= 3600  # §4: >= 60 min after landing
    assert transfer_departure_ok(t, start)  # inside service hours (overnight/24:00 aware)
    if start.date() > landed.date():
        assert pickup["warnings"]  # next-morning pickup is disclosed to the traveler


# ------------------------------------------------------------------ non-BUILT results and failures

def test_over_budget_not_persisted(client, engine, domain):
    req = dict(MENON, budget_inr=1000)
    r = client.post("/api/packages/build", json=req)
    assert r.status_code == 200
    body = r.json()
    direct = domain.build(req, "probe")
    assert body["status"] == "OVER_BUDGET" and body["trip"] is None
    assert body["cheapest_total_inr"] == direct["cheapest_total_inr"] and body["shortfall_inr"] == (
        direct["cheapest_total_inr"] - 1000)
    assert body["suggestion"] and package_rows(engine) == []


def test_no_package_not_persisted(client, engine):
    r = client.post("/api/packages/build", json=NO_PACKAGE)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "NO_PACKAGE" and body["reason_code"] == "NO_OUTBOUND_FLIGHT" and body["reason"]
    assert body["cheapest_total_inr"] is None and package_rows(engine) == []


def test_budget_equal_to_cheapest_is_built_via_api(client, domain):
    cheapest = domain.build(UAE, "probe")["cheapest_total_inr"]
    assert client.post("/api/packages/build", json=dict(UAE, budget_inr=cheapest)).status_code == 201
    over = client.post("/api/packages/build", json=dict(UAE, budget_inr=cheapest - 1)).json()
    assert over["status"] == "OVER_BUDGET" and over["shortfall_inr"] == 1


@pytest.mark.parametrize("bad", [
    {"nights": 2}, {"nights": 11}, {"start_date": "2027-03-31"}, {"start_date": "2027-04-17", "nights": 4},
    {"departure_city": "XYZ"}, {"destination_country": "France"}, {"children_ages": [18]}, {"adults": 0},
    {"budget_inr": -1}, {"total_inr": 1},  # client-supplied price field is rejected, never trusted
])
def test_invalid_requests_rejected_and_not_persisted(client, engine, bad):
    r = client.post("/api/packages/build", json={**MENON, **bad})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert package_rows(engine) == []


def test_unknown_and_malformed_package_ids(client):
    assert client.get("/api/packages/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/packages/not-a-uuid/export").status_code == 404


def test_catalog_files_unchanged_after_api_flows(client):
    client.post("/api/packages/build", json=MENON)
    client.post("/api/packages/build", json=UAE)
    hashes = {n: hashlib.sha256((OFFICIAL_CATALOG_DIR / f"{n}.json").read_bytes()).hexdigest() for n in CATALOG_FILES}
    assert hashes == OFFICIAL_SHA256


def test_exported_trip_is_deep_copy_safe(client, domain):
    """Validating/repricing an export never mutates the stored package."""
    pid = client.post("/api/packages/build", json=UAE).json()["package_id"]
    exported = client.get(f"/api/packages/{pid}/export").json()
    snapshot = copy.deepcopy(exported)
    domain.validate(exported, "confirm")
    price_trip(exported, domain.catalog)
    assert exported == snapshot == client.get(f"/api/packages/{pid}/export").json()
