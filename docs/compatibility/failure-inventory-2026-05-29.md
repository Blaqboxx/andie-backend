# Discovery Failure Inventory (2026-05-29)

Source run:
- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`
- Result: 187 run, 10 failures, 3 errors, 1 skipped

## Bucket A: API Contract Compatibility

1. `tests/test_autonomy_explainer_api.py`
- Symptom: `KeyError: status`
- Classification: response shape mismatch (missing expected `status` field)

2. `tests/test_coinmarketcap_agent.py`
- Symptom: endpoint 404s and payload status mismatch (`error` vs `executed`)
- Classification: legacy route contract + response contract mismatch

3. `tests/test_cryptonia_overseer.py`
- Symptom: endpoint 404s for capabilities/overseer paths
- Classification: legacy route availability mismatch

4. `tests/test_frontend_ui_agent.py`
- Symptom: 404 + payload status mismatch
- Classification: legacy endpoint + response contract mismatch

5. `tests/test_trading_approvals_api.py`
- Symptom: approval endpoints return 404
- Classification: missing legacy API route contract

6. `tests/test_skills_api.py` (import blocker)
- Symptom: `AttributeError: module skills.registry has no attribute register`
- Classification: compatibility break in legacy registry wiring (import-time API contract)

## Bucket B: Namespace / Packaging

1. `tests/test_capital_orchestration.py`
- Symptom: `ModuleNotFoundError: No module named andie_backend.trading`
- Classification: namespace shim gap for trading package imports

## Bucket C: Environment Assumptions

- None currently represented in the latest failure set after previous path hardening.

## Bucket D: Logic Regressions

- None confirmed yet; all current signatures point to compatibility/contract gaps.

## Burn-down Order (ROI)

1. Fix `test_skills_api` import blocker (unlocks a large module quickly).
2. Restore/alias missing API routes used by coinmarketcap/cryptonia/frontend/trading approvals tests.
3. Add `andie_backend.trading` namespace compatibility shim.
4. Align autonomy explainer response shape to include legacy `status` field.
5. Re-run full discovery and recategorize.

## Delta After Bucket A/B Patch Pass

Validation run:
- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_autonomy_explainer_api tests.test_capital_orchestration tests.test_coinmarketcap_agent tests.test_cryptonia_overseer tests.test_frontend_ui_agent tests.test_trading_approvals_api`
- Result: all passing (18 tests).

Remaining concentrated debt:
- `tests/test_skills_api.py` now dominates failures and represents a separate legacy API-surface restoration track.
- Primary themes: missing skills/control-plane endpoints in `interfaces/api/main.py`, missing legacy globals/metrics hooks, and a few response-shape mismatches.

Updated prioritization:
1. Re-introduce/wire the legacy skills and control-plane endpoint family.
2. Add compatibility exports for `control_plane_metrics` and any other patched test symbols.
3. Normalize autonomy control state defaults (`_autonomy_state`) to avoid NameError paths.
4. Re-run `tests.test_skills_api` in isolation until green.
5. Re-run full discovery and refresh this inventory.

## Update 2026-05-30 (skills control-plane pass)
- Implemented consolidated skills/control-plane compatibility contracts in interfaces/api/main.py.
- Restored shared module alignment for outcome/control metrics and policy audit logging.
- Unified skills registry wiring to avoid split namespace registration issues.
- Validation: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_skills_api -> 47/47 passing.

## Update 2026-05-29 (full discovery refresh post skills/control-plane)
- Run: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
- Result: 233 run, 1 error, 1 skipped
- Status: major reduction from prior 10 failures + 3 errors baseline.

Remaining failing cluster:
1. tests/test_capital_orchestration.py
- Symptom: ModuleNotFoundError: No module named andie_backend.trading
- Classification: namespace compatibility shim/import-path gap (legacy package export)

Interpretation:
- Skills/control-plane hotspot is now fully green in suite-level validation.
- Remaining debt appears concentrated in legacy namespace/export compatibility, not governance or executive logic.

## Final Baseline 2026-05-29 (discovery modes aligned)
- Runs:
  - PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
  - PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t .
- Result (both): 234 discovered, 233 passed, 1 skipped, 0 failures, 0 errors.

Final blocker root cause:
- Discovery import order occasionally preloaded a non-repo andie_backend package instance.
- That polluted sys.modules and removed andie_backend.trading from namespace resolution.

Stabilization fix summary:
1. tests/test_effectiveness_load_phase5j.py
- Insert REPO_ROOT into sys.path before any andie_backend import path is attempted.

2. tests/test_capital_orchestration.py
- If preloaded andie_backend is not repo-backed, evict andie_backend* entries from sys.modules.
- Invalidate importlib caches and rebind package __path__ to repository-backed namespace candidates.

Outcome:
- Namespace resolution is now deterministic under both unittest discovery modes.
