# Sandeep (Developer B) — Progress

## Phase 0 — Contract freeze (in review)

- Branch: `sandeep`. Base reviewed commit: `fd4f331` (contract v1.0.0).
- Tanish review of `fd4f331`: **CHANGES REQUIRED** (4 required changes + 6 clarifications). DomainPort methods, ownership, build states and edit/confirm flow approved.
- v1.1.0 revision: committed on `sandeep` (parent `fd4f331`, the commit that adds this file) and pushed to `origin/sandeep` for Tanish's final review. **Not frozen** until Tanish approves that exact commit. Not merged into `main`, `integration` or `feat/tanish-domain`.
- Tests at commit time: `python -m pytest -q tests/platform` → 95 passed, 0 failed, 1 skipped. The skip is `test_real_domain_factory_matches_contract`: pending integration, it runs once `app.domain.adapter.create_domain` exists.
- Untouched in this commit: `docker/data/db/**`, `docker/docker-compose.yml`, `.env`, `.gitignore`, and all Tanish-owned paths (`app/domain/**`, `tests/domain/**`).
- Changes in v1.1.0:
  - `app/contracts.py`: attraction day rules + `attraction_count_errors`; `booked_trip_shape_errors`; `change_result_shape_errors(result, original_trip)`; BUILT `end_date`/`cheapest_total_inr` checks; OVER_BUDGET `reason_code` null + non-empty `reason`; `build_request_errors`; `alternatives_shape_errors`; flight/attraction quantity = travelers; Python 3.11+ note.
  - `docs/INTEGRATION_CONTRACT.md`: updated to v1.1.0 with changelog (§6).
  - Tests: `tests/platform/test_contract.py`, `tests/platform/test_contract_review.py`, fixtures `tests/platform/fixtures/contract_fixtures.py` (TEST-ONLY fake domain).
  - Infra: `app/__init__.py`, `pytest.ini`, `pytest==9.2.0` in `requirements.txt`.
- Test command: `python -m pytest -q tests/platform`.

## Remaining Phase 0 blockers

- Tanish's final approval of the v1.1.0 commit (contract freeze gate).
- Shared base for both working branches not yet agreed (`integration` vs `main`).

## Open issues (not Phase 0 contract scope)

- ~1,271 PostgreSQL files tracked under `docker/data/db/**` (`.gitignore` only ignores `/data/db/`). Cleanup needs Sandeep's explicit approval; procedure reported in session, nothing untracked yet.
- `docker/docker-compose.yml` hardcodes `POSTGRES_PASSWORD`; no `.env.example` yet (Phase 1).
- Catalog (`data/catalog/*.json`), `TEAM_RULES.md`, `docs/DATA_DICTIONARY.md` not yet committed; `temp_dataset/`, zip and `prompt.md` should be ignored.

## Next

Phase 1 (FastAPI + PostgreSQL + form) only after the v1.1.0 contract is approved and committed.
