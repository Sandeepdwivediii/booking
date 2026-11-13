# Build My Holiday

> A deterministic, catalog-driven holiday package planner for building, comparing, editing, rescuing, and confirming complete trips.

`Python 3.11+` · `FastAPI` · `PostgreSQL` · `SQLAlchemy` · `Pydantic` · `Vanilla JavaScript` · `Docker Compose`

Build My Holiday creates complete travel packages from a fixed catalog of flights, hotels, rooms, transfers, and attractions. It evaluates valid city plans, checks inventory and timing rules, calculates all prices in INR, and persists only packages that pass the domain contract.

The application includes a responsive browser UI, a versioned REST API, transactional editing, budget rescue suggestions, idempotent booking, immutable reservation snapshots, and JSON export. Planning is rule-based and deterministic; no AI model runs inside the application.

## Highlights

- Builds the cheapest complete package that satisfies the request and catalog rules.
- Compares `cheapest`, `fastest`, and `best_rated` recommendations before saving a draft.
- Produces a chronological itinerary covering flights, stays, transfers, and attractions.
- Supports hotel and flight replacement, attraction changes, and complete city-plan rebuilds.
- Finds validated lower-cost variants with Trip Rescue while preserving selected attractions.
- Revalidates and reprices every server-side change; client-supplied prices are never trusted.
- Prevents lost updates with ETags, row locks, optimistic versions, and idempotency keys.
- Confirms a draft into one immutable reservation with a 10-digit reference.
- Exposes interactive OpenAPI documentation and a plain HTML/CSS/JavaScript frontend.
- Includes domain, API, PostgreSQL concurrency, integration, and smoke tests.

## Application workflow

```mermaid
flowchart LR
    A[Traveler enters trip details] --> B[Validate request against catalog]
    B --> C{Planning mode}
    C -->|Build| D[Find cheapest valid package]
    C -->|Compare| E[Generate cheapest, fastest and best-rated]
    E --> F[Traveler selects one option]
    D --> G{Package result}
    F --> G
    G -->|No feasible plan| H[Return NO_PACKAGE with reason]
    G -->|Above budget| I[Return OVER_BUDGET with cheapest total]
    G -->|Valid and affordable| J[Save versioned DRAFT]
    J --> K[Review itinerary and alternatives]
    K --> L[Edit components, change city plan or apply rescue]
    L --> M[Lock, reprice and revalidate]
    M -->|Rejected| K
    M -->|Accepted| N[Increment draft version]
    N --> K
    K --> O[Confirm package]
    O --> P[Revalidate under database lock]
    P --> Q[Create immutable reservation snapshot]
    Q --> R[Lookup or export booked trip]
```

The main lifecycle is:

1. **Discover options** — the UI loads supported departure cities, countries, night limits, and the catalog date window.
2. **Plan** — the domain enumerates legal directed city plans and searches for compatible flights, rooms, transfers, and attractions.
3. **Compare or build** — the traveler can preview three recommendation strategies or directly build the cheapest package.
4. **Persist a draft** — only a valid, within-budget package is saved. `NO_PACKAGE` and `OVER_BUDGET` results do not create database rows.
5. **Customize** — edits and rescue proposals are recalculated against the current catalog and saved atomically when valid.
6. **Confirm** — the latest draft is revalidated, booked once, and frozen as an immutable reservation snapshot.
7. **Retrieve** — the package can be reloaded, looked up by reservation reference, or exported in the shared trip JSON format.

## Architecture

```mermaid
flowchart TB
    UI[Static web client<br/>app/static] --> API[FastAPI routes<br/>app/api]
    API --> SVC[Application services<br/>app/services]
    SVC --> DOMAIN[Pure planning domain<br/>app/domain]
    DOMAIN --> CATALOG[Read-only JSON catalog<br/>data/catalog]
    SVC --> DB[(PostgreSQL)]
    API --> CONTRACT[Contracts and schemas<br/>app/contracts.py · app/schemas.py]
    DOMAIN --> CONTRACT
    SVC --> CONTRACT
```

| Layer | Responsibility |
|---|---|
| `app/static/` | Browser UI, form handling, recommendation cards, itinerary display, editing, rescue, confirmation, and lookup. |
| `app/api/` | HTTP routing, response envelopes, headers, request-size protection, and error translation. |
| `app/services/` | Use-case orchestration, persistence, transactions, idempotency, exports, recommendations, and rescue previews. |
| `app/domain/` | Catalog loading, exact planning, pricing, validation, alternatives, edits, city-plan rebuilds, and rescue search. |
| `app/contracts.py` | Stable platform/domain interface and executable response-shape checks. |
| `app/models.py` | Three PostgreSQL models for packages, reservations, and idempotency records. |
| `data/catalog/` | Eight read-only source files used by the planner in memory. The catalog is not copied into SQL. |

At startup, the application tries to connect to PostgreSQL, creates the additive schema when needed, and loads the catalog. Startup itself remains available if either dependency is missing, but `/api/health` returns `503 not_ready` and package operations remain unavailable until both recover.

## Quick start with Docker

### Prerequisites

- Git
- Docker Desktop or Docker Engine with Compose

### 1. Clone and configure

```bash
git clone https://github.com/Sandeepdwivediii/booking.git
cd booking
cp .env.example .env
```

On PowerShell, use `Copy-Item .env.example .env` instead of `cp` if the alias is unavailable.

The current development Compose file creates PostgreSQL with the password `sandeep`. Set the following value in `.env` before starting the stack:

```dotenv
POSTGRES_PASSWORD=sandeep
```

For any shared or production-like environment, replace this development credential in both `.env` and `docker/docker-compose.yml`.

### 2. Start the stack

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

### 3. Open the services

| Service | URL |
|---|---|
| Web application | http://127.0.0.1:8000 |
| Health check | http://127.0.0.1:8000/api/health |
| Swagger UI | http://127.0.0.1:8000/api/docs |
| Adminer | http://127.0.0.1:8080 |

For Adminer, use server `db`, database `bookingDB`, user `booking`, and the password configured above.

Useful commands:

```bash
# Follow application logs
docker compose -f docker/docker-compose.yml logs -f app

# Check container status
docker compose -f docker/docker-compose.yml ps

# Stop the stack without deleting its local PostgreSQL files
docker compose -f docker/docker-compose.yml down
```

## Local development setup

Run PostgreSQL in Docker and the FastAPI application on the host when you want reload support and a shorter edit/run cycle.

### 1. Start PostgreSQL

```bash
docker compose -f docker/docker-compose.yml up -d db
```

Ensure `.env` contains `POSTGRES_PASSWORD=sandeep` for the current development Compose configuration.

### 2. Create a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 3. Run the API and UI

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. The FastAPI application serves both the REST API and the static frontend.

## Configuration

Settings are loaded from `.env`, but real process environment variables take precedence.

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | Complete SQLAlchemy PostgreSQL URL. Overrides the individual `POSTGRES_*` settings. | Built from the values below |
| `POSTGRES_HOST` | PostgreSQL host for a locally run application. | `127.0.0.1` |
| `POSTGRES_USER` | PostgreSQL user. | `booking` |
| `POSTGRES_PASSWORD` | PostgreSQL password. | Empty; must match the database |
| `POSTGRES_DB` | Application database name. | `bookingDB` |
| `DB_PORT` | PostgreSQL host port. | `5432` |
| `TEST_DATABASE_URL` | Disposable database used by the test suite. | Same server, database `bookingDB_test` |
| `CATALOG_DIR` | Directory containing the eight catalog JSON files. | `data/catalog` |
| `APP_PORT` | Host port published by Docker Compose. | `8000` |
| `APP_DB` | Database name passed to the app container by Compose. | `bookingDB` |

Example explicit connection URL:

```dotenv
DATABASE_URL=postgresql+psycopg://booking:sandeep@127.0.0.1:5432/bookingDB
```

## API overview

All JSON endpoints use the `/api` prefix.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/health` | Report database and catalog readiness. |
| `GET` | `/api/catalog/options` | Return supported departure cities, countries, and catalog rules. |
| `POST` | `/api/packages/build` | Build and save the cheapest affordable package. |
| `POST` | `/api/packages/recommend` | Preview cheapest, fastest, and best-rated packages without saving. |
| `POST` | `/api/packages/recommend/select` | Recompute and save one selected recommendation. |
| `GET` | `/api/packages/{package_id}` | Load the current draft or booked package. |
| `GET` | `/api/packages/{package_id}/export` | Download the package in the shared trip JSON format. |
| `GET` | `/api/packages/{package_id}/alternatives` | List valid hotel, flight, or attraction alternatives with price differences. |
| `GET` | `/api/packages/{package_id}/city-plans` | List legal alternative city plans. |
| `POST` | `/api/packages/{package_id}/rescue` | Preview up to three validated budget-rescue proposals. |
| `PATCH` | `/api/packages/{package_id}` | Apply an edit, city-plan change, or selected rescue proposal. |
| `POST` | `/api/packages/{package_id}/confirm` | Confirm the latest valid draft exactly once. |
| `GET` | `/api/reservations/{reference}` | Retrieve an immutable reservation by its 10-digit reference. |

### Request headers and concurrency

| Header | Used by | Meaning |
|---|---|---|
| `If-Match` | Package `PATCH` | Expected draft version, normally copied from the package `ETag`. A stale value returns `409 VERSION_CONFLICT`. |
| `Idempotency-Key` | Recommendation selection, package `PATCH`, and confirmation | Makes retries safe. Reusing a key with a different payload is rejected. |
| `ETag` | Build, get, select, and successful edit responses | Current package version, for example `"2"`. |

Errors follow one envelope:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid request.",
    "details": []
  }
}
```

### Build example

```bash
curl --request POST http://127.0.0.1:8000/api/packages/build \
  --header "Content-Type: application/json" \
  --data '{
    "departure_city": "BLR",
    "destination_country": "Japan",
    "start_date": "2027-04-10",
    "nights": 6,
    "adults": 2,
    "children_ages": [6],
    "budget_inr": 350000
  }'
```

A successful build returns `201 Created`, `Location`, `ETag: "1"`, a package UUID, and the saved draft. An affordable plan is persisted as `DRAFT`; `OVER_BUDGET` and `NO_PACKAGE` return `200 OK` with an explanation and persist nothing.

## Planning and catalog rules

The planner treats the catalog as the source of truth and never mutates it.

- City links are directed, and every city segment must contain at least two consecutive nights.
- The requested night count must fit the catalog window and the supported minimum/maximum limits.
- Arrival and return airports must serve the first and last destination cities.
- Flight seats, room capacity, nightly room availability, transfer service windows, and attraction opening hours are enforced.
- Flights, hotels, transfers, and attractions are repriced from catalog records after every change.
- The build endpoint chooses the lowest total across all complete valid city plans.
- The fastest recommendation uses flight and intercity-transfer durations.
- The best-rated recommendation uses hotel star-nights, not guest review scores.
- A package above budget is not saved; the response reports the exact cheapest total and shortfall.

The catalog currently covers India departures, Japan and the United Arab Emirates, and travel dates in April 2027. See the [data dictionary](docs/DATA_DICTIONARY.md) for the complete field and pricing rules.

## Persistence and consistency

Only application state is stored in PostgreSQL:

| Table | Purpose |
|---|---|
| `app_packages` | Mutable draft JSON, original request, total, budget, status, and optimistic version. |
| `app_reservations` | One immutable booked snapshot per package, keyed by a 10-digit text reference. |
| `app_idempotency_records` | Stored responses for safe edit and confirmation retries. |

Important guarantees:

- Draft edits execute in short transactions with `SELECT ... FOR UPDATE` row locking.
- `If-Match` prevents a stale browser tab from overwriting a newer draft.
- Confirmation revalidates the latest committed draft while holding the package lock.
- Duplicate or concurrent confirmations converge on one reservation.
- Database triggers prevent updates or deletes of reservation rows and booked packages.
- Reservation references are stored as text so leading zeroes are preserved.

## Project structure

```text
booking/
├── app/
│   ├── api/                  # FastAPI routes and error handling
│   ├── domain/               # Planner, pricing, rules, validation, edits, rescue
│   ├── services/             # Transactions and application use cases
│   ├── static/               # Browser UI
│   ├── config.py             # Environment-based settings
│   ├── contracts.py          # DomainPort and executable contracts
│   ├── db.py                 # Engine, sessions, schema, database triggers
│   ├── main.py               # Application factory and static-file hosting
│   ├── models.py             # SQLAlchemy models
│   └── schemas.py            # Strict Pydantic request models
├── data/
│   ├── catalog/              # Read-only planning catalog
│   └── trip_format_example.json
├── docker/
│   └── docker-compose.yml    # App, PostgreSQL, and Adminer
├── docs/                     # Contracts, evidence, data dictionary, problem brief
├── tests/
│   ├── domain/               # Pure domain and independent-oracle tests
│   ├── domain_fixtures/      # Synthetic catalogs and judge oracles
│   └── platform/             # API, DB, transaction, integration, and smoke tests
├── Dockerfile
├── pytest.ini
└── requirements.txt
```

## Testing

Start PostgreSQL, activate the virtual environment, and run:

```bash
python -m pytest -q
```

The suite uses `bookingDB_test` by default and may create it when the configured PostgreSQL user has permission. Safety checks refuse to run destructive fixtures against the application database or any database whose name does not contain `test`.

Useful focused commands:

```bash
# Pure planning and validation behavior
python -m pytest -q tests/domain

# API, PostgreSQL, concurrency, and integration behavior
python -m pytest -q tests/platform

# Generate the implementation evidence report
python tests/platform/smoke/evidence_report.py
```

The platform suite covers real PostgreSQL transactions, concurrent edits and confirmations, restart persistence, real-catalog integration, idempotent retries, database immutability, and browser/Docker smoke paths.

## Development workflow

1. Create and activate `.venv`, then install the pinned dependencies.
2. Start the local PostgreSQL service with Docker Compose.
3. Run the app with Uvicorn reload and verify `/api/health` reports `ready`.
4. Keep catalog-independent business rules in `app/domain/`; keep HTTP and persistence concerns in `app/api/` and `app/services/`.
5. Update `app/contracts.py` and the integration contract when changing the platform/domain boundary.
6. Add focused domain tests first, followed by API or transaction tests for behavior that crosses a boundary.
7. Run the focused test module, then the complete suite before opening a pull request.
8. Check `git diff`, confirm that `.env` and local database files are not included, and document any API or workflow change.

## Additional documentation

- [Startup guide](STARTUP.md) — concise local setup instructions.


## Current scope and limitations

- This is a planning and booking simulation: it does not process payments or call real airline, hotel, or transfer supplier APIs.
- Confirmation freezes the reservation snapshot but does not decrement catalog seat or room inventory.
- Authentication and authorization are not implemented.
- Reservation lookup has no rate limiting; deploy it behind an appropriate gateway before public exposure.
- The bundled catalog has a fixed set of destinations and dates rather than live inventory.
- The bundled Compose file is intended for local development and must be hardened before deployment.
- Schema creation is additive through SQLAlchemy; the project does not currently include a versioned migration system such as Alembic.
