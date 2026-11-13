# Handoff — Tanish (Developer A, domain)

## Phase 7 — final domain verification (latest)

```text
Developer: Tanish (A)
Phase: 7 — complete. No discrepancies found; no production changes (domain frozen since e9c61c5 / 49f3bc6 code).
Remote: origin/integration = 70012ee (app/domain + contract identical to this branch); origin/sandeep = 7d3f4d4
Added: tests/domain_fixtures/judge_oracle.py  independent §4 rules + prices from RAW JSON (no app.* imports)
       tests/domain/test_phase7_final.py      34 tests (sweep, brute force, alternatives, boundaries, oracle self-tests)
feat/tanish-domain: 459 passed, 3 skipped (API modules BLOCKED: no app.main on branch)
integration 70012ee + all domain tests, disposable PostgreSQL 127.0.0.1:55435: 783 passed, 0 failed, 2 skipped
  (both skips are Sandeep's by-design placeholders)
```

Evidence:
- SY-17 sweep: 4 homes × 2 countries × 5 start dates × nights {3,4,6,10} × 3 parties (incl. infant age 0,
  child 17, 4A+3C) → every BUILT trip passes the independent judge; `NO_OUTBOUND_FLIGHT` re-derived from raw flights.
- M8 on official data: independent exhaustive 3-night brute force (own pickup scans, every room count,
  every attraction pair, branch-and-bound) = planner cheapest on 5 requests (INR 227,584 / 134,952 /
  77,104 / 293,714 / 140,952) and both say no package on the 6th; budget = min → BUILT, min−1 → OVER_BUDGET (shortfall 1).
- Every listed alternative (hotel per hotel item, both flights, attractions on every trip day, limit 50) on
  5 official trips incl. 2-city Japan plans: `change()` succeeds, diff == listed `price_diff_inr`, judge passes.
- Rejected edits (duplicate, Nara, closed ATT-TYO-006, missing item, wrong-date flight) leave the trip untouched.
- Boundaries: first night 04-01, return 04-20, 10 nights, 03-31 / 04-21 returns → `OUTSIDE_DATA_WINDOW`;
  15-traveler party respects seats/rooms. Judge oracle self-tests prove it detects 6 defect classes.

Remaining risks: the brute force covers single-city 3-night trips on official data (multi-city exactness is
covered by the Phase 3 random-catalog oracle); the judges' own tool may interpret display-only items
(e.g. long airport waits, warned) differently; G1–G4/S1–S3 not implemented (await approval).

---

## Phase 6 — booked export & confirm validation

```text
Developer: Tanish (A)
Phase: 6 — complete. No production code changes needed (no domain defects found); domain frozen except P0.
Remote state: origin/integration = 70012ee (contains domain 49f3bc6, identical app/domain + contract);
              origin/sandeep = 1aaabd1 (his Phase 7)
Added: tests/domain_fixtures/booked_oracle.py  independent BOOKED oracle (no app.contracts code)
       tests/domain/test_phase6_booked.py      BK-14, SY-11, tampering, validate(mode="confirm")
       tests/domain/test_phase6_api_confirm.py real confirm/reservation/export on the platform
On feat/tanish-domain: 425 passed, 3 skipped (the 3 API modules BLOCKED: no app.main on this branch)
On integration 70012ee + these tests, disposable PostgreSQL 127.0.0.1:55434:
  Phase 6 modules 23 passed; full suite 749 passed, 0 failed, 2 skipped (Sandeep's by-design placeholders)
PASS IDs: BK-14, SY-11 (+ BK-10, BK-11, snapshot immutability/restart, CONFIRM_REJECTED on tampered draft)
```

Verified: every booked item `BOOKED` with `booking == {"reference": ref}` (10 ASCII digits, string, leading
zeros kept); top-level-only `BOOKED` fails both oracles; snapshot == confirmed draft + stamp only; PATCH after
confirm → 409 `ALREADY_BOOKED`; retries (same/different key) return the same reference; one reservation row;
lookup/export identical after restart; confirm re-validates the LATEST draft (edited → booked as v2; rejected
edit → last valid draft; DB-tampered price → 422 `CONFIRM_REJECTED`, no reservation, still DRAFT).

---

## Phase 5 — edits and alternatives

```text
Developer: Tanish (A)
Phase: 5 — domain complete on feat/tanish-domain; shared integration still PENDING (integration owner)
Remote state checked: origin/sandeep = 8b8df4d (his Phase 3 PATCH/confirm + Phase 4 checkpoint, includes my
  b3b37be merged into HIS branch); origin/integration = c49a9cd (unchanged). No Tanish files touched upstream.
Implemented: app/domain/edits.py  apply_change() + list_alternatives() (real; skeleton removed)
             app/domain/planner.py arrival_wait_warnings()/departure_wait_warnings() shared by planner + edits
Tests on feat/tanish-domain: python -m pytest -q -> 408 passed, 2 skipped (both API modules BLOCKED: no app.main)
PASS IDs: ED-01..ED-08, PR-16 (+ AT-15 remove-to-zero, max 2/day, over-budget edit, stable codes)
Integration candidate (scratch, NOT merged: 8b8df4d + this domain; disposable PostgreSQL 127.0.0.1:55433):
  tests/domain/test_phase5_api_edits.py + test_phase4_api_roundtrip.py + contract -> 58 passed
  tests/platform -> 316 passed, 1 skipped, 1 FAILED = test_phase4_integration.py::
    test_real_edits_report_not_available_until_phase5 (asserts 503 "not implemented"; obsolete now) -> Sandeep
```

**change():** validate change shape (`UNKNOWN_OP`, `CHANGE_MALFORMED`) → deep copy → op handler
(`ITEM_NOT_FOUND`, `ITEM_WRONG_TYPE`, `ITEM_NOT_REMOVABLE`, `NO_CHANGE`, `UNKNOWN_CATALOG_ID`,
`ROOM_HOTEL_MISMATCH`, `HOTEL_WRONG_CITY`, `ROOM_CAPACITY`) → dependent airport transfers → order items →
full catalog repricing → `validate_trip(…, "modified")`; first passing candidate wins, otherwise the
first candidate's violations. Input never mutated; `TRIP_BOOKED` / `TRIP_MALFORMED` rejected, never raised.
- Room count for `replace_hotel` = minimal fitting k (cheapest, least stock).
- Transfer policy: hotel change keeps the current transfer (and time) if still valid; flight change re-times
  the current transfer to the most convenient legal pickup (arrival: earliest ≥ landing+60; departure:
  latest with airport ≥ 180 min early); then other transfers cheapest first. Each change is described in
  `adjustments`. No legal transfer → `NO_AIRPORT_TRANSFER` plus the root-cause violations (e.g.
  `FLIGHT_WRONG_AIRPORT`). Intercity transfers do not depend on hotels/flights and are untouched.
- `add_attraction` without `start_time`: earliest legal, non-overlapping start from 09:00, else earliest legal
  at all (avoids 00:00 visits for 24 h sites; never changes feasibility). Attractions are never moved or
  removed implicitly; conflicts reject (e.g. ED-04 `ATTRACTION_TIMELINE`).

**alternatives():** every candidate is simulated through `change()`, so only accepted options are listed,
sorted by `new_total_inr` (ties by IDs), `price_diff_inr = new_total - trip.total` exact incl. transfer
re-selection; attraction alternatives carry `start_time`; return-day/outside dates → `[]`; malformed
filters / unknown hotel item / malformed trip → `InvalidRequestError`; `limit` default 20.

**Pickup policy (as approved):** cheapest stays the default; next-day pickups and waits ≥ 3 h beyond the
60/180-minute buffers get a visible warning; no max-wait rule. Convenience is offered via flight/hotel
alternatives (Menon: FL-401 same-day arrival, +INR 3,780, removes the overnight wait). A dedicated
"transfer" alternative is NOT possible under the frozen contract (no component/op) — contract proposal if wanted.

**For Sandeep:** (1) retire/flip `test_real_edits_report_not_available_until_phase5`; (2) merge into
`integration` when ready and run `tests/domain/test_phase4_api_roundtrip.py` + `test_phase5_api_edits.py` there.

---

## Phase 4 — integration checkpoint

```text
Developer: Tanish (A)
Phase: 4 — domain side verified against Sandeep's platform; shared-branch merge PENDING (integration owner)
Branches: origin/integration = c49a9cd (contract freeze only, no API yet)
          origin/sandeep     = 4cedb49 (platform Phases 1-2: build/get/export, PostgreSQL)
          feat/tanish-domain = b3b37be + this Phase 4 commit
Integration candidate (scratch, NOT merged): git archive 4cedb49 + app/domain & tests from b3b37be;
  `git merge-tree origin/sandeep feat/tanish-domain` -> clean, no conflicts.
Tests actually run on the candidate (disposable PostgreSQL 17 on 127.0.0.1:55432, bookingDB_test):
  full suite (platform + domain)                     -> 488 passed, 0 failed, 1 skipped*
  tests/domain/test_phase4_api_roundtrip.py (new)     -> 21 passed
  real uvicorn HTTP smoke (bookingDB, 3 official builds + OVER_BUDGET/NO_PACKAGE/422) -> all as expected
  *skip = Sandeep's test_phase1_readiness placeholder "app.domain is present; covered by integration tests"
On feat/tanish-domain alone: 369 passed, 1 skipped (round-trip module BLOCKED: app.main not on branch)
Domain-side changes needed for compatibility: none
Next action: Sandeep merges sandeep + feat/tanish-domain into integration; rerun
  `python -m pytest -q tests/domain/test_phase4_api_roundtrip.py` there with TEST_DATABASE_URL set.
```

**Verified end to end (candidate):** `POST /api/packages/build` → 201 DRAFT v1 with `ETag "1"` →
PostgreSQL row (`id == trip_id == package_id`, JSONB trip/request lossless, `total_inr`/`budget_inr`
denormalized) → `GET` (also from a fresh app instance = restart) → `/export` (draft shape,
identical to the stored trip apart from top-level `status`). Each export re-validated by
`validate_trip(…, "initial") == []` and repriced from the catalog (item quantity/price and totals
equal). Japan (BLR, Menon, INR 215,544) and UAE (BOM INR 128,426; DEL INR 52,037) builds 0.01–0.06 s.
`OVER_BUDGET` / `NO_PACKAGE` → 200, nothing persisted; invalid input and client `total_inr` → 422
`VALIDATION_ERROR`, nothing persisted; unknown/malformed id → 404; catalog SHA-256 unchanged.

**Overnight airport pickup (Menon):** FL-405 lands NRT 21:30 → earliest legal pickup 22:30. The
Shinagawa bus (06:30–22:00, INR 4,480 for 2A+1C) is closed then, so the cheapest package uses the
06:30 next-morning bus; the taxi (06:00–23:00, INR 15,120) could leave at 22:30 for INR 10,640 more.
Both satisfy §4 (≥ 60 min after landing, inside service hours; no maximum-wait rule); the item carries
a warning and the warning survives persistence/export. 7 of 28 sampled official builds contain such a
pickup. **Open product decision (owner: Tanish/user):** keep "cheapest" for BUILT, or prefer a
same-day pickup when it fits the budget (contract allows BUILT total ≥ `cheapest_total_inr`).

**For Sandeep:** (1) merge both branches into `integration` (clean); (2) the readiness placeholder skip
can point at `tests/domain/test_phase4_api_roundtrip.py`; (3) still open: commit `TEAM_RULES.md` /
personal docs, untrack `docker/data/db/**`; (4) minor: starlette TestClient/httpx deprecation warning.

---

## Phase 3 summary

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
