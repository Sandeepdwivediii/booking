"""SQLAlchemy engine/session helpers. Schema is created additively (CREATE TABLE IF NOT EXISTS)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})


def make_sessionmaker(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def create_schema(engine: Engine) -> None:
    from app import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)  # checkfirst: never drops or alters existing tables
    with engine.begin() as conn:  # idempotent DB-level immutability for bookings (M7)
        for stmt in IMMUTABILITY_DDL:
            conn.execute(text(stmt))


IMMUTABILITY_DDL = (
    """CREATE OR REPLACE FUNCTION app_reservation_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'app_reservations rows are immutable' USING ERRCODE = 'restrict_violation'; END $$""",
    """CREATE OR REPLACE TRIGGER trg_app_reservations_immutable BEFORE UPDATE OR DELETE ON app_reservations
    FOR EACH ROW EXECUTE FUNCTION app_reservation_immutable()""",
    """CREATE OR REPLACE FUNCTION app_package_booked_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.status = 'BOOKED' THEN
        RAISE EXCEPTION 'booked package % is immutable', OLD.id USING ERRCODE = 'restrict_violation';
      END IF;
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $$""",
    """CREATE OR REPLACE TRIGGER trg_app_packages_booked_frozen BEFORE UPDATE OR DELETE ON app_packages
    FOR EACH ROW EXECUTE FUNCTION app_package_booked_frozen()""",
)


def ping(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
