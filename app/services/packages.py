"""Package persistence helpers: short transactions, lookups, optional row lock (used from Phase 3)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import ApiError
from app.models import AppPackage, AppReservation


@contextmanager
def transaction(factory: sessionmaker) -> Iterator[Session]:
    """One short transaction: COMMIT on success, ROLLBACK on any exception."""
    with factory.begin() as session:
        yield session


def parse_package_id(raw: str) -> str:
    """Canonical UUID string, or 404 (malformed ids are simply 'not found', never a 500)."""
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError, TypeError):
        raise ApiError(404, "NOT_FOUND", "Package not found.") from None


def insert_draft(session: Session, package_id: str, request: dict, trip: dict) -> AppPackage:
    pkg = AppPackage(id=package_id, status="DRAFT", version=1, request=request, trip=trip,
                     total_inr=trip["total_inr"], budget_inr=trip["budget_inr"])
    session.add(pkg)
    session.flush()
    return pkg


def get_package(session: Session, package_id: str, *, lock: bool = False) -> AppPackage:
    stmt = select(AppPackage).where(AppPackage.id == package_id)
    if lock:
        stmt = stmt.with_for_update()
    pkg = session.execute(stmt).scalar_one_or_none()
    if pkg is None:
        raise ApiError(404, "NOT_FOUND", "Package not found.")
    return pkg


def get_reservation_for(session: Session, package_id: str) -> AppReservation | None:
    return session.execute(
        select(AppReservation).where(AppReservation.package_id == package_id)
    ).scalar_one_or_none()


def package_view(pkg: AppPackage, reservation: AppReservation | None) -> dict:
    return {
        "package_id": pkg.id,
        "version": pkg.version,
        "status": pkg.status,
        "reference": reservation.reference if reservation else None,
        "trip": pkg.trip,
        "created_at": pkg.created_at.isoformat(),
        "updated_at": pkg.updated_at.isoformat(),
    }
