"""Phase 0 adversarial cases SY-01..SY-05 (catalog loader, official + synthetic data)."""

from __future__ import annotations

import hashlib
import subprocess
import sys

import pytest

from app.contracts import CatalogUnavailableError
from app.domain.adapter import create_domain
from app.domain.catalog import CATALOG_FILES, load_catalog
from tests.domain_fixtures.catalog_builder import (
    OFFICIAL_CATALOG_DIR, OFFICIAL_COUNTS, OFFICIAL_SHA256, REPO_ROOT, synthetic_catalog, write_catalog,
)


def _official_hashes() -> dict[str, str]:
    return {n: hashlib.sha256((OFFICIAL_CATALOG_DIR / f"{n}.json").read_bytes()).hexdigest() for n in CATALOG_FILES}


def _snapshot(catalog) -> dict:
    """Comparable view of every keyed index (keys + ordered IDs)."""
    ids = lambda recs, k: tuple(r[k] for r in recs)  # noqa: E731
    return {
        "counts": catalog.counts(),
        "flight_ids": tuple(sorted(catalog.flights)),
        "by_dep": {k: ids(v, "flight_id") for k, v in catalog.flights_by_departure.items()},
        "by_arr": {k: ids(v, "flight_id") for k, v in catalog.flights_by_arrival.items()},
        "hotels_by_city": {k: ids(v, "hotel_id") for k, v in catalog.hotels_by_city.items()},
        "rooms_by_hotel": {k: ids(v, "room_id") for k, v in catalog.rooms_by_hotel.items()},
        "availability": dict(catalog.availability),
        "airport_transfers": {k: ids(v, "transfer_id") for k, v in catalog.airport_transfers.items()},
        "intercity": {k: ids(v, "transfer_id") for k, v in catalog.intercity_transfers.items()},
        "attractions_by_city": {k: ids(v, "attraction_id") for k, v in catalog.attractions_by_city.items()},
        "serving": dict(catalog.airports_serving_city),
        "options": catalog.options(),
    }


@pytest.fixture(scope="module")
def official():
    if not (OFFICIAL_CATALOG_DIR / "destinations.json").is_file():
        pytest.fail(f"P0: official catalog missing at {OFFICIAL_CATALOG_DIR}")  # never silently skip
    return load_catalog(OFFICIAL_CATALOG_DIR)


# ------------------------------------------------------------------------------ SY-01

def test_sy01_official_catalog_loads_with_expected_counts(official):
    assert official.counts() == OFFICIAL_COUNTS
    assert len(official.departure_cities) == 4
    assert set(official.countries) == {"Japan", "United Arab Emirates"}
    assert set(official.country_of_city) == {"Tokyo", "Kyoto", "Osaka", "Dubai", "Abu Dhabi"}


def test_sy01_no_catalog_sql_tables_or_importer():
    """Domain package must not pull in DB/web libraries (no SQL catalog, no importer)."""
    code = ("import sys, app.domain.adapter, app.domain.catalog; "
            "bad=[m for m in ('sqlalchemy','psycopg','fastapi','pydantic','starlette') if m in sys.modules]; "
            "print(','.join(bad))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=REPO_ROOT)
    assert out.stdout.strip() == ""


# ------------------------------------------------------------------------------ SY-02

def test_sy02_reload_is_stable_and_has_no_duplicates(official):
    second = load_catalog(OFFICIAL_CATALOG_DIR)
    third = create_domain(str(OFFICIAL_CATALOG_DIR)).catalog
    assert _snapshot(official) == _snapshot(second) == _snapshot(third)
    # No duplicated IDs inside any index, and no phantom availability rows.
    for recs in official.flights_by_departure.values():
        assert len({r["flight_id"] for r in recs}) == len(recs)
    assert len(official.availability) == OFFICIAL_COUNTS["availability"]
    assert all(official.rooms_left(r, n) is not None for (r, n) in official.availability)
    assert official.rooms_left("RM-TYO-001-A", "2027-04-21") is None  # outside window: missing, not invented


# ------------------------------------------------------------------------------ SY-03

def test_sy03_official_files_match_phase0_baseline():
    assert _official_hashes() == OFFICIAL_SHA256


def test_sy03_hashes_unchanged_after_domain_operations(official):
    before = _official_hashes()
    domain = create_domain(str(OFFICIAL_CATALOG_DIR))
    domain.options()
    load_catalog(OFFICIAL_CATALOG_DIR)
    assert dict(official.source_sha256) == before == _official_hashes() == OFFICIAL_SHA256


# ------------------------------------------------------------------------------ SY-04

def test_sy04_room_with_unknown_hotel_fails_clearly(tmp_path):
    data = synthetic_catalog()
    data["rooms"][0]["hotel_id"] = "HTL-DOES-NOT-EXIST"
    write_catalog(tmp_path, data)
    with pytest.raises(CatalogUnavailableError) as exc:
        load_catalog(tmp_path)
    assert exc.value.code == "CATALOG_UNAVAILABLE"
    assert any("unknown hotel_id 'HTL-DOES-NOT-EXIST'" in e for e in exc.value.details["errors"])


def test_sy04_invalid_catalog_cannot_produce_a_domain(tmp_path):
    data = synthetic_catalog()
    data["rooms"][0]["hotel_id"] = "HTL-DOES-NOT-EXIST"
    write_catalog(tmp_path, data)
    with pytest.raises(CatalogUnavailableError):
        create_domain(str(tmp_path))  # factory never returns a partially loaded domain


# ------------------------------------------------------------------------------ SY-05

def test_sy05_nara_attractions_tolerated_but_not_plannable(official):
    nara = {aid for aid, a in official.attractions.items() if a["city"] == "Nara"}
    assert nara == {"ATT-NAR-001", "ATT-NAR-002"}
    assert set(official.excluded_attraction_ids) == nara
    assert "Nara" not in official.attractions_by_city
    assert "Nara" not in official.country_of_city  # no invented Nara city
    plannable = {a["attraction_id"] for recs in official.attractions_by_city.values() for a in recs}
    assert not plannable & nara and len(plannable) == OFFICIAL_COUNTS["attractions"] - 2
    assert all("Nara" not in c["cities"] for c in official.options()["countries"])


def test_sy05_synthetic_orphan_attraction(tmp_path):
    cat = load_catalog(write_catalog(tmp_path, synthetic_catalog()))
    assert cat.excluded_attraction_ids == ("ATT-N1",)
    assert "ATT-N1" in cat.attractions and "Nara" not in cat.attractions_by_city
