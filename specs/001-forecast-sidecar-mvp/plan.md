# Implementation Plan: Forecast Sidecar MVP

**Branch**: `001-forecast-sidecar-mvp` | **Date**: 2026-04-28 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/001-forecast-sidecar-mvp/spec.md`

## Summary

Stand up a Python sidecar that serves per-`(company, computed_object)`
LightGBM forecasts behind authenticated HTTP and produces those models from
a batch-job entrypoint built into the same image. The HTTP path is a thin
FastAPI app that resolves a model from GCS (cached in-process with bounded
size + TTL), runs `MLForecast.predict` with caller-supplied future features
and optional per-period overrides, and returns point + 80%/95% conformal
intervals. The training path is a Click CLI that reads a staged history CSV
and feature config JSON from signed GCS URLs, fits an `MLForecast` with
`LGBMRegressor`, calibrates intervals via `PredictionIntervals(n_windows=10)`,
and atomically promotes a new versioned artifact directory in GCS only after
both `model.pkl` and `metadata.json` are durable. OIDC verification, structlog
JSON to Cloud Logging, Sentry, and trace propagation mirror the existing
`toolsname-agent-sidecar` so operations are uniform across both sidecars.

## Technical Context

**Language/Version**: Python 3.11+ (constitution-mandated)
**Primary Dependencies**:
- Core: `mlforecast`, `lightgbm`, `utilsforecast` (metrics)
- Web: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`
- Cloud: `google-cloud-storage`, `google-auth`
- Observability: `structlog`, `sentry-sdk`, `opentelemetry-api` (trace context only — no SDK setup unless we add OTel later)
- Data: `polars` for I/O, `pandas` at the mlforecast API boundary, `pyarrow`
- CLI: `click` for `train_cli`
- Persistence: `joblib` for model serialization (mlforecast docs default)

**Storage**:
- **Durable**: GCS — `gs://{FORECAST_BUCKET}/forecasts/{company_id}/{co_id}/{vN}/{model.pkl, metadata.json}` plus `latest.json` and (on failure) `error.json`
- **Process memory**: LRU+TTL cache, default 100 entries × 1h TTL

**Testing**:
- `pytest` (unit + API) with `httpx.AsyncClient` against the FastAPI app via `lifespan="on"`
- `pytest-asyncio` for async paths
- `freezegun` for TTL/time tests
- A `tests/fakes/gcs.py` in-memory storage fake implementing the small subset of `google.cloud.storage` we use (signed URLs in tests resolve to `file://` paths; production-only constraint enforced by config)
- Smoke training run on a synthetic series fixture (constitution gate)

**Target Platform**:
- Linux container (Cloud Run service + Cloud Run Job, same image, two entrypoints)
- Region: same as backend primary residency (default `europe-west1` per spec assumptions; configurable)

**Project Type**: Web service + batch job from a single Python package (single project, src layout)

**Performance Goals** (from spec SC-001..006):
- Warm-cache p99 inference ≤ 500 ms end-to-end
- Cold-load p99 ≤ 3 s for ≤ 5 MB artifact
- Training p95 ≤ 60 s for 24 monthly periods × 5 series

**Constraints**:
- Stateless service tier — no DB, no per-request disk writes
- OIDC inbound on every endpoint except `/healthz` and `/readyz`
- LightGBM `deterministic=True`, fixed `seed`, fixed `num_threads` (constitution Principle I)
- One Docker image, two entrypoints (`uvicorn` for service, `python -m forecast_sidecar.train_cli` for job)
- Inference identity: `roles/storage.objectViewer`; trainer identity: `roles/storage.objectAdmin` — no privilege overlap

**Scale/Scope** (v1 expectations):
- O(hundreds) of `(company, CO)` models simultaneously promoted
- Weekly retraining cadence per pair, fan-out via Cloud Tasks
- Cache holds 100 models in 1 GiB service memory; cold loads from GCS at request time

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluating against `.specify/memory/constitution.md` v1.0.0:

### I. Reproducibility (NON-NEGOTIABLE) — ✅ PASS
- `uv.lock` committed; container builds via `uv sync --frozen`
- A `set_seed(seed)` helper called at the entrypoint of `train_cli`; seeds `random`, `numpy`, and the LightGBM `seed` param
- LightGBM training params: `deterministic=True`, `seed=<config>`, `num_threads=<config>` (default 1 for Cloud Run Job CPU=2 → reserve 1 thread for OS)
- Run manifest = the `metadata.json` written next to each model: includes git SHA (build-time env var `GIT_SHA`), config hash (sha256 of canonicalized feature-config JSON), data hash (sha256 of the history CSV bytes), Python + library versions
- No model is promoted to `latest.json` without a complete `metadata.json` next to it (FR-013, FR-015)

### II. Temporal Integrity (NON-NEGOTIABLE) — ✅ PASS (with documented boundary)
- **Training**: lags, rolling stats, and date features defined declaratively in the feature config and bound to mlforecast via `lags=`, `lag_transforms=`, `date_features=`. Project code does no `.shift()` on the target column.
- **Evaluation**: holdout via `MLForecast.cross_validation` with rolling-origin windows (`n_windows >= 10`). No random k-fold.
- **Exogenous classification**: feature config declares each non-target column as `static_features`, `historic_exog`, or `future_exog`. The contract for `/forecast` requires the caller to provide all `future_exog` columns for every requested period.
- **Boundary note**: at inference, `future_features` is computed by the **caller** (Go backend reading from Postgres). This is documented and does not violate Principle II — the constitution governs *this service's* feature computation, and this service performs none at inference. A constitution-exception comment is not needed because the principle is preserved by routing all feature engineering through mlforecast at training time.

### III. Data Contract — ✅ PASS
- Loader (`storage.read_history`) validates the history CSV at load time using a `pandera`/`pydantic` schema generated from the feature config: `period` (datetime, monotonic per `unique_id`), `target` (float, non-NaN), declared feature columns with declared dtypes, declared `freq` (`D`, `W-MON`, `MS`, etc.), gap policy applied per config.
- Inference request schema is Pydantic v2 — strict, no implicit coercion. `future_features` row count must equal `horizon_periods` (FR-005).
- Polars is used for ETL inside the trainer; conversion to pandas happens once at the mlforecast call boundary.

### IV. Baseline-Beating Evaluation — ✅ PASS
- The trainer fits one `MLForecast` with `[lgb.LGBMRegressor(...), SeasonalNaive(season_length=infer_seasonality(freq))]`. Both models go through the same CV; per-horizon and per-`unique_id` MAE/sMAPE are computed via `utilsforecast.evaluation`.
- `metadata.json.metrics` includes `baseline_smape` next to `model_smape`. SC-005 is enforced by a *gate* in the trainer: if `model_smape > baseline_smape * 0.9` for the global aggregate, the trainer still writes the artifact and metadata but **does not update `latest.json`** and writes `error.json` flagging the regression.
- Per-series results retained in `metadata.json.metrics.per_series` for inspection.

### V. Configuration Over Code — ✅ PASS
- Hyperparameters, lags, rolling stats, date features, target transforms, frequency, and target column all live in the per-`(company, CO)` `feature_config.json`. Config is content-hashed and the hash lands in `metadata.json`.
- The trainer entrypoint reads config and binds mlforecast at startup; no `if model_version == ...` branches in `model/train.py`.
- Service-level config (env vars in §10 of the spec) is loaded by `pydantic_settings.BaseSettings`.

**Result**: 5/5 principles green. No entries needed in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/001-forecast-sidecar-mvp/
├── plan.md                    # This file
├── research.md                # Phase 0 output
├── data-model.md              # Phase 1 output
├── quickstart.md              # Phase 1 output
├── contracts/                 # Phase 1 output
│   ├── openapi.yaml           # /forecast, /healthz, /readyz
│   ├── train_cli.md           # CLI contract (Click) — args, exit codes, GCS layout
│   └── feature_config.schema.json  # JSON Schema for the per-(company, CO) feature config
├── checklists/
│   └── requirements.md        # Created by /speckit-specify
└── tasks.md                   # Created later by /speckit-tasks
```

### Source Code (repository root)

```text
forecast-sidecar/
├── pyproject.toml             # uv project config: deps, ruff, mypy, pytest
├── uv.lock                    # locked dependency graph (committed)
├── Dockerfile                 # multi-stage: uv → slim runtime; ENTRYPOINT [], CMD configurable
├── .dockerignore
├── README.md                  # stack, layout, local dev, deploy, contract (acceptance §14.5)
├── .github/workflows/
│   ├── ci.yml                 # uv sync && ruff && mypy && pytest
│   └── deploy.yml             # build → Artifact Registry → Cloud Run service + Job
├── .pre-commit-config.yaml    # ruff, mypy, pytest -m "not slow and not gpu"
├── configs/
│   └── lightgbm_defaults.yaml # default LGBMRegressor params (overridable per feature_config)
├── src/forecast_sidecar/
│   ├── __init__.py
│   ├── main.py                # FastAPI app + lifespan (cache, sentry, structlog init)
│   ├── train_cli.py           # Click CLI entrypoint (`python -m forecast_sidecar.train_cli`)
│   ├── auth.py                # OIDC verify dependency + audience/allowlist enforcement
│   ├── config.py              # pydantic-settings BaseSettings (env)
│   ├── schemas.py             # Pydantic v2 request/response models
│   ├── storage.py             # GCS read/write, atomic latest promotion, signed-URL fetch
│   ├── cache.py               # LRU+TTL model cache (cachetools.TTLCache wrapped)
│   ├── observability.py       # structlog config + sentry init + trace ctx propagation
│   ├── seeds.py               # set_seed(seed) helper (random/numpy/lightgbm)
│   ├── manifest.py            # build run manifest (git SHA, config/data hashes, lib versions)
│   └── model/
│       ├── __init__.py
│       ├── features.py        # feature config → mlforecast args (lags, transforms, date feats)
│       ├── train.py           # fit, calibrate, evaluate, package
│       ├── predict.py         # load + apply scenario_overrides + predict
│       └── baselines.py       # seasonal-naive baseline + sMAPE gate (constitution IV)
└── tests/
    ├── conftest.py            # synthetic series fixtures, GCS fake, time fixtures
    ├── fakes/
    │   └── gcs.py             # in-memory GCS stand-in
    ├── fixtures/
    │   ├── sample_history.csv
    │   └── sample_feature_config.json
    ├── unit/
    │   ├── test_features.py
    │   ├── test_baselines.py
    │   ├── test_cache.py
    │   ├── test_manifest.py
    │   ├── test_seeds.py
    │   └── test_storage_atomic.py
    ├── contract/
    │   ├── test_openapi_shape.py     # response-schema conformance
    │   └── test_feature_config.py    # JSON-Schema validation of feature_config.json
    ├── integration/
    │   ├── test_train_smoke.py       # trainer end-to-end on synthetic series → fake GCS
    │   ├── test_predict_smoke.py     # service end-to-end on a freshly trained model
    │   ├── test_auth.py              # OIDC verify, audience, allowlist
    │   └── test_scenario_overrides.py
    └── api/
        └── test_api.py               # /forecast happy path, error classes, healthz/readyz
```

**Structure Decision**: Single Python project, src layout, one importable
package `forecast_sidecar`. Two entrypoints (`forecast_sidecar.main:app` for
the service, `forecast_sidecar.train_cli` for the job) built into a single
container image — no monorepo, no split packages. Testing is split into
`unit/` (fast, default-selected), `contract/` (schema-level), `integration/`
(end-to-end against fakes), and `api/` (FastAPI TestClient).

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations. Table intentionally empty.
