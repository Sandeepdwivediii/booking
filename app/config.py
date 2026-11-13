"""Runtime settings from environment variables (.env is loaded but never overrides real env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env", override=False)

MAX_BODY_BYTES = 64 * 1024  # build requests are tiny; reject oversized bodies early (IN-16)


def _postgres_url(dbname: str) -> str:
    user = os.getenv("POSTGRES_USER", "booking")
    password = os.getenv("POSTGRES_PASSWORD", "")
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "5432")
    return f"postgresql+psycopg://{quote(user)}:{quote(password)}@{host}:{port}/{quote(dbname)}"


@dataclass(frozen=True)
class Settings:
    database_url: str
    catalog_dir: Path


def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL") or _postgres_url(os.getenv("POSTGRES_DB", "bookingDB")),
        catalog_dir=Path(os.getenv("CATALOG_DIR") or REPO_ROOT / "data" / "catalog"),
    )


def get_test_database_url() -> str:
    """Disposable test database (default bookingDB_test on the same server)."""
    return os.getenv("TEST_DATABASE_URL") or _postgres_url("bookingDB_test")
