"""TEST-ONLY helpers for persisting synthetic drafts and reading raw rows in the disposable test DB."""

from __future__ import annotations

import copy
import uuid

from sqlalchemy import text

from app.db import make_sessionmaker
from app.services.packages import insert_draft, transaction
from tests.platform.fixtures.fake_domain import make_trip

REQ = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10",
       "nights": 6, "adults": 2, "children_ages": [6], "budget_inr": 350000}


def persist(engine, trip: dict | None = None, request: dict | None = None) -> tuple[str, dict]:
    pid = str(uuid.uuid4())
    request = request or REQ
    trip = copy.deepcopy(trip) if trip else make_trip(request, pid)
    trip["trip_id"] = pid
    with transaction(make_sessionmaker(engine)) as s:
        insert_draft(s, pid, request, trip)
    return pid, trip


def package_row(engine, pid: str) -> dict:
    with engine.connect() as c:
        return dict(c.execute(text("SELECT * FROM app_packages WHERE id=:id"), {"id": pid}).mappings().one())


def count(engine, table: str, pid: str | None = None) -> int:
    sql = f"SELECT count(*) FROM {table}" + (" WHERE package_id=:id" if pid else "")
    with engine.connect() as c:
        return c.execute(text(sql), {"id": pid}).scalar_one()
