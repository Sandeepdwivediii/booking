# Build My Holiday

Plan, change and book a complete holiday (flights, hotels, airport and intercity transfers, attractions) from the
official hackathon catalog. FastAPI + PostgreSQL backend, plain HTML/CSS/JS frontend. No AI runs inside the app.

## Start (Docker, 2 commands)

```bash
cp .env.example .env        # set POSTGRES_PASSWORD to your database password (never commit .env)
docker compose -f docker/docker-compose.yml up -d --build
```

Open http://127.0.0.1:8000. Health check: http://127.0.0.1:8000/api/health (`ready` when the database and catalog are loaded).
Adminer (DB browser): http://127.0.0.1:8080.

Optional variables: `APP_PORT` (default 8000), `DB_PORT` (host port of PostgreSQL, default 5432),
`APP_DB` (database the app uses, default `bookingDB`).

## Start locally (without the app container)

```bash
docker compose -f docker/docker-compose.yml up -d db
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Requires Python 3.11+. Settings come from `.env` (see `.env.example`): `DATABASE_URL` or `POSTGRES_*`/`DB_PORT`,
`CATALOG_DIR` (default `data/catalog`).

## Catalog

The eight official files live in `data/catalog/` and are byte-identical to the supplied dataset (never modified;
`.gitattributes` disables line-ending conversion). They are loaded into memory at startup; there is no catalog SQL.
To use another copy, put the eight JSON files in a folder and set `CATALOG_DIR`. If files are missing or corrupt, the
app still starts but `/api/health` returns 503 with the reason and no package is built.

## Architecture

```
browser (app/static)  ->  FastAPI (app/api)  ->  services (app/services)  ->  PostgreSQL
                                                       |
                                                       v
                                          domain (app/domain, pure Python, in-memory catalog)
```

- `app/contracts.py`: frozen interface between platform and domain (`DomainPort`: `options`, `build`, `validate`,
  `alternatives`, `change`), plus executable shape checks. Details: `docs/INTEGRATION_CONTRACT.md`.
- `app/domain/`: catalog loader, exact planner, rule validator, pricing, edits. No database or HTTP code.
- `app/services/`: request validation, transactions (row lock, `If-Match` versions, idempotency keys,
  reservation numbers), judge JSON export.
- Database: only three tables. `app_packages` (drafts with version), `app_reservations` (immutable booked snapshot,
  10-digit TEXT reference, one per package), `app_idempotency_records` (stored responses for retries).
  Database triggers block any change to a reservation or a booked package.

## City rule

The app considers every city plan in the chosen country: distinct cities in an order allowed by the **directed**
city links, each city at least 2 consecutive nights, nights summing to the request. For each plan it finds the
cheapest legal flights, hotels, transfers and attractions, and returns the **cheapest complete package overall**.
If that package is over budget, the app says so and shows the exact cheapest total (M8). If no plan works, it says
why (`NO_PACKAGE` with a reason).

## Features

| Must-have | Status |
|---|---|
| M1 request form (7 fields, clear errors) | done |
| M2 build one package, or explain why none is possible | done |
| M3 every rule in section 4 holds | done (independently re-checked by test oracles) |
| M4 day-by-day plan in time order | done (local times; flight arrival shown with its own time zone) |
| M5 total, budget, amount left/over, JSON export | done |
| M6 change hotel or flight, add/remove attractions; whole package re-checked | done |
| M7 confirm -> 10-digit reservation number; summary after restart; "not found" | done |
| M8 budget too small -> cheapest total + suggestion | done |

Also: price difference shown for each change option (G2), warnings and automatic transfer adjustments after
changes (G1, partial).

## API

| Method and path | Purpose |
|---|---|
| `GET /api/health` | readiness (database + catalog) |
| `GET /api/catalog/options` | departure cities, countries, rules |
| `POST /api/packages/build` | build; `BUILT` -> saved draft (201), `OVER_BUDGET` / `NO_PACKAGE` -> 200, nothing saved |
| `GET /api/packages/{id}` | saved package (`ETag` = version) |
| `GET /api/packages/{id}/export` | judge JSON (draft or booked) |
| `GET /api/packages/{id}/alternatives` | options for hotel, flights, attractions |
| `PATCH /api/packages/{id}` | one change; needs `If-Match` and `Idempotency-Key` |
| `POST /api/packages/{id}/confirm` | book; needs `Idempotency-Key`; retries return the same reservation |
| `GET /api/reservations/{reference}` | reservation summary, or 404 |

Errors use `{"error": {"code", "message", "details"}}`.

## Tests

```bash
python -m pytest -q          # needs PostgreSQL; uses a separate database bookingDB_test (created if missing)
```

- `tests/domain/`: domain rules, planner (checked against a brute-force oracle), edits.
- `tests/platform/`: API, database, transactions, real-PostgreSQL concurrency, real-catalog integration,
  independent catalog and attraction oracles.
- `tests/platform/smoke/`: headless-Chrome walkthrough (`browser_smoke.py`), Docker race/restart rehearsal
  (`docker_rehearsal.py`), evidence generator (`evidence_report.py` -> `docs/EVIDENCE.md`).

## Known limitations

- No login, payments or real supplier booking (not required). Confirming does not reduce seats or rooms (S2 not built).
- One package per request (no cheapest/fastest/best-rated choice, G4) and no change of city plan (G3).
- Reservation lookup has no rate limiting; references are random over 10^10 values and unknown/malformed
  numbers get the same 404.
- The cheapest package can use a next-morning airport pickup when no transfer runs at night (legal; shown as a warning).
- Repository hygiene (open): the local PostgreSQL data folder `docker/data/db/` is still tracked in Git, and the
  compose `db` service has a fixed password. Set your real password in `.env` for the app.
