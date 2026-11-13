# Sandeep (Developer B) — Progress

## Phase 0 — Contract freeze

- Contract v1.0.0 proposed in `fd4f331`; Tanish review: CHANGES REQUIRED (14 findings).
- Contract v1.1.0 committed in `3a6d164b0fba0ab7562851b36dcd0f708be16f2a` on `sandeep`; **APPROVED by Tanish** (all 14 findings resolved; he independently ran the contract tests: 95 passed, 1 skipped).
- `3a6d164` changes vs base `ea23f46`: `app/__init__.py`, `app/contracts.py`, `docs/INTEGRATION_CONTRACT.md`, `docs/progress/SANDEEP_PROGRESS.md`, `pytest.ini`, `requirements.txt` (+`pytest==9.2.0`), `tests/platform/fixtures/contract_fixtures.py`, `tests/platform/test_contract.py`, `tests/platform/test_contract_review.py`. No `docker/**`, `.env`, `.gitignore` or Tanish-owned paths.
- v1.1.0 contents: attraction day rules (`attraction_count_errors`), `booked_trip_shape_errors`, `change_result_shape_errors(result, original_trip)`, BUILT `end_date`/`cheapest_total_inr` checks, OVER_BUDGET `reason_code` null + non-empty `reason`, `build_request_errors`, `alternatives_shape_errors`, item city/quantity semantics, Python 3.11+.

### Post-approval test reliability fix (tests only; DomainPort and contract semantics unchanged)

- Real-adapter test skips (reason `pending integration`) until both `app.domain.adapter` and `create_domain` exist; an adapter that exists but fails to import now FAILS instead of skipping. Also skips if the official catalog is absent.
- All test paths are absolute (`REPO_ROOT`, `CATALOG_DIR` in `tests/platform/fixtures/contract_fixtures.py`); tests pass when run from any working directory.
- Verified: `python -m pytest -q tests/platform` → 95 passed, 0 failed, 1 skipped (real adapter, pending integration), from repo root and from an unrelated cwd. Skip/fail/pass behaviour simulated in a throwaway copy (no adapter → skip; adapter without `create_domain` → skip; broken import → fail; stub adapter → pass).

### Branch sync (pending Sandeep approval)

- Shared base per TEAM_RULES: `integration` (reviewed merge checkpoints). `main`, `integration`, `feat/tanish-domain` were all at `ea23f46` (same tree as `a740c41`); dry-run merge with `sandeep` is conflict-free.
- Plan: commit test fix on `sandeep` → merge `origin/integration` into `sandeep` → push → fast-forward `origin/integration` to `sandeep` → Tanish fast-forwards `feat/tanish-domain`. `main` untouched until the final merge.
- `docker/data/db/**` is identical on every branch, so none of these steps rewrite PostgreSQL files.

## Remaining Phase 0 blockers

- Sandeep approval of the sync commands; then gate 1: both working branches on the same `integration` SHA.
- Official catalog `data/catalog/*.json` is untracked, so a fresh clone has no catalog (real-adapter test would skip, Tanish's loader tests need it). Needs a decision to commit it.

## Open issues (not Phase 0 contract scope)

- ~1,271 PostgreSQL files tracked under `docker/data/db/**` (`.gitignore` only ignores `/data/db/`). Cleanup needs Sandeep's explicit approval; nothing untracked yet.
- `docker/docker-compose.yml` hardcodes `POSTGRES_PASSWORD`; no `.env.example` yet (Phase 1).
- `TEAM_RULES.md`, `docs/DATA_DICTIONARY.md` not committed; `temp_dataset/`, zip and `prompt.md` should be ignored.

## Next

Phase 1 (FastAPI + PostgreSQL + form) only after branch sync and Sandeep's explicit go-ahead.
