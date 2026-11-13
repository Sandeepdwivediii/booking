# Integration Contract — Build My Holiday (v1.1.0, DRAFT for final review)

Executable source of truth: `app/contracts.py` (stdlib only, **Python 3.11+** for `typing.NotRequired`; Docker images must use `python:3.11` or newer). This doc explains it.
v1.1.0 = v1.0.0 (`fd4f331`) + fixes from Tanish's Phase 0 review (see §6).
Owners: Sandeep (platform, publishes) · Tanish (domain, reviews/approves). Any change after
freeze = proposal here → both approve → one small integration commit.

## 1. Boundary

- Domain (`app/domain/**`, Tanish): pure Python. Loads the 8 `data/catalog/*.json` files into memory, plans, validates, prices, lists alternatives, applies changes. No FastAPI, SQLAlchemy, DB, HTTP or file writes.
- Platform (Sandeep): HTTP, Pydantic request validation, PostgreSQL (`app_packages`, `app_reservations`, `app_idempotency_records`), versioning, idempotency, confirmation, export, UI.
- Factory: `app.domain.adapter.create_domain(catalog_dir: str) -> DomainPort`. Called once at startup with `CATALOG_DIR` (default `data/catalog`). Raises `CatalogUnavailableError` if files are missing/corrupt → API stays non-ready (`/api/health` 503).
- All methods: never mutate inputs; return fresh JSON-serializable data (str/int/bool/None/list/dict; **integer INR**, no floats, no datetime objects); thread-safe for concurrent reads; deterministic for the same catalog + input.

## 2. `DomainPort`

```python
class DomainPort(Protocol):
    def options(self) -> dict: ...                                              # Options
    def build(self, request: dict, trip_id: str) -> dict: ...                   # BuildRequest -> BuildResult
    def validate(self, trip: dict, mode: str) -> list[dict]: ...                # -> [Violation]; [] = valid
    def alternatives(self, trip: dict, component: str, filters: dict) -> list[dict]: ...  # -> [Alternative]
    def change(self, trip: dict, change: dict) -> dict: ...                     # ChangeRequest -> ChangeResult
```

### options() → Options
`{departure_cities: [{airport, city}], countries: [{name, code, timezone, cities: [str]}], rules: {min_nights, max_nights, min_nights_per_city, first_night, last_departure}}` — a structure **derived** from `destinations.json`, not the raw JSON: `countries[].cities` is reduced to city names, `links` are omitted, and `rules.data_window.{first_night,last_departure}` is flattened into `rules`. The API uses it for the form and for request validation.

### build(request, trip_id) → BuildResult
BuildRequest (exactly 7 keys, already validated by the API). Missing/extra keys or wrong types (`app.contracts.build_request_errors(request)` non-empty; `bool` is not an int) → `InvalidRequestError`. A well-typed request whose dates fall outside the catalog data window → `NO_PACKAGE` with `OUTSIDE_DATA_WINDOW` (the API normally rejects these first with 422).

| key | type | API guarantees |
|---|---|---|
| `departure_city` | str | airport code in `options.departure_cities[].airport` (e.g. `BLR`) |
| `destination_country` | str | name in `options.countries[].name` (e.g. `Japan`) |
| `start_date` | `YYYY-MM-DD` | arrival day (local); `>= first_night`; `start_date + nights <= last_departure` |
| `nights` | int | `min_nights..max_nights` (3–10) |
| `adults` | int | 1..20 |
| `children_ages` | list[int] | each 0..17, length ≤ 20 |
| `budget_inr` | int | 0..100,000,000 |

BuildResult (all keys always present):

| status | trip | cheapest_total_inr | shortfall_inr | reason_code | reason | suggestion |
|---|---|---|---|---|---|---|
| `BUILT` | full Trip, `total_inr <= budget_inr` | None, or int ≤ `trip.total_inr` | None | None | optional | optional |
| `OVER_BUDGET` | None | int > budget | `cheapest - budget` (≥ 1) | **None** | **required** non-empty | **required** |
| `NO_PACKAGE` | None | None | None | **required** UPPER_SNAKE | **required** non-empty | optional |

- `cheapest_total_inr`, whenever it is an int, is the **exact global cheapest complete feasible package total** for the request (ignoring budget). For `BUILT` it may be None (not computed); if present it is ≤ `trip.total_inr`.
- `BUILT`: trip passes `validate(trip, "initial") == []`, `trip.trip_id == trip_id`, `trip.end_date == start_date + nights`, request fields echoed (`budget_inr`, `start_date`, `country`, traveler), all items `PLANNED`, `booking: null`, prices from catalog. Never a fake/partial BUILT.
- `OVER_BUDGET` only when ≥ 1 complete legal package exists ignoring budget; cheapest is exact (no top-K/greedy). Budget equality is `BUILT` (PR-08). `budget_inr=0` with a feasible package → `OVER_BUDGET` (IN-13).
- `NO_PACKAGE` reason codes (domain may add more): `OUTSIDE_DATA_WINDOW, NO_CITY_PLAN, NO_OUTBOUND_FLIGHT, NO_RETURN_FLIGHT, NO_HOTEL_AVAILABILITY, NO_AIRPORT_TRANSFER, NO_INTERCITY_TRANSFER, NO_ATTRACTION_SCHEDULE, NO_FEASIBLE_COMBINATION`.
- Monotonic: raising the budget never turns `BUILT` into `OVER_BUDGET` (PR-15).
- build never raises for infeasibility. Target runtime ≤ 10 s; API runs it in a worker thread.

### Trip (judge export format + allowed extras)

```
trip_id, traveler{name|null, home_airport, adults, children, child_ages},
country, start_date, end_date (= start_date + nights), budget_inr, total_inr (= sum items.price_inr), items[]
```
Item: `item_id` (unique in trip), `type` (`flight|hotel|transfer|attraction`), `role` (extra, below), `name`, `city`, `status`, `catalog_id`, `room_id` (hotel only, else null), `start`, `end` (local `YYYY-MM-DD` for hotels, `YYYY-MM-DDTHH:MM` otherwise; transfer `end` = arrival time), `quantity` (int ≥ 1), `price_inr` (int ≥ 0), `booking` (null in drafts), `sources: []`, `warnings: [str]`.

`role` ∈ `outbound_flight, return_flight, hotel_stay, arrival_transfer, departure_transfer, intercity_transfer, city_taxi, attraction_visit`. Items are ordered chronologically (by local time at the event's place; domain decides order across time zones). `item_id`s may be renumbered after a change; clients only use ids from the version they hold (enforced by `If-Match`).

Item semantics:

| role | `city` | `quantity` |
|---|---|---|
| `outbound_flight` | arrival (first) city | travelers (adults + children) |
| `return_flight` | **home city** (e.g. `Bengaluru`) | travelers |
| `hotel_stay` | hotel city | rooms `k` |
| `arrival_transfer` / `departure_transfer` | **hotel city** | `PER_VEHICLE`: vehicles; `PER_PERSON`: travelers |
| `intercity_transfer` | **`to_city`** | `PER_VEHICLE`: vehicles; `PER_PERSON`: travelers |
| `city_taxi` | city served | vehicles |
| `attraction_visit` | attraction city | travelers |

Platform-only fields: top-level `status` (`DRAFT`/`BOOKED`) and per-item `status="BOOKED"` + `booking.reference` are stamped by the platform at confirm/export. Domain trips are always drafts.

Booked export / reservation snapshot (`booked_trip_shape_errors(trip, reference)`): top-level `status == "BOOKED"`; **every** item `status == "BOOKED"` and `booking == {"reference": reference}` (exactly that object); `reference` is a `str` that fully matches `[0-9]{10}` (leading zeros kept, never an int). Mixed PLANNED/BOOKED is invalid (SY-10, SY-11, BK-14).

### Attraction day rules (all modes unless stated)

Day kinds use local dates: **arrival day** = `start_date`; **city-change day** = check-in date of each hotel segment after the first; **return day** = `end_date`; every other day = **full day**.

| day | `initial` | `modified` / `confirm` |
|---|---|---|
| full day | 1–2 | 0–2 |
| arrival / city-change day | 0–2 (start after reaching the city) | 0–2 |
| return day | 0 | 0 |

Max 2 per day applies in every mode and to `add_attraction` (adding a 3rd on any day is rejected). Count rules are executable as `attraction_count_errors(trip, mode)`; city, opening days/hours, closures, overlap and uniqueness remain `validate()`'s job.

### validate(trip, mode) → [Violation]
`Violation = {code: UPPER_SNAKE, message: str, item_id: str|null}`; `[]` = valid. Full re-check against the catalog: every Section-4 rule **and** prices recomputed (tampered `price_inr`/`total_inr` → violation) **and** `total_inr <= budget_inr`.

| mode | used by | difference |
|---|---|---|
| `initial` | build self-check | all hard rules + ≥ 1 attraction on every full day |
| `modified` | after change | all hard rules (incl. max 2/day, 0 on return day); full days may have 0 (AT-15) |
| `confirm` | confirm transaction | same as `modified`, run on the latest stored draft |

Unknown mode → `InvalidRequestError`. Never raises for rule failures; malformed trips return violations.
`validate()` **ignores** top-level `status` and item `status`/`booking` metadata: it checks domain feasibility only, so a booked snapshot can be re-validated independently (booked metadata is checked by `booked_trip_shape_errors`).

### alternatives(trip, component, filters) → [Alternative]

| component | required filters | meaning |
|---|---|---|
| `hotel` | `item_id` (hotel item) | other hotel+room for that city segment, **including other room types of the same hotel** |
| `outbound_flight` / `return_flight` | — | other flights for that leg |
| `attraction` | `date` (`YYYY-MM-DD`) | attractions addable that day |

Optional `limit` (default 20). Unknown component / missing required filter / malformed date (not a real `YYYY-MM-DD`) → `InvalidRequestError`. A well-formed date on the return day or outside the trip → `[]`.
`Alternative = {component, change (ready-to-send ChangeRequest), name, catalog_id, room_id|null, start|null, end|null, price_inr, new_total_inr|null, price_diff_inr|null}`, cheapest first. `change.op` matches the component (`replace_hotel` / `replace_flight` / `add_attraction`); attraction alternatives **always include `start_time`**; hotel alternatives always include `room_id`. Should list only options for which `change()` would succeed; `change()` stays the authority. Executable: `alternatives_shape_errors(alts, component)`.

### change(trip, change) → ChangeResult

```
{"op": "replace_hotel",     "item_id", "hotel_id", "room_id"}
{"op": "replace_flight",    "item_id", "flight_id"}
{"op": "add_attraction",    "attraction_id", "date", "start_time"?}   # HH:MM; absent → earliest legal
{"op": "remove_attraction", "item_id"}
```
`ChangeResult = {ok: bool, trip: Trip|null, violations: [Violation], adjustments: [str]}`.
- All-or-nothing: clone → apply → re-read catalog → reprice → `validate(…, "modified")`. `ok=true` ⇒ new trip, no violations, `total_inr <= budget_inr`, and `trip_id`, `traveler`, `country`, `start_date`, `end_date`, `budget_inr` unchanged. Executable: `change_result_shape_errors(result, original_trip)`.
- `ok=false` ⇒ `trip=null`, ≥ 1 violation (unknown item/op/id, rule break, over budget). Never raises for user-level problems.
- Domain may re-select dependent **transfers** (airport/intercity) to keep the trip valid and must describe each in `adjustments` (G1). It must not silently remove or move attractions.

## 3. Errors

Exceptions (in `app.contracts`): `DomainError(message, details)` base · `CatalogUnavailableError` (`CATALOG_UNAVAILABLE` → 503) · `InvalidRequestError` (`INVALID_REQUEST` → 422). Anything else = bug → 500 `INTERNAL_ERROR`, no traceback in response.

API envelope: `{"error": {"code", "message", "details"}}`.

| code | HTTP | when |
|---|---|---|
| `VALIDATION_ERROR` | 422 | bad request body/params (Pydantic, IN-01..16) |
| `INVALID_CHANGE` | 422 | `change()` returned `ok=false` (details = violations) |
| `CONFIRM_REJECTED` | 422 | `validate(draft,"confirm")` not empty |
| `INVALID_REQUEST` | 422 | `InvalidRequestError` |
| `NOT_FOUND` | 404 | unknown package / reservation reference |
| `VERSION_CONFLICT` | 409 | `If-Match` ≠ current version |
| `ALREADY_BOOKED` | 409 | PATCH on a BOOKED package |
| `IDEMPOTENCY_CONFLICT` | 409 | same `Idempotency-Key`, different payload |
| `PRECONDITION_REQUIRED` | 428 | PATCH without `If-Match` |
| `CATALOG_UNAVAILABLE` / `SERVICE_UNAVAILABLE` | 503 | catalog or DB not ready |
| `INTERNAL_ERROR` | 500 | unexpected |

## 4. HTTP API (platform, for reference)

| method & path | notes |
|---|---|
| `GET /api/health` | 200 ready / 503 not ready (DB + catalog) |
| `GET /api/catalog/options` | `options()` |
| `POST /api/packages/build` | body = BuildRequest. API generates UUID `trip_id` once. `BUILT` → persist DRAFT v1, 201 `{package_id, version, status, trip}`. `OVER_BUDGET`/`NO_PACKAGE` → 200 with BuildResult, **nothing persisted** |
| `GET /api/packages/{id}` | stored package + `ETag: "<version>"` |
| `GET /api/packages/{id}/export` | judge JSON (draft or booked) |
| `GET /api/packages/{id}/alternatives?component=&item_id=&date=` | `alternatives()` |
| `PATCH /api/packages/{id}` | headers `If-Match`, `Idempotency-Key`; body = ChangeRequest; row lock → idempotency replay → version check → `change()` → save v+1 |
| `POST /api/packages/{id}/confirm` | `Idempotency-Key`; one transaction; 10-digit **string** reference; retries return same reference |
| `GET /api/reservations/{reference}` | immutable booked snapshot or 404 |

`trip_id` = `package_id` (UUID string). Client-sent prices/totals are ignored; totals always come from the domain.

## 5. Tests

- Contract (platform): `python -m pytest -q tests/platform` runs `tests/platform/test_contract.py` (shape checkers, frozen signatures, stdlib purity) and `tests/platform/test_contract_review.py` (regressions for §6). TEST-ONLY `FakeDomain` and synthetic data live in `tests/platform/fixtures/contract_fixtures.py`. A skipped test activates automatically once `app.domain.adapter.create_domain` exists and checks the real adapter against the port.
- Domain lane can reuse in `tests/domain/**`: `build_request_errors`, `build_result_shape_errors`, `trip_shape_errors`, `booked_trip_shape_errors`, `attraction_count_errors`, `change_result_shape_errors`, `alternatives_shape_errors`, `violation_shape_errors`.

## 6. Changelog

- **1.1.0** (Tanish review of `fd4f331`): attraction day rules for all modes; `booked_trip_shape_errors`; `change_result_shape_errors(result, original_trip)` checks fixed fields + budget; BUILT `end_date` and `cheapest_total_inr` invariants; OVER_BUDGET `reason_code` null + non-empty `reason`; `options()` documented as derived; item `city`/`quantity` semantics; alternatives (same-hotel rooms, `start_time`, `[]` vs `InvalidRequestError`); `build_request_errors` + `OUTSIDE_DATA_WINDOW`; Python 3.11+; contract tests committed. Reference/`HH:MM` checks use `fullmatch` with ASCII digits (rejects trailing newline and non-ASCII digits).
- **1.0.0**: initial draft.
