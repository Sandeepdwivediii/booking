"""Platform test fixtures: guarded disposable PostgreSQL test DB + app clients with a fake domain.

Safety: tests only ever touch the database from get_test_database_url() (default bookingDB_test),
and refuse to run if it looks like the app's own database.
"""

from __future__ import annotations

import dataclasses

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config import get_settings, get_test_database_url
from app.db import Base, create_schema, make_engine
from app.main import create_app
from tests.platform.fixtures.fake_domain import ApiFakeDomain

TABLES = ("app_idempotency_records", "app_reservations", "app_packages")


def _assert_disposable(test_url: str) -> None:
    t, m = make_url(test_url), make_url(get_settings().database_url)
    if not t.database or "test" not in t.database.lower():
        pytest.exit(f"Refusing to use non-test database {t.database!r} for tests", returncode=2)
    if (t.host, t.port, t.database) == (m.host, m.port, m.database):
        pytest.exit("TEST_DATABASE_URL points at the application database", returncode=2)


def _ensure_database(test_url: str) -> None:
    u = make_url(test_url)
    conninfo = dict(host=u.host, port=u.port or 5432, user=u.username, password=u.password,
                    dbname="postgres", connect_timeout=3, autocommit=True)
    with psycopg.connect(**conninfo) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (u.database,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{u.database}"')  # name validated by _assert_disposable


@pytest.fixture(scope="session")
def test_db_url() -> str:
    url = get_test_database_url()
    _assert_disposable(url)
    try:
        _ensure_database(url)
    except Exception as e:  # report as BLOCKED rather than failing unrelated tests
        pytest.skip(f"BLOCKED: test database unavailable ({type(e).__name__})")
    return url


@pytest.fixture(scope="session")
def test_engine(test_db_url):
    engine = make_engine(test_db_url)
    Base.metadata.drop_all(engine)  # disposable test DB only (guarded above)
    create_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(test_engine):
    with test_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    return test_engine


@pytest.fixture
def fake_domain() -> ApiFakeDomain:
    return ApiFakeDomain()


def make_client(database_url: str, loader) -> TestClient:
    settings = dataclasses.replace(get_settings(), database_url=database_url)
    return TestClient(create_app(settings, domain_loader=loader), raise_server_exceptions=False)


@pytest.fixture
def client(db, test_db_url, fake_domain):
    with make_client(test_db_url, lambda _dir: (fake_domain, None)) as c:
        yield c
