# Handoff — Tanish (Developer A, domain)

```text
Developer: Tanish (A)
Phase: 2 (pure rules, time, pricing) — complete. Phases 0 and 1 complete (2e30080).
Branch / commit: feat/tanish-domain (base c49a9cd, contract v1.1.0 frozen)
Implemented:
  Phase 1: app/domain/catalog.py   load_catalog() -> immutable indexed Catalog
           app/domain/adapter.py   create_domain() -> Domain (DomainPort); options() real
  Phase 2: app/domain/timeutil.py  local/UTC, HH:MM + 24:00, overnight windows, scheduled departures
           app/domain/rules.py     room fit, per-night availability, transfer service/area predicates
           app/domain/pricing.py   integer-INR formulas, price_item(), price_trip()
           app/domain/validator.py Violation codes, check_prices(), check_hotel_item()
Tests actually run: python -m pytest -q tests/domain -> 111 passed
                    python -m pytest -q (full)    -> 207 passed, 0 failed, 0 skipped
PASS IDs: SY-01..05; HT-01..07, HT-11..14; TR-09, TR-10, TR-13, TR-16; PR-01..07, PR-17; FL-11, FL-12
FAIL IDs: none   BLOCKED IDs: none
Contract mismatch or dependency: none
Next action: Phase 3 (exact planner + full validate_trip + M8) after approval
```

## Platform-visible behaviour

- `create_domain(catalog_dir)` loads in ~0.1 s; bad catalog → `CatalogUnavailableError` (never partial).
- `options()` is real. `build`, `validate`, `alternatives`, `change` still **raise `NotImplementedError`**
  (Phases 3–5); no fake results.

## Phase 2 design decisions

- **Time zones:** fixed offsets (IST +05:30, JST +09:00, GST +04:00) per DATA_DICTIONARY "no DST";
  no OS tz-data dependency (slim Docker images). A test cross-checks against IANA `zoneinfo` when present.
  Unknown zone → `ValueError`. Flight chronology uses each airport's zone; all 469 official flights satisfy
  `arrival_utc - departure_utc == duration_minutes`.
- **Service windows:** `service_window(date, from, until)`; `until <= from` wraps past midnight;
  `24:00` = next midnight; both ends inclusive. A time is in service if any window starting the day
  before/of/after contains it (handles 00:15 inside 06:00–00:30).
- **Scheduled services:** with `frequency_minutes`, legal departures are `open + n*freq` within the
  window (08:37 rejected for 08:00/60). `next_departure()` returns the earliest legal one at/after a time
  (next day's first if the window has closed). Without frequency, any in-window time is legal.
- **Rooms:** `rooms_fit(adults, children, k, room)` implements all three §4 conditions incl. `adults >= k`;
  `min_rooms()` searches k = 1..adults. Availability is checked per night of `[check_in, check_out)`;
  a missing row is unavailable, never a default. Check-in/out display times are ignored (HT-12).
- **Pricing:** all ints. Flight = seat × all travelers (infants included). PER_VEHICLE quantity =
  `ceil(travelers / max_passengers)`; PER_PERSON and attraction quantity = travelers; missing `child_price`
  → adult price; free items price 0. Hotel quantity is the item's room count `k` (planner choice, checked
  by `check_hotel_item`), price = night price × k × nights.
- **Validation building blocks:** return contract Violations. Codes so far: `UNKNOWN_CATALOG_ID`,
  `ITEM_MALFORMED`, `PRICE_MISMATCH`, `QUANTITY_MISMATCH`, `TOTAL_MISMATCH`, `OVER_BUDGET`,
  `ROOM_HOTEL_MISMATCH`, `HOTEL_WRONG_CITY`, `ROOM_CAPACITY`, `HOTEL_NO_AVAILABILITY`.
  `check_prices` reprices every item from the catalog (client amounts never trusted); budget equality passes.
  Children are counted as `len(traveler.child_ages)`.

## Catalog design (Phase 1, app/domain/catalog.py)

- Pure stdlib + `app.contracts`; read-only file access; SHA-256 per source in `source_sha256`.
- Records deep-frozen (`MappingProxyType` / tuples). Indexes: `airports`, `airports_serving_city`,
  `flights_by_departure[(from,to,local dep date)]`, `flights_by_arrival[(from,to,local arr date)]`
  (cheapest first), `hotels_by_city`, `rooms_by_hotel`, `availability[(room_id,night)]` + `rooms_left()`,
  `airport_transfers[(airport,city)]` (area `None` = wildcard), directed `intercity_transfers[(from,to)]`
  from links only, `city_transfers[city]`, `attractions_by_city` (destination cities only).
- `closed_dates` absent → `()`; `child_price` absent stays absent. Nara attractions → `excluded_attraction_ids`.

## Official dataset audit (Phase 0)

10 airports, 469 flights, 49 hotels, 125 rooms, 2,500 availability rows (1–20 Apr), 66 transfers,
56 attractions; no duplicates or dangling refs. SHA-256 baseline: `tests/domain_fixtures/catalog_builder.py`.
Notable: `24:00` and overnight windows (`TR-KIX-NMB-SHUTTLE` 06:00–00:30), 8 scheduled intercity services
(30-min frequency), `rooms_left` may be 0, 24 free attractions, ATT-TYO-006 closed 10–20 Apr.

## Unresolved / known limits

- Full `validate_trip` (city plan, flight dates/airports/seats, transfer ±60/180-minute timing,
  intercity linkage, attractions, modes) is Phase 3; Phase 2 provides its primitives only.
- Transfers are assumed to stay within one time zone (true for all catalog transfers).
