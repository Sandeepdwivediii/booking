"""Shared test bootstrap: make sure the disposable PostgreSQL test database exists before ANY test runs
(domain API tests may run before platform fixtures). Never touches the application database."""

from __future__ import annotations


def pytest_sessionstart(session) -> None:
    try:
        from tests.platform.conftest import _assert_disposable, _ensure_database
        from app.config import get_test_database_url

        url = get_test_database_url()
        _assert_disposable(url)  # refuses non-"test" or application database names
        _ensure_database(url)
    except Exception:
        pass  # PostgreSQL unavailable: DB-backed tests report themselves as BLOCKED
