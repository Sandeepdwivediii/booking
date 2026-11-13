# Integration Contract — Build My Holiday (v1.0.0, DRAFT for review)

Executable source of truth: `app/contracts.py` (stdlib only). This doc explains it.
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
`{departure_cities: [{airport, city}], countries: [{name, code, timezone, cities: [str]}], rules: {min_nights, max_nights, min_nights_per_city, first_night, last_departure}}` — straight from `destinations.json`. The API uses it for the form and for request validation.

### build(request, trip_id) → BuildResult
BuildRequest (exactly 7 keys, already validated by the API):

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
| `BUILT` | full Trip, `total_inr <= budget_inr` | None or int | None | None | optional | optional |
| `OVER_BUDGET` | None | exact global cheapest complete feasible total (> budget) | `cheapest - budget` (≥ 1) | None | text | **required** |
| `NO_PACKAGE` | None | None | None | **required** UPPER_SNAKE | **required** text | optional |

- `BUILT`: trip passes `validate(trip, "initial") == []`, `trip.trip_id == trip_id`, all items `PLANNED`, `booking: null`, prices from catalog. Never a fake/partial BUILT.
- `OVER_BUDGET` only when ≥ 1 complete legal package exists ignoring budget; cheapest is exact (no top-K/greedy). Budget equality is `BUILT` (PR-08). `budget_inr=0` with a feasible package → `OVER_BUDGET` (IN-13).
- `NO_PACKAGE` reason codes (domain may add more): `OUTSIDE_DATA_WINDOW, NO_CITY_PLAN, NO_OUTBOUND_FLIGHT, NO_RETURN_FLIGHT, NO_HOTEL_AVAILABILITY, NO_AIRPORT_TRANSFER, NO_INTERCITY_TRANSFER, NO_ATTRACTION_SCHEDULE, NO_FEASIBLE_COMBINATION`.
- Monotonic: raising the budget never turns `BUILT` into `OVER_BUDGET` (PR-15).
- build never raises for infeasibility. Target runtime ≤ 10 s; API runs it in a worker thread.

### Trip (judge export format + allowed extras)

```
trip_id, traveler{name|null, home_airport, adults, children, child_ages},
country, start_date, end_date (= start_date + nights), budget_inr, total_inr (= sum items.price_inr), items[]
```
Item: `item_id` (unique in trip), `type` (`flight|hotel|transfer|attraction`), `role` (extra, below), `name`, `city` (flight: arrival city), `status`, `catalog_id`, `room_id` (hotel only, else null), `start`, `end` (local `YYYY-MM-DD` for hotels, `YYYY-MM-DDTHH:MM` otherwise; transfer `end` = arrival time), `quantity` (seats/rooms/vehicles/tickets, int ≥ 1), `price_inr` (int ≥ 0), `booking` (null in drafts), `sources: []`, `warnings: [str]`.

`role` ∈ `outbound_flight, return_flight, hotel_stay, arrival_transfer, departure_transfer, intercity_transfer, city_taxi, attraction_visit`. Items are ordered chronologically (by local time at the event's place; domain decides order across time zones). `item_id`s may be renumbered after a change; clients only use ids from the version they hold (enforced by `If-Match`).

Platform-only fields: top-level `status` (`DRAFT`/`BOOKED`) and per-item `status="BOOKED"` + `booking.reference` are stamped by the platform at confirm/export. Domain trips are always drafts.

### validate(trip, mode) → [Violation]
`Violation = {code: UPPER_SNAKE, message: str, item_id: str|null}`; `[]` = valid. Full re-check against the catalog: every Section-4 rule **and** prices recomputed (tampered `price_inr`/`total_inr` → violation) **and** `total_inr <= budget_inr`.

| mode | used by | difference |
|---|---|---|
| `initial` | build self-check | adds 1–2 attractions on every full day |
| `modified` | after change | all hard rules; full days may have 0 attractions (AT-15) |
| `confirm` | confirm transaction | same as `modified`, run on the latest stored draft |

Unknown mode → `InvalidRequestError`. Never raises for rule failures; malformed trips return violations.

### alternatives(trip, component, filters) → [Alternative]

| component | required filters | meaning |
|---|---|---|
| `hotel` | `item_id` (hotel item) | other hotel+room for that city segment |
| `outbound_flight` / `return_flight` | — | other flights for that leg |
| `attraction` | `date` (`YYYY-MM-DD`) | attractions addable that day |

Optional `limit` (default 20). Unknown component / missing required filter → `InvalidRequestError`.
`Alternative = {component, change (ready-to-send ChangeRequest), name, catalog_id, room_id|null, start|null, end|null, price_inr, new_total_inr|null, price_diff_inr|null}`, cheapest first. Should list only options for which `change()` would succeed; `change()` stays the authority.

### change(trip, change) → ChangeResult

```
{"op": "replace_hotel",     "item_id", "hotel_id", "room_id"}
{"op": "replace_flight",    "item_id", "flight_id"}
{"op": "add_attraction",    "attraction_id", "date", "start_time"?}   # HH:MM; absent → earliest legal
{"op": "remove_attraction", "item_id"}
```
`ChangeResult = {ok: bool, trip: Trip|null, violations: [Violation], adjustments: [str]}`.
- All-or-nothing: clone → apply → re-read catalog → reprice → `validate(…, "modified")`. `ok=true` ⇒ new trip, same `trip_id`, no violations, `total_inr <= budget_inr`.
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

- Contract (platform): `python -m pytest -q tests/platform/test_contract.py` — shape checkers, frozen signatures, stdlib purity, TEST-ONLY `FakeDomain` in `tests/platform/fixtures/contract_fixtures.py`. A skipped test activates automatically once `app.domain.adapter.create_domain` exists and checks the real adapter against the port.
- Domain lane can reuse `app.contracts.build_result_shape_errors / trip_shape_errors / change_result_shape_errors / violation_shape_errors` in `tests/domain/**`.
