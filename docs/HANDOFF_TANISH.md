# Handoff — Tanish (Developer A, domain)

```text
Developer: Tanish (A)
Phase: 0 (SY-01..05) + 1 (catalog loader/indexes, options()) — complete
Branch / commit: feat/tanish-domain (base c49a9cd, contract v1.1.0 frozen)
Implemented:
  app/domain/catalog.py  load_catalog(root) -> immutable Catalog; validates and indexes all 8 files
  app/domain/adapter.py  create_domain(catalog_dir) -> Domain (DomainPort); options() real
Files changed: app/domain/{__init__,catalog,adapter}.py, tests/domain/*, tests/domain_fixtures/*, this file
Tests actually run: python -m pytest -q tests/domain -> 61 passed
                    python -m pytest -q (full)    -> 157 passed, 0 failed, 0 skipped
PASS IDs: SY-01 SY-02 SY-03 SY-04 SY-05 (+ platform test_real_domain_factory_matches_contract now active, passing)
FAIL IDs: none   BLOCKED IDs: none
Contract mismatch or dependency: none
Next action: Phase 2 (pure availability/capacity, time, pricing, validate_trip primitives) after approval
```

## Platform-visible behaviour

- `create_domain(catalog_dir)` loads in ~0.1 s. Any missing/corrupt/inconsistent file → `CatalogUnavailableError`
  (`details = {"errors": [...<=50], "error_count": n}`); never a partial catalog.
- `options()` is real (contract shape, fresh dicts).
- `build`, `validate`, `alternatives`, `change` **raise `NotImplementedError`** until Phases 2–5.
  They do not return fake results; the API should map this to 500/503 for now.

## Catalog design (app/domain/catalog.py)

- Pure stdlib + `app.contracts`; files opened read-only; SHA-256 of each source kept in `source_sha256`.
- Records are deep-frozen (`MappingProxyType` / tuples); mutation raises `TypeError`.
- Indexes: `airports`, `airports_serving_city` (from `serves_cities`), `flights_by_departure[(from,to,local dep date)]`,
  `flights_by_arrival[(from,to,local arr date)]` (cheapest first, deterministic), `hotels_by_city`, `rooms_by_hotel`,
  `availability[(room_id, night)]` + `rooms_left()` (missing row → `None`), `airport_transfers[(airport, city)]`
  (area filtering — `None` = wildcard — is the caller's job), directed `intercity_transfers[(from_city,to_city)]`
  from `destinations.links` only, `city_transfers[city]`, `attractions_by_city` (destination cities only).
- Normalization: `closed_dates` absent → `()`. `child_price` absent stays absent (pricing falls back to adult price, Phase 2).
- Known anomaly: `ATT-NAR-001/002` (Nara) load but are listed in `excluded_attraction_ids`, never plannable.

## Official dataset audit (Phase 0)

Counts: 10 airports, 469 flights, 49 hotels, 125 rooms, 2,500 availability rows (20 nights × 125 rooms, 1–20 Apr),
66 transfers, 56 attractions. No duplicate IDs, no dangling references. SHA-256 baseline in
`tests/domain_fixtures/catalog_builder.py::OFFICIAL_SHA256`. Notable data: `24:00` and overnight windows
(`TR-KIX-NMB-SHUTTLE` 06:00–00:30), `rooms_left` can be 0, `seats_left` min 1, 24 free attractions,
4 attractions with `closed_dates` (ATT-TYO-006 closed 10–20 Apr).
