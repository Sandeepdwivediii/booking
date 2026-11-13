# Sandeep (Developer B) — Progress

## Phase 0 — Contract freeze and source audit: PASS (complete)

- Contract: v1.0.0 `fd4f331` → Tanish review (14 findings) → v1.1.0 `3a6d164`, **approved by Tanish**.
- Test reliability fix `4b477dc` (tests only): real-adapter test skips until `app.domain.adapter.create_domain` exists (broken adapter import fails, not skips); all test paths absolute (`REPO_ROOT`, `CATALOG_DIR`).
- Frozen contract on `integration`: `37fc0f0` (fast-forward, no force).
- Official dataset `c49a9cd` (separate commit): `data/catalog/*.json` (8), `data/trip_format_example.json`, `docs/DATA_DICTIONARY.md`, `docs/problem-1-holiday-package.md`; staged blobs verified byte-identical to `hackathon-problem-1-holiday-package.zip`, no secret-like strings, ~560 KB. `.gitattributes` sets `-text` on these files so `core.autocrlf` can't change their bytes (SY-03 hashes).
- **Gate 1 met:** `origin/integration` = `origin/sandeep` = `origin/feat/tanish-domain` = `c49a9cd`. `main` untouched (`ea23f46`).

### Files owned/added in Phase 0

`app/__init__.py`, `app/contracts.py`, `docs/INTEGRATION_CONTRACT.md`, `docs/progress/SANDEEP_PROGRESS.md`, `pytest.ini`, `requirements.txt` (+`pytest==9.2.0`), `.gitattributes`, `tests/platform/test_contract.py`, `tests/platform/test_contract_review.py`, `tests/platform/fixtures/contract_fixtures.py` (TEST-ONLY `FakeDomain`; mocked, not real integration).

### Tests

- `python -m pytest -q tests/platform` → **95 passed, 0 failed, 1 skipped**.
- Skipped: `test_real_domain_factory_matches_contract` — pending integration (needs Tanish's `app.domain.adapter.create_domain`).
- All passing tests run against the contract checkers and the mocked `FakeDomain`; **no real-domain results yet**.
- Phase 0 has no assigned adversarial IDs (SANDEEP.md).

### Audit findings (environment)

- Python 3.11 venv; FastAPI 0.162.5, SQLAlchemy 2.1.8, Pydantic 2.14.3, psycopg 3.3.9 already pinned.
- Compose: `postgres:17-alpine` (`bookingDB`/`booking`, `127.0.0.1:${DB_PORT:-5432}`, healthcheck) + Adminer `127.0.0.1:8080`; volume `./data/db` → `docker/data/db`. Must be preserved.
- `.env` variable names: `POSTGRES_PASSWORD`, `DB_PORT` (values not read).
- Graphify: not installed/configured (no `graphify-out/`); repo is small, targeted reads used instead.

## Phase 1 — Platform foundation: PASS

### Implemented

- `app/config.py`: settings from env/.env (`DATABASE_URL` or `POSTGRES_*`/`DB_PORT`; `TEST_DATABASE_URL`, default `bookingDB_test`; `CATALOG_DIR`, default `data/catalog`). `.env.example` added (placeholders only).
- `app/db.py`, `app/models.py`: SQLAlchemy 2 engine/session; exactly 3 tables, created additively (`create_all`, never drops):
  - `app_packages` (id = trip_id UUID, status DRAFT/BOOKED check, version ≥ 1, request/trip JSONB, total/budget, timestamps)
  - `app_reservations` (reference TEXT PK with `^[0-9]{10}$` check, `package_id` UNIQUE FK, snapshot JSONB)
  - `app_idempotency_records` (UNIQUE package_id+operation+key, request_hash, stored response; operation PATCH/CONFIRM)
- `app/schemas.py`: strict Pydantic `BuildRequestIn` (no coercion, extra fields forbidden, sanity caps from contract); `app/services/request_validation.py`: catalog checks from `domain.options()` (airport, country, nights range, data window).
- `app/services/domain_provider.py`: loads `app.domain.adapter.create_domain(CATALOG_DIR)`; **no production fake fallback**. `app/services/runtime.py`: lazy DB/domain readiness with retry (recovers without restart).
- `app/api/errors.py`: envelope `{"error": {code, message, details}}`; validation details exclude raw input; DB errors → 503; unexpected → 500 without traceback. Body > 64 KB → 422.
- `app/api/routes.py`: `GET /api/health` (200 ready / 503 with per-check reason), `GET /api/catalog/options`, `POST /api/packages/build` (validate → domain → contract shape check → persist **BUILT only** as DRAFT v1 → 201; OVER_BUDGET/NO_PACKAGE → 200, nothing persisted). The Phase 4 work of wiring this to the real adapter is now just a merge, because the route already calls `create_domain`.
- `app/main.py`: app factory, lifespan never crashes on missing DB/catalog, static mount. `app/static/`: form with the 7 fields, options loaded from the API, per-field server errors, loading/result states; rendering uses `textContent` only.
- Run: `docker compose -f docker/docker-compose.yml up -d db` then `uvicorn app.main:app --reload` → http://127.0.0.1:8000.

### Tests (`python -m pytest -q tests/platform` → 189 passed, 0 failed, 1 skipped)

All API tests use TEST-ONLY `tests/platform/fixtures/fake_domain.py` (mocked domain) on the disposable `bookingDB_test` (guarded: name must contain "test" and differ from the app DB).

| ID | Result | ID | Result |
|---|---|---|---|
| IN-01 | PASS | IN-09 | PASS |
| IN-02 | PASS | IN-10 | PASS (mocked domain) |
| IN-03 | PASS (input accepted; mocked domain) | IN-11 | PASS |
| IN-04 | PASS | IN-12 | PASS |
| IN-05 | PASS | IN-13 | PASS (mocked OVER_BUDGET; real behaviour depends on planner) |
| IN-06 | PASS | IN-14 | PASS |
| IN-07 | PASS | IN-15 | PASS |
| IN-08 | PASS | IN-16 | PASS |
| SY-19 | PASS (DB down, catalog missing/corrupt, recovery) | | |

Plus schema/constraint tests, envelope/500 tests, static-form test, loader tests. Skipped: real-adapter contract test (pending integration).

Runtime smoke (real app, real `bookingDB`): `/` 200; `/api/health` 503 `catalog: domain engine not integrated yet`; options/build 503 `CATALOG_UNAVAILABLE`; invalid body 422. `bookingDB` now has the 3 empty app tables, created by startup.

### Decisions

- Client-injected fields (`price`, `country`, `end_date`, …) → 422, rather than being silently ignored.
- Invalid input is rejected before the domain is called; the domain is never called while the DB is down.
- Health body is `{status, checks}` (not the error envelope); 503 when not ready.

### Known limitations

- UI can't load options or build until Tanish's adapter is merged (honest 503). Itinerary view is Phase 2.
- `starlette` TestClient emits a deprecation warning about `httpx` (harmless).

## Phase 2 — Persisted draft, itinerary UI, judge export: PASS

### Implemented

- `app/services/packages.py`: `transaction()` (commit/rollback), `insert_draft`, `get_package(lock=...)` (`SELECT … FOR UPDATE` ready for Phase 3), `get_reservation_for`, `package_view`, `parse_package_id` (malformed id → 404, never 500).
- `app/services/export.py`: `draft_export` (top-level `DRAFT`, all items `PLANNED`/`booking: null`, judge key order) and `booked_export(trip, reference)` (top-level and every item `BOOKED` + `{"reference": …}`), both validated with the contract checkers; malformed output raises `ExportError` → 500 (never served).
- Routes: `GET /api/packages/{id}` (ETag `"<version>"`, `no-store`, status, reference), `GET /api/packages/{id}/export` (attachment `trip-<id>.json`; BOOKED packages export the immutable reservation snapshot after `booked_trip_shape_errors`). Build now uses the helpers and returns `Location` + ETag.
- UI: `app/static/itinerary.js` (pure: day grouping by local date in domain order, day kinds departure/arrival/full/change/return, check-out markers, price breakdown + within/over budget); `app.js` package view (header, status/version or reference, Download JSON, total/budget/left-or-over table, day-by-day plan), URL `/?package=<id>` so refresh/back reloads from the backend; loading/not-found/error states.

### Tests (`python -m pytest -q tests/platform` → 215 passed, 0 failed, 1 skipped)

- Phase 2 (26): GET/ETag/no-store, uppercase UUID, unknown/malformed/injection/oversized ids → 404, build→GET roundtrip, **draft survives app restart** (BK-15 groundwork), DB down → 503, transaction rollback, row lock blocks `FOR UPDATE NOWAIT`, draft export full/judge-shaped (SY-06/07/10/12 shape groundwork), corrupted draft refused, booked export from snapshot with leading-zero reference (BK-06/BK-14 groundwork), mixed BOOKED/PLANNED snapshot refused (SY-11 groundwork), serializer units; itinerary.js under Node (day kinds, order, check-out, cross-zone order kept, breakdown within/over).
- No Phase 2 adversarial IDs are assigned. The IDs above are groundwork only; they're formally verified with real data in Phases 4/6.
- All domain data are TEST-ONLY fakes (mocked); no real planner results yet. Skipped: real-adapter contract test (pending integration).
- Smoke: real app + fake domain on `bookingDB_test`: build 201 → GET 200 `ETag "1"` → export 200 attachment (10 items, no shape errors) → `/?package=<id>` 200. `node --check` passes for both JS files. No browser automation (manual UI check pending).

## Open issues (deferred by Sandeep; not Phase 0 scope)

- 1,271 PostgreSQL files tracked under `docker/data/db/**` (`.gitignore` only ignores `/data/db/`). Do not untrack without explicit approval; safe procedure: stop db → back up → ignore `docker/data/` → `git rm -r --cached docker/data/db` → verify → commit; other clones must back up before pulling (git would delete their copies).
- `docker/docker-compose.yml` hardcodes `POSTGRES_PASSWORD` instead of `${POSTGRES_PASSWORD}`; no `.env.example` (Phase 1 config work, needs approval for compose change).
- Untracked local-only files: `SANDEEP.md`, `TANISH.md`, `TEAM_RULES.md`, zip, `prompt.md`, `temp_dataset/` (TEAM_RULES suggests committing `TEAM_RULES.md` + `@TEAM_RULES.md` in `CLAUDE.md`; not done).

- `docker/data/db/**` shows as locally modified (live Postgres writes to tracked files). Never stage them; avoid `git checkout -- .`, `reset --hard`, `stash`, or branch switches that touch those paths.

## Next: Phase 3 (awaiting Sandeep approval)

Generic PATCH/confirm transaction machinery with an injected deterministic DomainPort (row lock, `If-Match`, idempotency key + request hash replay before version check, unique constraints); trip options/flight/hotel/attraction controls and reservation lookup shell; independent raw-fixture attraction oracle `tests/platform/test_oracle_attractions.py` (AT-01..AT-17).
