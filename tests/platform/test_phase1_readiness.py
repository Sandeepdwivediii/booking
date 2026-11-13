"""SY-19 readiness: API starts but reports not-ready and refuses builds while PostgreSQL or the
catalog/domain is unavailable; recovers without restart. Domain = TEST-ONLY fakes (mocked)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from sqlalchemy import text

from app.contracts import CatalogUnavailableError
from app.db import make_engine, make_sessionmaker
from app.services.domain_provider import load_domain
from tests.platform.conftest import make_client
from tests.platform.fixtures.fake_domain import ApiFakeDomain

VALID = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10",
         "nights": 6, "adults": 2, "children_ages": [6], "budget_inr": 350000}
DEAD_DB = "postgresql+psycopg://nobody:x@127.0.0.1:1/bookingDB_test"


def _count(db):
    with db.connect() as c:
        return c.execute(text("SELECT count(*) FROM app_packages")).scalar_one()


def test_SY_19_ready_when_db_and_catalog_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ready", "checks": {"database": "ok", "catalog": "ok"}}


def test_SY_19_catalog_missing_not_ready(db, test_db_url):
    loader = lambda _d: (None, "catalog unavailable: flights.json missing")  # noqa: E731
    with make_client(test_db_url, loader) as c:
        h = c.get("/api/health")
        assert h.status_code == 503
        assert h.json()["checks"] == {"database": "ok", "catalog": "catalog unavailable: flights.json missing"}
        for r in (c.get("/api/catalog/options"), c.post("/api/packages/build", json=VALID)):
            assert r.status_code == 503 and r.json()["error"]["code"] == "CATALOG_UNAVAILABLE"
    assert _count(db) == 0


def test_SY_19_database_down_app_still_starts(fake_domain):
    with make_client(DEAD_DB, lambda _d: (fake_domain, None)) as c:  # startup must not crash
        h = c.get("/api/health")
        assert h.status_code == 503 and h.json()["checks"]["database"] == "unavailable"
        r = c.post("/api/packages/build", json=VALID)
        assert r.status_code == 503 and r.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert fake_domain.calls == []  # no build served while DB is down
        assert "nobody" not in r.text and "password" not in r.text.lower()


def test_SY_19_recovers_when_db_comes_back(db, test_db_url, fake_domain):
    with make_client(DEAD_DB, lambda _d: (fake_domain, None)) as c:
        assert c.get("/api/health").status_code == 503
        rt = c.app.state.runtime  # simulate PostgreSQL becoming reachable
        rt.engine = make_engine(test_db_url)
        rt.sessionmaker = make_sessionmaker(rt.engine)
        assert c.get("/api/health").status_code == 200
        assert c.post("/api/packages/build", json=VALID).status_code == 201


def test_SY_19_recovers_when_catalog_becomes_available(db, test_db_url):
    attempts = []

    def loader(_d):
        attempts.append(1)
        return (None, "catalog unavailable") if len(attempts) == 1 else (ApiFakeDomain(), None)

    with make_client(test_db_url, loader) as c:
        assert c.get("/api/health").status_code == 200  # 1st attempt at startup failed, retried
    assert len(attempts) == 2


# ---------------------------------------------------------------- real loader (no app.domain yet)

def _install_adapter(monkeypatch, create_domain=None, *, missing_attr=False):
    pkg, mod = types.ModuleType("app.domain"), types.ModuleType("app.domain.adapter")
    pkg.__path__ = []
    if not missing_attr:
        mod.create_domain = create_domain
    monkeypatch.setitem(sys.modules, "app.domain", pkg)
    monkeypatch.setitem(sys.modules, "app.domain.adapter", mod)


def test_loader_reports_missing_adapter(monkeypatch):
    if "app.domain" in sys.modules or Path(__file__).resolve().parents[2].joinpath("app", "domain").exists():
        pytest.skip("app.domain is present; covered by integration tests")
    domain, reason = load_domain(Path("unused"))
    assert domain is None and "not integrated" in reason


def test_loader_reports_corrupt_catalog(monkeypatch):
    def create_domain(_d):
        raise CatalogUnavailableError("flights.json is not valid JSON")
    _install_adapter(monkeypatch, create_domain)
    assert load_domain(Path("x")) == (None, "catalog unavailable: flights.json is not valid JSON")


def test_loader_hides_unexpected_errors(monkeypatch):
    def create_domain(_d):
        raise ValueError("boom with internal detail")
    _install_adapter(monkeypatch, create_domain)
    assert load_domain(Path("x")) == (None, "catalog failed to load")


def test_loader_rejects_non_port(monkeypatch):
    _install_adapter(monkeypatch, lambda _d: object())
    domain, reason = load_domain(Path("x"))
    assert domain is None and "DomainPort" in reason


def test_loader_accepts_port(monkeypatch):
    fake = ApiFakeDomain()
    _install_adapter(monkeypatch, lambda _d: fake)
    assert load_domain(Path("x")) == (fake, None)
