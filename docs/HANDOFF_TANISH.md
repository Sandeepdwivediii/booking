# Handoff — Tanish (Developer A, domain)

```text
Developer: Tanish (A)
Phase: 3 (exact planner, full validate_trip, M8) — complete. Phases 0-2 complete (20dbe5b).
Branch / commit: feat/tanish-domain (base c49a9cd, contract v1.1.0 frozen)
Implemented (Phase 3):
  app/domain/planner.py    build_trip(): exact cheapest package -> BUILT / OVER_BUDGET / NO_PACKAGE
  app/domain/validator.py  validate_trip(catalog, trip, mode): full §4 rules for initial/modified/confirm
  app/domain/rules.py      + attraction open/visit/start helpers
  app/domain/timeutil.py   + latest_departure(), weekday_code()
  app/domain/edits.py      Phase 5 SKELETON: contract argument checks, NotImplementedError for real work
  app/domain/adapter.py    build/validate wired; alternatives/change -> edits skeleton
Tests actually run: python -m pytest -q tests/domain -> 273 passed
                    python -m pytest -q (full)    -> 369 passed, 0 failed, 0 skipped (~10 s)
PASS IDs (Phase 3): CT-01..10; FL-01..10; TR-01..08, TR-11, TR-12, TR-14, TR-15, TR-17; HT-08..10;
                    PR-08..15; SY-14   (+ SY-17 smoke on official data; earlier SY/HT/TR/PR/FL still pass)
FAIL IDs: none   BLOCKED IDs: none
Contract mismatch or dependency: none
Next action: Phase 4 (integration with platform build API, real-catalog oracle runs) after approval
```

## Platform-visible behaviour

- `build(request, trip_id)` is real. Structurally/semantically invalid request → `InvalidRequestError`
  (country/airport unknown, nights outside 3..10, adults < 1, ages outside 0..17, budget < 0, bad types).
  Dates outside the data window → `NO_PACKAGE` / `OUTSIDE_DATA_WINDOW`.
- `BUILT` returns the globally cheapest package (`cheapest_total_inr == trip.total_inr`), already
  self-validated with `validate_trip(..., "initial")`. A planner/validator disagreement raises
  `RuntimeError` (→ 500) instead of returning an invalid package.
- `OVER_BUDGET`: exact cheapest total, `shortfall_inr`, reason and suggestion. `NO_PACKAGE`: reason code
  from the deepest failing stage (`NO_OUTBOUND_FLIGHT`, `NO_RETURN_FLIGHT`, `NO_HOTEL_AVAILABILITY`,
  `NO_INTERCITY_TRANSFER`, `NO_AIRPORT_TRANSFER`, `NO_ATTRACTION_SCHEDULE`, `NO_CITY_PLAN`, `OUTSIDE_DATA_WINDOW`).
- `validate(trip, mode)` is real; unknown mode → `InvalidRequestError`; ignores status/booking metadata.
- `alternatives` / `change`: argument errors per contract (unknown component / missing filter →
  `InvalidRequestError`; unknown op → `ok=false`, `UNKNOWN_OP`); real work raises `NotImplementedError` (Phase 5).
- Item `warnings` flag a next-morning airport pickup or an evening-before departure pickup.
- Official catalog runtime: 696-request sweep (4 homes × 2 countries × dates × nights × parties), worst 0.14 s.

## Search strategy and why it is exact

For each city plan (all permutations of distinct cities along **directed** links, every split with
>= `min_nights_per_city`, summing to `nights`), total cost decomposes into:

| Part | Choice | Why exact |
|---|---|---|
| Middle hotels | cheapest (hotel, room, minimal k) available every night | hotel only interacts with other parts via `area`, which matters only for airport transfers (first/last city). Minimal fitting k is cheapest and needs the least stock. |
| First/last hotel | cheapest per **area**, then combined with every (flight, airport transfer) pair | within one area all hotels are interchangeable for every rule |
| Intercity | every linked transfer enumerated per change day (cartesian product, ≤ 2 per link) | price is time-independent |
| Airport pickup | arrival: earliest legal ≥ landing+60; departure: latest legal with airport ≥ 180 min early | price is time-independent; extreme times maximise slack for every other constraint |
| Attractions | exactly one per full day, distinct, min total | prices ≥ 0 so extra visits never help; feasible sets form a transversal matroid, so greedy-by-price with augmenting-path matching gives the exact min-weight basis |

No top-K, sampling, time limits or heuristic cuts. Ties break deterministically (plan order, then
price, then catalog IDs). `PR-15` monotonicity follows because the cheapest package is budget-independent.
Display-only choices (attraction 10:00 preference, intercity ≥ 09:00 preference) always fall back to the
earliest legal time, so they never change feasibility or price.

**Evidence:** independent brute-force oracle (`tests/domain_fixtures/oracle.py`) re-implements filters
naively (full scans, every k, every attraction permutation, minute-by-minute time scans) and validates
combinations with `validate_trip`. Planner == oracle on 150/150 random catalogs during development
(84 BUILT, others NO_PACKAGE across 4 stages); 60 seeds run in the suite, also checking OVER_BUDGET at
`min − 1`. On official data (oracle too slow there) a single-swap test confirms no cheaper valid swap.

## validate_trip rule coverage (stable codes)

Header: `TRIP_MALFORMED`, `TRAVELER_INVALID`, `COUNTRY_UNKNOWN`, `HOME_AIRPORT_UNKNOWN`, `TRIP_DURATION`,
`OUTSIDE_DATA_WINDOW`. Prices: `PRICE_MISMATCH`, `QUANTITY_MISMATCH`, `TOTAL_MISMATCH`, `OVER_BUDGET`,
`UNKNOWN_CATALOG_ID`, `ITEM_MALFORMED`. Hotels/plan: `HOTEL_MISSING`, `HOTEL_GAP_OVERLAP`, `CITY_NOT_IN_COUNTRY`,
`CITY_REPEATED`, `CITY_MIN_NIGHTS`, `NO_CITY_LINK`, `ROOM_HOTEL_MISMATCH`, `HOTEL_WRONG_CITY`, `ROOM_CAPACITY`,
`HOTEL_NO_AVAILABILITY`. Flights: `FLIGHT_COUNT`, `FLIGHT_WRONG_ROUTE`, `FLIGHT_WRONG_AIRPORT`,
`FLIGHT_WRONG_DATE`, `FLIGHT_SEATS`, `FLIGHT_TIME_MISMATCH`. Transfers: `AIRPORT_TRANSFER_COUNT`,
`TRANSFER_WRONG_TYPE`, `TRANSFER_WRONG_AIRPORT`, `TRANSFER_WRONG_AREA`, `TRANSFER_TOO_EARLY`,
`TRANSFER_LATE_FOR_FLIGHT`, `TRANSFER_OUT_OF_SERVICE`, `TRANSFER_TIME_MISMATCH`, `INTERCITY_MISSING`,
`INTERCITY_UNEXPECTED`, `INTERCITY_NOT_LINKED`, `INTERCITY_WRONG_DIRECTION`, `CITY_TAXI_INVALID`,
`TIMELINE_CONFLICT`. Attractions: `ATTRACTION_WRONG_CITY`, `ATTRACTION_CLOSED`, `ATTRACTION_TIME_MISMATCH`,
`ATTRACTION_TIMELINE`, `ATTRACTION_DUPLICATE`, `ATTRACTION_OVERLAP`, `ATTRACTION_COUNT` (contract day rules).

## Earlier phases (unchanged)

- Phase 2: fixed-offset time zones (no DST), `24:00`/overnight windows, frequency departures, room fit incl.
  `adults >= k`, per-night availability (missing = unavailable), integer pricing, `check_prices`, `check_hotel_item`.
- Phase 1: immutable indexed `Catalog`, validated load, `options()`. Phase 0: SHA-256 baseline, counts,
  Nara attractions excluded.

## Limitations / risks

- The planner never adds optional extras (change-day/arrival-day attractions, second daily attraction,
  city taxis): they only add cost. The validator accepts them for edits (Phase 5).
- Cheapest packages can include a next-morning airport pickup (legal per §4, flagged in `warnings`).
- Brute-force oracle cannot run on the full official catalog (combinatorial); official optimality is
  covered by decomposition proof + single-swap test.

## Phase 4 dependencies

- Sandeep: `POST /api/packages/build` calling `create_domain(...).build(request, trip_id)` in a worker
  thread, persisting the BUILT trip; map `InvalidRequestError` → 422, `RuntimeError` → 500.
- Tanish: run the oracle/validator on the persisted + exported trip (`GET .../export`) to prove
  `trip_id` and prices survive the round trip.
