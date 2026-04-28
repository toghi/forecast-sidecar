# forecast-sidecar

Per-`(company_id, computed_object_id)` time-series forecasting service for the
`toolsname` backend. Built on [MLForecast](https://github.com/Nixtla/mlforecast)
+ [LightGBM](https://github.com/microsoft/LightGBM); served behind an
authenticated, internal-only HTTP API on Cloud Run; trained as a same-image
batch Job. **Dumb on purpose**: knows nothing about Postgres, RLS, scenarios,
or users — receives prepared series + features as JSON, returns calibrated
point + 80%/95% conformal forecasts.

## Stack

| Component | Choice |
|---|---|
| Language | Python ≥ 3.11 |
| Forecasting | [`mlforecast`](https://github.com/Nixtla/mlforecast) ≥ 0.13 |
| Model | [`lightgbm`](https://github.com/microsoft/LightGBM) ≥ 4.5 |
| Metrics | [`utilsforecast`](https://github.com/Nixtla/utilsforecast) ≥ 0.2 |
| HTTP | [`fastapi`](https://github.com/fastapi/fastapi) + `uvicorn[standard]` |
| Validation | [`pydantic`](https://github.com/pydantic/pydantic) ≥ 2 + `pydantic-settings` |
| Storage | `google-cloud-storage` (artifacts), `polars` (ETL), `pandas` (mlforecast boundary) |
| Auth | `google-auth` (OIDC verify) |
| Logging | `structlog` (JSON to Cloud Logging) |
| Errors | `sentry-sdk` |
| CLI | `click` (trainer) |
| Persistence | `joblib` (model serialization) |
| Tests | `pytest` + `httpx` + `freezegun` |
| Quality | `ruff` (lint+format), `mypy --strict` on `src/`, `gitleaks` (secret scan) |
| Env | `uv` (locked deps + virtualenv) |
| Local | Docker Compose + `fake-gcs-server` |
| Infra | Terraform on GCP (Cloud Run service + Job, GCS, Cloud Tasks, Secret Manager, peered VPC) |
| CI/CD | GitLab CI/CD |

## Repository layout

```text
forecast-sidecar/
├── src/forecast_sidecar/   # importable package
│   ├── main.py             # FastAPI app (lifespan, /forecast, /healthz, /readyz)
│   ├── train_cli.py        # python -m forecast_sidecar.train_cli
│   ├── auth.py             # OIDC verify + audience/allow-list
│   ├── config.py           # pydantic-settings + FR-041 startup gate
│   ├── schemas.py          # Pydantic v2 wire schemas
│   ├── storage.py          # GCS layer + atomic CAS on latest.json
│   ├── cache.py            # LRU+TTL + asyncio singleflight
│   ├── observability.py    # structlog + Sentry + trace context
│   ├── seeds.py / manifest.py
│   └── model/
│       ├── features.py     # feature_config → mlforecast kwargs
│       ├── train.py        # validate, fit, calibrate, gate, metadata
│       ├── predict.py      # load + scenario overrides + predict
│       └── baselines.py    # SeasonalNaive + Constitution-IV gate
├── tests/                  # 106 tests (unit / contract / integration / api)
├── configs/                # lightgbm_defaults.yaml
├── docs/architecture.md    # diagrams + lifecycles + constitution → code map
├── infra/                  # Terraform modules + per-env (staging/production)
├── ci/                     # GitLab CI/CD includes
├── docker/                 # entrypoints, fake-gcs seed
├── compose.yaml            # local stack (sidecar + trainer + fake-gcs)
└── specs/001-forecast-sidecar-mvp/  # spec, plan, research, contracts, tasks
```

## Architecture

The service has two execution modes built from one image: a synchronous HTTP
inference path (Cloud Run service, `ingress=internal`) and a batch training
path (Cloud Run Job). Models are stored per-`(company, CO)` as
`v{N}/model.pkl` + `metadata.json` in GCS, with an atomically-promoted
`latest.json` pointer. Inference loads on demand into an in-process cache
(LRU + TTL); training fits with conformal calibration and is gated against
a SeasonalNaive baseline (Constitution Principle IV).

For full diagrams (system context, request lifecycle, training lifecycle,
storage CAS, cache, auth, constitution → code map), see
**[docs/architecture.md](docs/architecture.md)**.

## Local development

```bash
brew install uv libomp     # libomp is a LightGBM runtime requirement on macOS
uv sync --extra dev
uv run pre-commit install
cp .env.example .env
```

Then either run the full stack via Docker Compose:

```bash
docker compose up --build
curl -s http://localhost:8080/forecast \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/sample_request.json | jq
```

…or run the service directly for fast iteration:

```bash
uv run uvicorn forecast_sidecar.main:app --reload --port 8080
```

Train a model from a fixture (without GCP creds; uses the in-memory fake-GCS):

```bash
uv run python -m forecast_sidecar.train_cli \
  --company-id="00000000-0000-0000-0000-000000000001" \
  --computed-object-id="00000000-0000-0000-0000-000000000002" \
  --history-url="file://$(pwd)/tests/fixtures/sample_history.csv" \
  --feature-config-url="file://$(pwd)/tests/fixtures/sample_feature_config.json" \
  --output-version=1
```

Run the test suite:

```bash
uv run pytest                   # fast tests
uv run pytest -m slow           # full perf + coverage-band sweep
```

For the long-form recipe, see
[specs/001-forecast-sidecar-mvp/quickstart.md](specs/001-forecast-sidecar-mvp/quickstart.md).

## Deployment

Three environments, all owned by Terraform:

| Env | Mechanism | Secrets | Reachability |
|---|---|---|---|
| Local | `docker compose up` | `.env` (gitignored) | only the host |
| Staging | Cloud Run + GCS in `{prefix}-forecast-staging` | Secret Manager | backend's VPC only |
| Production | Cloud Run + GCS in `{prefix}-forecast-production` | Secret Manager | backend's VPC only |

CI/CD is GitLab CI/CD ([`.gitlab-ci.yml`](.gitlab-ci.yml) + the includes in
[`ci/`](ci/)). Stages: `lint → test → build → iac-validate → deploy:staging
→ iac-apply:production → deploy:production → drift-check`. Merges to `main`
auto-deploy to staging; production is gated on a `vX.Y.Z` tag plus manual
approval.

## Contracts

The HTTP and CLI surfaces this service exposes are formalized in:

- HTTP: [`specs/001-forecast-sidecar-mvp/contracts/openapi.yaml`](specs/001-forecast-sidecar-mvp/contracts/openapi.yaml)
- Trainer CLI: [`specs/001-forecast-sidecar-mvp/contracts/train_cli.md`](specs/001-forecast-sidecar-mvp/contracts/train_cli.md)
- Per-`(company, CO)` feature config: [`specs/001-forecast-sidecar-mvp/contracts/feature_config.schema.json`](specs/001-forecast-sidecar-mvp/contracts/feature_config.schema.json)

The OpenAPI spec is also served live at `/docs` in non-production deploys.

## Constitution

Implementation choices are governed by
[`.specify/memory/constitution.md`](.specify/memory/constitution.md). Skim
the five principles before opening a PR; the architecture doc maps each
principle to the specific module that enforces it.

## Status

MVP runtime complete (US1–US4). Phases 8 (infrastructure) and 9 (polish)
land the deploy pipeline + cross-cutting hardening. See
[`specs/001-forecast-sidecar-mvp/tasks.md`](specs/001-forecast-sidecar-mvp/tasks.md)
for progress.
