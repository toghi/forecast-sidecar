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

## 3. Run the service locally (mocked auth)

```bash
export FORECAST_BUCKET=local-dev-bucket
export EXPECTED_AUDIENCE=http://localhost:8080
export AUTH_BYPASS=1                 # only honored with localhost audience + debug
export LOG_LEVEL=debug
export FORECAST_ALLOW_FILE_URLS=1    # lets the trainer accept file:// URLs
uv run uvicorn forecast_sidecar.main:app --reload --port 8080
```

In another shell, train a model from the bundled fixture:

```bash
uv run python -m forecast_sidecar.train_cli \
  --company-id="00000000-0000-0000-0000-000000000001" \
  --computed-object-id="00000000-0000-0000-0000-000000000002" \
  --history-url="file://$(pwd)/tests/fixtures/sample_history.csv" \
  --feature-config-url="file://$(pwd)/tests/fixtures/sample_feature_config.json" \
  --output-version=1
```

…then call the service:

```bash
curl -s http://localhost:8080/forecast \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/sample_request.json | jq
```

## 4. Build and run the container

```bash
docker build --build-arg GIT_SHA="$(git rev-parse HEAD)" -t forecast-sidecar .
docker run --rm -p 8080:8080 \
  -e FORECAST_BUCKET=local-dev-bucket \
  -e EXPECTED_AUDIENCE=http://localhost:8080 \
  -e AUTH_BYPASS=1 -e LOG_LEVEL=debug \
  forecast-sidecar
```

Run the trainer from the same image by overriding `CMD`:

```bash
docker run --rm forecast-sidecar \
  python -m forecast_sidecar.train_cli --help
```

## 5. Where things live

- Service entry: [src/forecast_sidecar/main.py](../../src/forecast_sidecar/main.py)
- Trainer entry: [src/forecast_sidecar/train_cli.py](../../src/forecast_sidecar/train_cli.py)
- Wire schemas: [src/forecast_sidecar/schemas.py](../../src/forecast_sidecar/schemas.py)
- Storage layer: [src/forecast_sidecar/storage.py](../../src/forecast_sidecar/storage.py)
- Model code: [src/forecast_sidecar/model/](../../src/forecast_sidecar/model/)
- Feature config schema: [contracts/feature_config.schema.json](contracts/feature_config.schema.json)
- HTTP contract: [contracts/openapi.yaml](contracts/openapi.yaml)
- Trainer contract: [contracts/train_cli.md](contracts/train_cli.md)

## 6. The constitution

`/Users/arthur/sources/forecast-sidecar/.specify/memory/constitution.md`
governs implementation choices. Skim it before opening a PR — it is
short.

## 7. Common tasks

| Task | Command |
|---|---|
| Update lockfile | `uv lock` |
| Add a dep | `uv add <pkg>` |
| Add a dev dep | `uv add --dev <pkg>` |
| Format / lint | `uv run ruff format && uv run ruff check --fix` |
| Type-check | `uv run mypy src/` |
| Run smoke training | `uv run pytest tests/integration/test_train_smoke.py -v` |
| Generate OpenAPI from running service | `curl http://localhost:8080/openapi.json | jq` |
