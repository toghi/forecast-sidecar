# Quickstart: Forecast Sidecar

For first-time contributors. Assumes `uv` is installed (`brew install uv`).

## 1. Clone and install

```bash
git clone <repo-url> forecast-sidecar
cd forecast-sidecar
uv sync                       # creates .venv, installs locked deps
uv run pre-commit install
```

## 2. Run the test suite

```bash
uv run pytest                 # fast tests; default selection
uv run pytest -m slow         # full integration smoke (5–15s)
```

The integration tests use an in-memory GCS fake — no GCP credentials
required.

## 3. Run the full stack locally (Docker Compose)

The recommended local flow. Boots the inference service, the trainer
container, and an in-cluster `fake-gcs-server` so no GCP creds are
needed.

```bash
cp .env.example .env                 # fill in any local-only overrides
docker compose up --build
```

Compose brings up:

| Service | Port | Purpose |
|---|---|---|
| `sidecar` | 8080 | FastAPI inference service (`forecast_sidecar.main:app`) |
| `trainer` | – | One-shot trainer; runs once at startup against the fixture |
| `fake-gcs` | 4443 | `fsouza/fake-gcs-server` emulating GCS for local artifacts |

After compose is up, the fixture-trained model is already in the local
bucket. Call the service:

```bash
curl -s http://localhost:8080/forecast \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/sample_request.json | jq
```

To re-train against a different fixture:

```bash
docker compose run --rm trainer \
  python -m forecast_sidecar.train_cli \
    --company-id="00000000-0000-0000-0000-000000000001" \
    --computed-object-id="00000000-0000-0000-0000-000000000002" \
    --history-url="file:///fixtures/sample_history.csv" \
    --feature-config-url="file:///fixtures/sample_feature_config.json" \
    --output-version=2
```

## 4. Run the service without Docker (uv)

When you want fast iteration on Python code only:

```bash
cp .env.example .env
uv sync
uv run uvicorn forecast_sidecar.main:app --reload --port 8080
```

`pydantic-settings` reads `.env` automatically. `AUTH_BYPASS=1` in the
example file is honored only because `EXPECTED_AUDIENCE` resolves to
`http://localhost:*` and `LOG_LEVEL=debug` — the same gate that
prevents the bypass from being usable in any cloud env.

## 5. Working on infrastructure (Terraform + GitHub Actions)

```bash
cd infra/environments/staging
terraform init    # uses GCS backend; needs gcloud creds with state-bucket access
terraform fmt -check
terraform validate
terraform plan -out=plan.tfplan
```

Apply runs in CI only (FR-035). Locally, `plan` is the only command
you should expect to run. Modules live in `infra/modules/` and are
shared between `staging` and `production` — change a module, both envs'
`plan` will move on the next CI run.

The full pipeline lives under `.github/workflows/`: `lint.yml`,
`test.yml`, `build.yml`, `iac.yml`, `deploy-staging.yml` (auto on push
to `main`), `deploy-production.yml` (manual, `vX.Y.Z` tag-gated via a
GitHub Environment with required reviewers), `drift-check.yml`
(scheduled). Authentication to GCP is via Workload Identity Federation
(no JSON keys).

## 6. Where things live

- **Architecture overview**: [docs/architecture.md](../../docs/architecture.md) — start here for the mental model
- **README**: [README.md](../../README.md) — landing page; links here and to the contracts below
- Service entry: [src/forecast_sidecar/main.py](../../src/forecast_sidecar/main.py)
- Trainer entry: [src/forecast_sidecar/train_cli.py](../../src/forecast_sidecar/train_cli.py)
- Wire schemas: [src/forecast_sidecar/schemas.py](../../src/forecast_sidecar/schemas.py)
- Storage layer: [src/forecast_sidecar/storage.py](../../src/forecast_sidecar/storage.py)
- Model code: [src/forecast_sidecar/model/](../../src/forecast_sidecar/model/)
- Local stack: [compose.yaml](../../compose.yaml), [docker/](../../docker/)
- Infra: [infra/modules/](../../infra/modules/), [infra/environments/](../../infra/environments/)
- CI/CD: [.github/workflows/](../../.github/workflows/)
- Env contract: [.env.example](../../.env.example)
- Feature config schema: [contracts/feature_config.schema.json](contracts/feature_config.schema.json)
- HTTP contract: [contracts/openapi.yaml](contracts/openapi.yaml)
- Trainer contract: [contracts/train_cli.md](contracts/train_cli.md)

## 7. The constitution

`/Users/arthur/sources/forecast-sidecar/.specify/memory/constitution.md`
governs implementation choices. Skim it before opening a PR — it is
short.

## 8. Common tasks

| Task | Command |
|---|---|
| Update lockfile | `uv lock` |
| Add a dep | `uv add <pkg>` |
| Add a dev dep | `uv add --dev <pkg>` |
| Format / lint | `uv run ruff format && uv run ruff check --fix` |
| Type-check | `uv run mypy src/` |
| Run smoke training | `uv run pytest tests/integration/test_train_smoke.py -v` |
| Generate OpenAPI from running service | `curl http://localhost:8080/openapi.json | jq` |
| Local stack up | `docker compose up --build` |
| Local stack down | `docker compose down -v` (drops fake-gcs volume) |
| Terraform plan (staging) | `cd infra/environments/staging && terraform plan` |
| Terraform plan (production) | `cd infra/environments/production && terraform plan` |
| Run secret scan locally | `gitleaks detect --redact -v` |
| Run link check locally | `lychee README.md docs/ specs/` |
