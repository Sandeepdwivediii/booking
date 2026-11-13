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

## Phase 3 — Transaction machinery, edit/confirm UI, attraction oracle: PASS

### Implemented

- `app/services/transactions.py`. One short transaction per mutation; inside it, in this order: `SELECT … FOR UPDATE` (with `lock_timeout` 5s, which gives 503 instead of hanging) → idempotency replay (same key + same request hash → stored response; different hash → 409 `IDEMPOTENCY_CONFLICT`) → `ALREADY_BOOKED` → exact `If-Match` (409 `VERSION_CONFLICT`, `current_version` in details) → domain call on the locked latest trip → write package + idempotency record (and the reservation for confirm) → COMMIT.
  - **Rejected edits:** return 422 `INVALID_CHANGE`; the package row is left byte-identical (incl. `updated_at`), and the outcome is stored so a retry gets the same response.
  - **Not stored:** version conflicts and errors, so a retry can still succeed.
  - **Confirm:** books the latest draft after `validate(…, "confirm")` (422 `CONFIRM_REJECTED`). The 10-digit TEXT reference comes from `secrets`; it's inserted in a SAVEPOINT and retried on a collision (up to 10 tries, then 503 with nothing written). The snapshot is `booked_export` (checked against the contract). An already-BOOKED package returns its existing reservation for any key.
  - **Fault-injection hook:** `checkpoint(name)`, used by the atomicity tests.
- **Routes** (thin wrappers, needed so the UI controls can be exercised):
  - `GET /api/packages/{id}/alternatives`
  - `PATCH /api/packages/{id}` (`If-Match`, `Idempotency-Key`; strict discriminated `ChangeIn` body)
  - `POST /api/packages/{id}/confirm`
  - `GET /api/reservations/{reference}` (malformed reference → 404)
  - Formal ED-*/BK-* acceptance stays in Phases 5/6 with the real domain.
- **UI:** Change hotel / Change flight (alternatives list with price difference), Remove attraction, + Add attraction per arrival/full/change day, Confirm and book (one Idempotency-Key per package version kept in `sessionStorage`), and a header reservation lookup. URLs are `/?reservation=<ref>`, and the booked view is read-only. Errors show as banners: INVALID_CHANGE/CONFIRM_REJECTED list the violations; VERSION_CONFLICT/ALREADY_BOOKED offer "Reload package". The UI only renders what the backend returns.
- **Oracle:** `tests/platform/fixtures/attraction_oracle.py` is an independent check on raw `attractions.json`-shaped records. It imports nothing from `app.*` (enforced by an AST test). It checks city vs that night's hotel, weekday, `closed_dates`, opening hours incl. `24:00`, end = start + visit, overlap (adjacent allowed), uniqueness, return day, arrival before city arrival, and counts per mode. `load_catalog_attractions()` is ready to check real BUILT trips in Phase 4.

### Tests (`python -m pytest -q tests/platform` → 286 passed, 0 failed, 1 skipped)

| Group | Result |
|---|---|
| AT-01 … AT-17 (`test_oracle_attractions.py`, 25 tests) | **all PASS** (oracle on raw fixtures; real-catalog normalization incl. 56 records, missing `closed_dates`, Nara) |
| Transactions/concurrency (`test_phase3_transactions.py`, 46 tests, real PostgreSQL) | PASS |
| Phases 0–2 regression | PASS |

Concurrency and atomicity verified on real PostgreSQL. The edit/confirm logic is tested with the TEST-ONLY deterministic fake; these are groundwork for the formal IDs in Phases 5/6:
- **Two tabs, same version:** exactly one 200 and one 409; the loser never calls `domain.change` (ED-12/19).
- **Same key sent concurrently:** applied once (ED-14).
- **PATCH vs CONFIRM, three rounds:** the booked snapshot always equals the last committed draft (ED-13).
- **20 concurrent confirms:** 1 reservation, the same reference for all, and `validate` called once (BK-01/09).
- **Two packages confirmed concurrently:** distinct references (BK-04).
- **Injected crashes:** after the package UPDATE (ED-16), after the reservation INSERT (BK-07) and after the status UPDATE (BK-08), everything rolls back and the package stays confirmable.
- **Reference collision:** retried, with leading zeros kept (BK-05/06).
- **Lock timeout:** returns 503 instead of hanging.
- **Mutation check:** with `FOR UPDATE` removed, 4 of the 5 concurrency tests fail, so the lock is what they test.

Browser smoke (`tests/platform/smoke/browser_smoke.py`, headless Chrome via DevTools, real app + fake domain on `bookingDB_test`): **20/20 PASS, no JS errors**. It covers options/health, the 422 field error, build → package view, remove/add attraction, the refused over-budget hotel, the stale-tab VERSION_CONFLICT + reload, refresh persistence, confirm → reference, the read-only booked view, refresh after booking, and lookup (unknown / malformed / valid).

## Phase 4 — Integration checkpoint (real domain): PASS

### Integration

- Reviewed `origin/feat/tanish-domain` @ `b3b37be` (3 commits, 19 files, **all in Tanish-owned paths**: `app/domain/**`, `tests/domain/**`, `tests/domain_fixtures/**`, `docs/HANDOFF_TANISH.md`; stdlib-only imports + `app.contracts`; no `docker/`, no platform files, no contract changes).
  - `options`, `build` and `validate` are real.
  - `alternatives`/`change` are a documented Phase 5 skeleton: argument checks, then `NotImplementedError`, with no fake data.
  - His handoff reports 273 domain tests passing.
- Merged into `sandeep` with `--no-ff` (merge commit `75bbd7d`), without switching branches. A dry-run `merge-tree` was clean; the merge changed 0 `docker/` files. No Tanish file was modified.
- Production path: `load_domain` → `app.domain.adapter.create_domain(data/catalog)`. The app reports health `ready` on `bookingDB` (no writes). Tests assert there is no fake fallback: production loader type, and no `tests` imports or `FakeDomain` anywhere in `app/**`.
- `NotImplementedError` from the domain → 503 `SERVICE_UNAVAILABLE` "This option is not available yet." (was a generic 500). Flight times in the UI now show the departure only (arrival is in another time zone, shown in details).

### Tests (`python -m pytest -q` full → 590 passed, 0 failed, 1 skipped)

- Real-adapter contract test (`test_real_domain_factory_matches_contract`) now **runs and passes** (was the pending skip). The one skip is the Phase 1 "adapter missing" loader test, now inapplicable.
- `tests/platform/test_phase4_integration.py` (31 tests, **real domain + original catalog + real PostgreSQL**, no fakes):
  - Build → persist → GET → export for 4 requests: Menon Japan, DEL UAE solo, BOM Japan infant+teen, and MAA UAE 10 nights across two cities. Each export passes **Tanish's validator** (`initial` + `confirm`), my **attraction oracle**, and my new **catalog oracle** (`tests/platform/fixtures/catalog_oracle.py`, raw JSON only, no `app.*` imports). The catalog oracle re-derives every price, quantity, ID, room/hotel/city link, per-night availability, seat count, catalog flight times, flight duration across time zones (UTC), pickup ≥ landing + 60, airport ≥ departure − 180, completeness and total.
  - Real OVER_BUDGET (budget 0 and cheapest−1 → exact cheapest/shortfall; budget == cheapest → BUILT), real NO_PACKAGE (reason code + reason), neither persisted; deterministic rebuild; real confirm (snapshot passes booked shape, validator and catalog oracle; lookup works); real edits → honest 503.
  - Sweep test: 54 request combinations → every BUILT trip passes both oracles. A one-off full sweep found **843 BUILT trips (379 multi-city) + 261 NO_PACKAGE, 0 oracle findings**.

| Phase 4 ID | Result | Evidence (real exports) |
|---|---|---|
| SY-06 | PASS | 2-item sample flagged by catalog oracle + Tanish validator; real exports complete |
| SY-07 | PASS | missing `room_id` flagged; stored draft without it → export refused (500) |
| SY-08 | PASS | flight qty/price derived from catalog; mutation flagged |
| SY-09 | PASS | PER_VEHICLE qty = ceil(travelers/max); people-qty mutation flagged |
| SY-10 | PASS | BOOKED-in-draft flagged; export normalizes draft items to PLANNED/null |
| SY-12 | PASS | `sources: []` on every item; missing → shape error |
| SY-13 | PASS | invented ID per type flagged by catalog oracle and `UNKNOWN_CATALOG_ID` |
| SY-15 | PASS | UTC timeline order + flight duration across IST/JST/GST; reorder flagged; UI keeps domain order |
| BK-15 | PASS | real draft and export byte-identical after a fresh app instance (restart) |

Also re-run on the real domain: IN-03, IN-06, IN-07, IN-10, IN-13 → PASS.

Browser smoke (`tests/platform/smoke/browser_smoke.py`): **real mode 8/8 PASS** (options, health, 422, real build → view, edit shows "not available yet", real confirm → reference, refresh) and **fake mode 20/20 PASS**; no JS errors.

### Coverage: real vs fake

- **Real domain:** build, validate, confirm, export, OVER_BUDGET/NO_PACKAGE, the SY IDs and BK-15 above.
- **Fake domain only** (machinery tests, not proof of real behaviour): PATCH/alternatives flows, ED-* groundwork, the concurrency tests. **BLOCKED for real** until Tanish's Phase 5 `alternatives`/`change`.

### Notes for Tanish (not defects)

- Cheapest Menon package uses a next-morning airport pickup (legal, flagged in `warnings`, shown in the UI). Consider whether demo UX would prefer a same-evening option when the price is equal.

### Phase 4 checkpoint closed

`origin/integration` was fast-forwarded `c49a9cd → 8b8df4d` (no force), with Sandeep's approval. Tanish sync: `git merge --ff-only origin/integration`.

## Phase 5 — Edits (M6): BLOCKED (real domain edits not available)

### Status of Tanish's domain

- `origin/feat/tanish-domain` @ `98ccbb0` adds only `tests/domain/test_phase4_api_roundtrip.py` and a handoff update. It was merged into `sandeep` (merge commit touches only those 2 files; dry run clean); his round-trip test passes against the platform (21 passed).
- `app/domain/edits.py` is **still the Phase 5 skeleton** (`NotImplementedError` for real `alternatives()`/`change()`). The API returns an honest 503 "not available yet"; there is no fake fallback.

### Implemented (platform)

- Confirm ordering fix (SY-18): `domain.validate(…, "confirm")` now runs **before** the platform shape check. A tampered stored draft returns 422 `CONFIRM_REJECTED` with the domain's violations (it was a 500). 500 only if the domain accepts a malformed draft.
- TEST-ONLY fake now rejects non-catalog (`FAKE-`) ids like a real catalog.
- `tests/platform/test_phase5_edits.py`: each acceptance case runs as `[fake]` (platform machinery) and `[real]` (production loader, original catalog). Real cases needing a successful real edit skip as BLOCKED and **will run automatically** once Tanish implements `change()`/`alternatives()`.
- The edit UI from Phase 3 already covers the Phase 5 UI items: no optimistic updates, backend price/version only, INVALID_CHANGE/VERSION_CONFLICT/ALREADY_BOOKED messages. Re-verified in the browser.

### Tests (`python -m pytest -q` → 639 passed, 0 failed, 13 skipped)

| ID | [fake] | [real] |
|---|---|---|
| ED-09 valid hotel/flight change | PASS | BLOCKED |
| ED-10 remove attraction | PASS | BLOCKED |
| ED-11 edit over budget rejected, row unchanged | PASS | BLOCKED |
| ED-11 over-budget draft never confirmed | PASS | **PASS** |
| ED-12 two tabs, same If-Match | PASS | BLOCKED |
| ED-13 PATCH vs CONFIRM serializable | PASS | BLOCKED |
| ED-14 same key + payload replays after version advanced | PASS | BLOCKED |
| ED-15 same key, different payload → 409 | PASS | BLOCKED |
| ED-16 crash between UPDATE and idempotency INSERT | PASS | BLOCKED |
| ED-17 two keys sequential | PASS | BLOCKED |
| ED-18 PATCH after confirm → ALREADY_BOOKED, snapshot intact | PASS | **PASS** |
| ED-19 stale PATCH rechecked inside the row lock (holder commits v2 while locked) | PASS | **PASS** |
| PR-18 client amounts/trip/version in PATCH → 422 | PASS | **PASS** |
| PR-18 totals after edit are server-derived | PASS | BLOCKED |
| SY-18 forged hotel/attraction/item ids on PATCH | PASS | BLOCKED |
| SY-18 coherent forged price in stored draft caught at confirm | n/a (fake does not re-price) | **PASS** |

- Real-domain results so far: 5 PASS, 11 BLOCKED. All fake-domain machinery passes on real PostgreSQL. Browser smoke: fake 20/20, real 8/8, no JS errors.
- Regression: Phases 0–4 all pass (incl. 31 real-domain Phase 4 tests and Tanish's 294 domain tests).

### Blocker

Tanish: implement `list_alternatives` / `apply_change` (contract §2: hotel incl. same-hotel rooms, flights, attractions with `start_time`; all-or-nothing, re-price, `validate(…, "modified")`, `adjustments`). No platform change is needed; rerun `python -m pytest -q tests/platform/test_phase5_edits.py`.

## Open issues (deferred by Sandeep; not Phase 0 scope)

- 1,271 PostgreSQL files tracked under `docker/data/db/**` (`.gitignore` only ignores `/data/db/`). Do not untrack without explicit approval; safe procedure: stop db → back up → ignore `docker/data/` → `git rm -r --cached docker/data/db` → verify → commit; other clones must back up before pulling (git would delete their copies).
- `docker/docker-compose.yml` hardcodes `POSTGRES_PASSWORD` instead of `${POSTGRES_PASSWORD}`; no `.env.example` (Phase 1 config work, needs approval for compose change).
- Untracked local-only files: `SANDEEP.md`, `TANISH.md`, `TEAM_RULES.md`, zip, `prompt.md`, `temp_dataset/` (TEAM_RULES suggests committing `TEAM_RULES.md` + `@TEAM_RULES.md` in `CLAUDE.md`; not done).

- `docker/data/db/**` shows as locally modified (live Postgres writes to tracked files). Never stage them; avoid `git checkout -- .`, `reset --hard`, `stash`, or branch switches that touch those paths.

## Next

- When Tanish's Phase 5 lands: merge, rerun `test_phase5_edits.py` (11 BLOCKED → expected to run), browser smoke real mode with edits.
- Phase 6 (awaiting approval): formal BK-01..BK-16 + SY-16 on the real domain (confirm/lookup machinery exists since Phase 3), restart/retry evidence, 20-confirm race, booked UI polish.
