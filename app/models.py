"""The only three writable tables. The catalog stays in memory (domain), never in SQL."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AppPackage(Base):
    """Mutable draft (trip JSON) with optimistic version; becomes BOOKED once."""

    __tablename__ = "app_packages"
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT', 'BOOKED')", name="ck_app_packages_status"),
        CheckConstraint("version >= 1", name="ck_app_packages_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # == trip.trip_id (UUID)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trip: Mapped[dict] = mapped_column(JSONB, nullable=False)
    total_inr: Mapped[int] = mapped_column(BigInteger, nullable=False)
    budget_inr: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AppReservation(Base):
    """Immutable booked snapshot. Reference is TEXT, exactly 10 ASCII digits (leading zeros kept)."""

    __tablename__ = "app_reservations"
    __table_args__ = (
        CheckConstraint("reference ~ '^[0-9]{10}$'", name="ck_app_reservations_reference"),
    )

    reference: Mapped[str] = mapped_column(Text, primary_key=True)
    package_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("app_packages.id"), nullable=False, unique=True
    )
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AppIdempotencyRecord(Base):
    """Stored response for (package, operation, Idempotency-Key); request_hash detects key reuse."""

    __tablename__ = "app_idempotency_records"
    __table_args__ = (
        UniqueConstraint("package_id", "operation", "idempotency_key", name="uq_app_idempotency_key"),
        CheckConstraint("operation IN ('PATCH', 'CONFIRM')", name="ck_app_idempotency_operation"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    package_id: Mapped[str] = mapped_column(String(36), ForeignKey("app_packages.id"), nullable=False)
    operation: Mapped[str] = mapped_column(String(10), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
