"""Phase 1 schema: exactly three app tables with the integrity constraints later phases rely on."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db import create_schema


def _pkg(conn, pid="p1", status="DRAFT"):
    conn.execute(text("INSERT INTO app_packages (id, status, version, request, trip, total_inr, budget_inr) "
                      "VALUES (:id, :s, 1, '{}', '{}', 1, 1)"), {"id": pid, "s": status})


def test_exactly_three_tables(db):
    assert set(inspect(db).get_table_names()) == {"app_packages", "app_reservations", "app_idempotency_records"}


def test_create_schema_is_idempotent_and_non_destructive(db):
    with db.begin() as c:
        _pkg(c)
    create_schema(db)
    with db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM app_packages")).scalar_one() == 1


def test_reference_text_keeps_leading_zero(db):  # BK-06 groundwork
    with db.begin() as c:
        _pkg(c)
        c.execute(text("INSERT INTO app_reservations (reference, package_id, snapshot) VALUES ('0123456789', 'p1', '{}')"))
    with db.connect() as c:
        assert c.execute(text("SELECT reference FROM app_reservations")).scalar_one() == "0123456789"


@pytest.mark.parametrize("ref", ["123456789", "12345678901", "12345abcde", "012345678 "])
def test_reference_must_be_ten_digits(db, ref):
    with pytest.raises(IntegrityError), db.begin() as c:
        _pkg(c)
        c.execute(text("INSERT INTO app_reservations (reference, package_id, snapshot) VALUES (:r, 'p1', '{}')"), {"r": ref})


def test_one_reservation_per_package(db):
    with db.begin() as c:
        _pkg(c)
        c.execute(text("INSERT INTO app_reservations (reference, package_id, snapshot) VALUES ('1111111111', 'p1', '{}')"))
    with pytest.raises(IntegrityError), db.begin() as c:
        c.execute(text("INSERT INTO app_reservations (reference, package_id, snapshot) VALUES ('2222222222', 'p1', '{}')"))


def test_idempotency_key_unique_per_package_operation(db):
    ins = text("INSERT INTO app_idempotency_records (package_id, operation, idempotency_key, request_hash, "
               "response_status, response_body) VALUES ('p1', :op, 'k1', 'h', 200, '{}')")
    with db.begin() as c:
        _pkg(c)
        c.execute(ins, {"op": "PATCH"})
        c.execute(ins, {"op": "CONFIRM"})  # same key, other operation: allowed
    with pytest.raises(IntegrityError), db.begin() as c:
        c.execute(ins, {"op": "PATCH"})


@pytest.mark.parametrize("status", ["PENDING", "draft", ""])
def test_package_status_constrained(db, status):
    with pytest.raises(IntegrityError), db.begin() as c:
        _pkg(c, status=status)
