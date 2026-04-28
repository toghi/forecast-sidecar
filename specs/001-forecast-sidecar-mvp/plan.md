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
- Docs CI: `lychee` (relative + external link-checker) for `README.md` + `docs/` (FR-029, SC-013)
- Infra: Terraform ≥ 1.7 (`hashicorp/google` provider); `terraform fmt`/`validate`/`plan` in CI; state in GCS backend per env (FR-031, FR-036)
- CI/CD: GitHub Actions (`.github/workflows/*.yml`); `gitleaks` for secret scan; Docker Buildx for image build; `gcloud` for Cloud Run deploys (FR-033, SC-017)
- Local stack: Docker Compose (`compose.yaml`) + `fake-gcs-server` (Apache 2.0) for in-cluster GCS emulation (FR-032, FR-037, SC-014)

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
- Cloud Run ingress = `internal` on staging + production, reached via **Direct VPC egress + VPC peering** (clarification 2026-04-28); Cloud NAT for non-Google outbound, Private Google Access for GCS/Secret Manager/JWKS (FR-038, FR-039)
- `ALLOWED_CALLERS` MUST be non-empty in staging + production; service refuses to start otherwise; Terraform refuses to plan/apply if unset (FR-041)
- Versions retained: 10 most-recent per `(company, CO)`; trainer prunes after successful promotion (FR-042)
- Trainer queue concurrency cap: 5 (production), 2 (staging) (FR-043)
- LightGBM `deterministic=True`, fixed `seed`, fixed `num_threads` (constitution Principle I)
- One Docker image, two entrypoints (`uvicorn` for service, `python -m forecast_sidecar.train_cli` for job)
- Inference identity: `roles/storage.objectViewer`; trainer identity: `roles/storage.objectAdmin` — no privilege overlap
- Secrets in cloud envs ONLY via Secret Manager → Cloud Run env binding; local env via gitignored `.env` (FR-036)

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

**Documentation FRs (FR-027 → FR-030)**: not constitution-bound, but
shaped by the constitution. `docs/architecture.md` includes a section
"How the constitution is realized in the code" mapping each principle to
the specific module / file / config that enforces it (e.g. Principle I →
`seeds.py` + `manifest.py`; Principle II → `model/features.py`; etc.) so
reviewers can spot drift without re-reading the constitution.

**Operations & Infrastructure FRs (FR-031 → FR-043)**: re-evaluated
against all five principles after their addition; no violations.
- *Reproducibility (I)*: Terraform-managed everything (FR-031) plus
  immutable container tags (FR-035 production-deploy gate) plus
  per-env state buckets (FR-036) reinforce reproducibility — they make
  *infrastructure* state addressable the same way Principle I makes
  *model* state addressable. `terraform plan` is the infra equivalent
  of the constitution's run manifest.
- *Temporal Integrity (II)*: not affected — these FRs are about how the
  service is reachable and configured, not about how features are
  computed.
- *Data Contract (III)*: extended to the env-var surface — `.env.example`
  is the documented contract for env vars, validated against
  `Settings.model_fields` in CI (SC-020). This is a strict generalization
  of Principle III to operational config.
- *Baseline-Beating Evaluation (IV)*: not affected.
- *Configuration Over Code (V)*: strongly reinforced — three envs differ
  only in `terraform.tfvars` and Secret Manager contents (FR-032,
  FR-036). Code paths are identical across envs; no `if env == "prod"`
  branches are permitted. Clarification additions FR-041 (allow-list
  required in cloud envs), FR-042 (10-version retention), and FR-043
  (queue concurrency cap 5/2) are all enforced through `terraform.tfvars`
  + Terraform validation blocks, not in-code branches.

The defense-in-depth requirement (FR-040 — OIDC still required from
inside the VPC) does not interact with the constitution but is captured
in the architecture doc's auth section per FR-028.

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
├── .dockerignore              # excludes .env, infra/.terraform/, etc.
├── .gitignore                 # excludes .env, infra/.terraform/, *.tfstate*
├── .gitleaks.toml             # SC-017: secret-scanning rules
├── .lychee.toml               # FR-029/SC-013: link-check config
├── .env.example               # FR-036: enumerates every var the app reads (committed)
├── README.md                  # FR-027: stack, layout, local dev, deploy, contract pointers; links to docs/architecture.md
├── docs/
│   └── architecture.md        # FR-028: system-context diagram, request + training lifecycles, storage/cache/auth, constitution mapping, contract links
├── .github/
│   └── workflows/             # FR-033: GitHub Actions
│       ├── lint.yml           # ruff, mypy, gitleaks, terraform fmt, lychee
│       ├── test.yml           # pytest (fast on PRs; full on main + tags)
│       ├── build.yml          # docker buildx → push to Artifact Registry
│       ├── iac.yml            # terraform fmt -check, validate, plan (uploaded as artifact for PR review)
│       ├── deploy-staging.yml # auto on push to main
│       ├── deploy-production.yml # manual on vX.Y.Z tag (production Environment with required reviewers)
│       └── drift-check.yml    # scheduled (24h cadence) — terraform plan -detailed-exitcode
├── compose.yaml               # FR-032/FR-037: local stack — sidecar + trainer + fake-gcs-server
├── docker/
│   ├── service.entrypoint.sh  # uvicorn launcher used by the service container
│   ├── trainer.entrypoint.sh  # python -m forecast_sidecar.train_cli wrapper
│   └── fake-gcs/
│       └── seed.sh            # pre-creates the local "bucket" and uploads the fixture
├── infra/                     # FR-031: Terraform; all GCP resources here
│   ├── modules/
│   │   ├── cloud_run_service/ # service + Secret Manager wiring + ingress=internal
│   │   ├── cloud_run_job/     # trainer job + IAM
│   │   ├── gcs_bucket/        # versioned, lifecycle rules, IAM bindings
│   │   ├── artifact_registry/ # one repo per env
│   │   ├── cloud_tasks/       # trainer-trigger queue
│   │   ├── secret_manager/    # secret + accessor binding
│   │   ├── network/           # Direct VPC egress + VPC peering + Cloud NAT + Private Google Access (FR-039)
│   │   └── iam/               # least-privilege roles per FR-026
│   └── environments/
│       ├── staging/
│       │   ├── main.tf
│       │   ├── backend.tf     # GCS state, project gs://{prefix}-tfstate-staging
│       │   ├── variables.tf
│       │   └── terraform.tfvars  # non-secret env settings
│       └── production/
│           ├── main.tf
│           ├── backend.tf     # GCS state, project gs://{prefix}-tfstate-production
│           ├── variables.tf
│           └── terraform.tfvars
├── .pre-commit-config.yaml    # ruff, mypy, pytest -m "not slow and not gpu", terraform fmt
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
