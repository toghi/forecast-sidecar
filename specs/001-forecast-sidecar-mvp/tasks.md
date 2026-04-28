---
description: "Task list for forecast-sidecar MVP feature implementation"
---

# Tasks: Forecast Sidecar MVP

**Input**: Design documents from `specs/001-forecast-sidecar-mvp/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: REQUIRED (not optional). The constitution's pre-commit and CI
gates mandate pytest, schema validation, and the baseline-beating gate;
spec acceptance scenarios map directly to integration and API tests.

**Organization**: Tasks are grouped by user story so each story can be
implemented, tested, and demonstrated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- All file paths are project-relative; absolute paths are used only inside the tasks themselves

## Path Conventions

Single project, src layout (per [plan.md](plan.md) §Project Structure):

- App: `src/forecast_sidecar/`
- Tests: `tests/{unit,contract,integration,api,fakes,fixtures}/`
- Configs: `configs/`
- Local stack: `compose.yaml`, `docker/`
- Infra: `infra/{modules,environments/{staging,production}}/`
- CI: `.gitlab-ci.yml`, `ci/`
- Docs: `README.md`, `docs/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Repository scaffolding, dependency installation, lint/type/test config, local-stack skeleton. No business logic yet.

- [X] T001 Create the full directory tree: `src/forecast_sidecar/model/`, `tests/{unit,contract,integration,api,fakes,fixtures}/`, `configs/`, `docker/fake-gcs/`, `infra/modules/{cloud_run_service,cloud_run_job,gcs_bucket,artifact_registry,cloud_tasks,secret_manager,network,iam}/`, `infra/environments/{staging,production}/`, `ci/`, `docs/`
- [X] T002 Initialize the uv project at `pyproject.toml` (project name `forecast-sidecar`, Python `>=3.11,<3.12`, src layout, package `forecast_sidecar`)
- [X] T003 Add runtime deps to `pyproject.toml`: `uv add mlforecast lightgbm utilsforecast fastapi 'uvicorn[standard]' 'pydantic>=2' pydantic-settings google-cloud-storage google-auth structlog sentry-sdk polars pandas pyarrow joblib click cachetools`
- [X] T004 Add dev deps to `pyproject.toml`: `uv add --dev pytest pytest-asyncio httpx freezegun ruff mypy types-cachetools jsonschema`
- [X] T005 Add `[tool.ruff]` and `[tool.ruff.lint]` config to `pyproject.toml` (line-length 100, target-version py311, rules E F I N UP B S RUF, `per-file-ignores` for `tests/*` to allow `S101` asserts)
- [X] T006 Add `[tool.mypy]` config to `pyproject.toml` (strict mode, `files = ["src"]`, ignore_missing_imports for mlforecast/lightgbm/utilsforecast/google.* until stubs are bundled)
- [X] T007 Add `[tool.pytest.ini_options]` to `pyproject.toml` declaring markers `slow`, `gpu`, `integration`, `requires_data`; default `addopts = "-m 'not slow and not gpu'"`
- [X] T008 [P] Create `.gitignore` with `.env`, `.venv`, `__pycache__/`, `**/__pycache__/`, `*.pyc`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `infra/**/.terraform/`, `infra/**/.terraform.lock.hcl` (commit), `*.tfplan`, `*.tfstate*`, `models/`, `data/`, `htmlcov/`, `.coverage`
- [X] T009 [P] Create `.dockerignore` excluding `.git/`, `.venv/`, `__pycache__/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `tests/`, `infra/`, `docs/`, `specs/`, `.env`, `*.tfstate*`
- [X] T010 [P] Create `.pre-commit-config.yaml` with hooks: `ruff format`, `ruff check --fix`, `mypy --strict src/`, `pytest -m "not slow and not gpu"`, `terraform fmt -recursive infra/`, `gitleaks detect --staged --redact`
- [X] T011 [P] Create `.env.example` listing every variable from data-model §4.1 with placeholder values and one-line comments (`PORT`, `FORECAST_BUCKET`, `EXPECTED_AUDIENCE`, `ALLOWED_CALLERS`, `MODEL_CACHE_SIZE`, `MODEL_CACHE_TTL_SECONDS`, `LATEST_POINTER_TTL_SECONDS`, `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `LOG_LEVEL`, `AUTH_BYPASS`, `GIT_SHA`, `FORECAST_ALLOW_FILE_URLS`, `GCS_FAKE_HOST`)
- [X] T012 [P] Create `configs/lightgbm_defaults.yaml` with the default LGBMRegressor params from data-model §4.2 (`n_estimators: 500`, `learning_rate: 0.05`, `num_leaves: 31`, `max_depth: -1`, `min_data_in_leaf: 20`, `objective: regression`, `metric: mae`, `verbosity: -1`)
- [X] T013 [P] Create empty `src/forecast_sidecar/__init__.py` and `src/forecast_sidecar/model/__init__.py`

**Checkpoint**: `uv sync` succeeds; `uv run ruff check .` and `uv run mypy src/` pass on empty package; `uv run pytest` discovers zero tests.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Cross-cutting modules and test infrastructure that every user story consumes. After this phase, the FastAPI app boots in docker compose, but no public routes exist yet.

**⚠️ CRITICAL**: No user-story phase (3+) may begin until Phase 2 completes.

### Cross-cutting code modules

- [X] T014 [P] Implement `set_seed(seed: int) -> None` in `src/forecast_sidecar/seeds.py` (seeds `random`, `numpy.random`, environment variable for LightGBM)
- [X] T015 [P] Implement run-manifest builder in `src/forecast_sidecar/manifest.py` (functions: `compute_data_hash(bytes) -> str`, `compute_config_hash(dict) -> str`, `library_versions() -> dict[str, str]`, `build_manifest(...) -> dict`; uses `importlib.metadata.version`)
- [X] T016 Implement Settings class in `src/forecast_sidecar/config.py` using `pydantic-settings.BaseSettings` with all fields from data-model §4.1; `model_config = SettingsConfigDict(env_file=".env", extra="ignore")`; module-level `get_settings()` (lru_cache) accessor; add a `model_validator(mode="after")` that raises `ConfigurationError` when the resolved `EXPECTED_AUDIENCE` is **not** a `localhost:*` URL AND `ALLOWED_CALLERS` is empty (FR-041 startup gate); the FastAPI lifespan from T022 catches this and exits non-zero before binding any port
- [X] T017 [P] Implement Pydantic v2 wire schemas in `src/forecast_sidecar/schemas.py`: `ForecastRequest`, `FuturePeriodFeatures`, `ForecastResponse`, `ForecastPoint` (with `lo95 ≤ lo80 ≤ point ≤ hi80 ≤ hi95` validator), `ModelMetricsSummary`, `ErrorResponse`, `HealthResponse`, `ReadyResponse` per data-model §1
- [X] T018 [P] Implement structlog + Sentry + trace-context bootstrap in `src/forecast_sidecar/observability.py` (functions: `init_structlog(level)`, `init_sentry(dsn, environment, release)`, `extract_trace_context(headers) -> dict`, `tag_sentry_scope(company_id, computed_object_id, mode)` to set scope tags on the current `sentry_sdk.Hub`); cover with a unit test in `tests/unit/test_observability.py` that captures an exception with `tag_sentry_scope` active and asserts the resulting event has `tags = {company_id, computed_object_id, mode}` (FR-025 / SC-008)
- [X] T019 Implement OIDC verifier in `src/forecast_sidecar/auth.py` as a FastAPI dependency `verify_oidc_token(request, settings) -> Claims`: extract `Authorization: Bearer`, run `google.oauth2.id_token.verify_oauth2_token` via `asyncio.to_thread`, check `aud`, check optional `email` allow-list, honor `AUTH_BYPASS` only when `EXPECTED_AUDIENCE` starts with `http://localhost` AND `LOG_LEVEL=debug` (depends on T016)
- [X] T020 Implement GCS storage layer in `src/forecast_sidecar/storage.py` (depends on T016): `read_history(url) -> polars.DataFrame`, `read_feature_config(url) -> dict`, `read_model_pkl(bucket, path) -> bytes`, `read_metadata(bucket, path) -> dict`, `read_latest_pointer(bucket, company, co) -> dict | None`, `write_artifact_bundle(bucket, company, co, version, model_bytes, metadata)`, `atomic_promote_latest(bucket, company, co, version, expected_generation) -> bool` (CAS via `If-Generation-Match`), `file_url_supported(url) -> bool` (gated by `FORECAST_ALLOW_FILE_URLS`)
- [X] T021 Implement model cache in `src/forecast_sidecar/cache.py` (depends on T016, T020): two `cachetools.TTLCache` instances — `_model_cache` keyed `(company, co, version)` with `ttl=MODEL_CACHE_TTL_SECONDS`, `_latest_cache` keyed `(company, co)` with `ttl=LATEST_POINTER_TTL_SECONDS=60`; per-key `asyncio.Lock` (singleflight) so concurrent first-loads do not double-fetch
- [X] T022 Implement FastAPI app skeleton in `src/forecast_sidecar/main.py` (depends on T016, T018, T020, T021): factory `create_app() -> FastAPI`, async `lifespan(app)` that initializes structlog, Sentry, GCS client, and the cache and stores them on `app.state`; FastAPI dependencies `get_settings`, `get_storage`, `get_cache`; `app = create_app()` at module level for `uvicorn` discovery; NO public routes yet
- [X] T023 [P] Implement feature-config → mlforecast args mapping in `src/forecast_sidecar/model/features.py`: `build_mlforecast_kwargs(feature_config: dict) -> dict` returning `{lags, lag_transforms, date_features, target_transforms, freq, static_features}` correctly typed for mlforecast; `categorical_columns(feature_config) -> list[str]`; `infer_seasonality(freq) -> int` for SeasonalNaive

### Test infrastructure

- [X] T024 [P] Implement in-memory GCS fake in `tests/fakes/gcs.py`: minimal `Bucket`/`Blob` stand-ins implementing `upload_from_string`, `download_as_bytes`, `exists`, `generation`, and `if_generation_match` semantics that mirror real GCS for the CAS used by T020
- [X] T025 [P] Create synthetic-series fixture generator in `tests/conftest.py` returning a deterministic 24-month × 3-series Polars DataFrame with a known seasonal+trend pattern + Gaussian noise (fixed seed); also expose `fake_storage` fixture wrapping T024
- [X] T026 [P] Create `tests/fixtures/sample_history.csv` (24 monthly periods × 3 series, columns `unique_id, ds, y, segment, region, calls, active_clients, bizdev_id`)
- [X] T027 [P] Create `tests/fixtures/sample_feature_config.json` matching `contracts/feature_config.schema.json` with `target=y`, monthly freq, lags [1,3,6,12], static_features [segment, region], future_exog [active_clients, bizdev_id]
- [X] T028 [P] Create `tests/fixtures/sample_request.json` — a valid `ForecastRequest` for the fixture model (12-period horizon, future_features for each period)

### Foundational tests (must pass before any user-story work)

- [X] T029 [P] Unit tests for `set_seed` in `tests/unit/test_seeds.py` (asserts numpy.random reproducibility, env-var setting)
- [X] T030 [P] Unit tests for manifest hashing in `tests/unit/test_manifest.py` (deterministic hash, version capture)
- [X] T031 [P] Unit tests for cache TTL+LRU semantics in `tests/unit/test_cache.py` using `freezegun` (eviction by size, expiry by TTL, singleflight on concurrent miss)
- [X] T032 [P] Unit tests for `storage.atomic_promote_latest` in `tests/unit/test_storage_atomic.py` against the GCS fake (race scenarios from research R4: precondition-failed → re-read → idempotent vs stale-retry vs retry-with-new-gen)
- [X] T033 [P] Contract test for feature-config schema in `tests/contract/test_feature_config.py` using `jsonschema.validate` on `tests/fixtures/sample_feature_config.json` against `specs/001-forecast-sidecar-mvp/contracts/feature_config.schema.json`
- [X] T034 [P] Unit tests for `model/features.build_mlforecast_kwargs` in `tests/unit/test_features.py` (correct lags array, lag_transforms instantiated, date_features mapped, infer_seasonality(MS)=12)
- [X] T035 [P] Unit tests for OIDC `verify_oidc_token` in `tests/unit/test_auth.py` using monkeypatched `verify_oauth2_token` (valid → claims; bad audience → 401; not-in-allowlist → 401; bypass honored only with localhost+debug; bypass refused with cloud audience)
- [X] T035a [P] Unit tests for `Settings` startup gate in `tests/unit/test_config.py`: instantiating `Settings` with a non-localhost `EXPECTED_AUDIENCE` AND empty `ALLOWED_CALLERS` raises `ConfigurationError` (FR-041); same audience with non-empty allow-list constructs cleanly; localhost audience with empty allow-list constructs cleanly (local-dev path)

**Checkpoint**: `docker compose up` boots the sidecar container with `/healthz` only (no auth), all foundational unit + contract tests pass, fake-gcs has the seeded fixture bucket.

---

## Phase 3: User Story 1 — On-Demand Forecast (Priority: P1) 🎯 MVP

**Goal**: Authenticated `POST /forecast` endpoint returns point + 80%/95% interval forecasts for a previously persisted `(company, CO)` model.

**Independent Test**: With a fixture model present in fake-GCS, send a valid `ForecastRequest` for 12 periods; assert response shape conforms to `contracts/openapi.yaml`, intervals widen with horizon, `lo95 ≤ lo80 ≤ point ≤ hi80 ≤ hi95`, and unauthenticated requests are rejected with 401.

### Tests for User Story 1 ⚠️ Write first, ensure they FAIL before implementation

- [X] T036 [P] [US1] Contract test in `tests/contract/test_openapi_shape.py` — load `specs/001-forecast-sidecar-mvp/contracts/openapi.yaml`, schema-validate a fixture `ForecastResponse` against `#/components/schemas/ForecastResponse`
- [X] T037 [P] [US1] API test in `tests/api/test_forecast_happy.py` using `httpx.AsyncClient` against `app` (lifespan on, AUTH_BYPASS on, fake-GCS): seed a pickled fixture `MLForecast` model, POST `sample_request.json`, assert 200 + valid response + interval invariants. Additionally send `X-Cloud-Trace-Context: <trace>/<span>;o=1` and assert the same trace id appears in the captured structlog output for that request (FR-024 propagation half).
- [X] T038 [P] [US1] API test in `tests/api/test_forecast_errors.py` covering: 401 (no token), 401 (bad audience, allowed_callers mismatch), 401 (simulated allowlisted SA token but no `Authorization` header — FR-040 defense-in-depth: VPC reachability does not bypass OIDC), 400 (missing future_exog column with `missing_columns` populated), 400 (horizon vs future_features row mismatch with `expected_rows`/`actual_rows`), 404 with `error="not_yet_trained"` (no `latest.json` for the pair), 404 with `error="model_not_found"` (explicit `model_version=N` missing while `latest.json` exists), 409 with `error="model_not_ready"` (only `error.json` present, no `model.pkl`), 503 (GCS raises ServiceUnavailable). Distinct codes per the FR-006 six-class taxonomy.
- [X] T039 [P] [US1] Integration test in `tests/integration/test_predict_smoke.py`: train a small fixture model in-test (using a stub trainer that writes `model.pkl`+`metadata.json`), then call `/forecast`; assert end-to-end correctness against the synthetic series. Add a second scenario (using `freezegun`): seed `v1`, hit `/forecast` (warm cache); promote `v2` in fake-GCS; advance time past `LATEST_POINTER_TTL_SECONDS` (60 s); hit `/forecast` again with `model_version` omitted; assert the response now resolves to `v2` without service restart (SC-009).

### Implementation for User Story 1

- [X] T040 [US1] Implement `model/predict.py` `load_or_fetch(cache, storage, company, co, version) -> tuple[MLForecast, metadata_dict]` (depends on T020, T021): resolves `latest.json` if version is None, populates cache, raises typed exceptions `NotYetTrainedError` (no `latest.json` AND no explicit version requested), `ModelNotFoundError` (explicit version `N` requested but `v{N}/model.pkl` absent), `ModelNotReadyError` (`v{N}/error.json` present, no `model.pkl`), and `StorageUnavailableError`. The route handler in T042 maps these to FR-006's error enum (`not_yet_trained` / `model_not_found` / `model_not_ready` / `storage_unavailable`).
- [X] T041 [US1] Implement `model/predict.py` `predict(model, metadata, request) -> ForecastResponse`: validates `future_features` columns against `metadata.feature_config.future_exog` (raises `MissingColumnsError` with the offending columns), converts to pandas at the mlforecast boundary, calls `MLForecast.predict(h=horizon, X_df=future_df, level=[80, 95])`, reshapes to `ForecastPoint` list
- [X] T042 [US1] Wire `POST /forecast` in `src/forecast_sidecar/main.py` (depends on T019, T022, T040, T041): route uses `verify_oidc_token` dependency, maps typed exceptions to the `ErrorResponse` taxonomy from data-model §1.6 (`bad_request`/`invalid_token`/`model_not_found`/`model_not_ready`/`storage_unavailable`), structlogs request_id + company_id + co_id + model_version + cache_hit + latency_ms + status (FR-023)

**Checkpoint**: User Story 1 fully functional and testable independently. T036–T039 all green. The integration test in T039 validates the FR-023 log contract by capturing structlog output.

---

## Phase 4: User Story 2 — Train New Model Version (Priority: P1)

**Goal**: A `python -m forecast_sidecar.train_cli` invocation reads staged history + feature_config from a URL, fits an MLForecast(LGBMRegressor) model with conformal calibration, evaluates against a SeasonalNaive baseline, and atomically promotes a new version artifact in GCS — or writes `error.json` and exits non-zero.

**Independent Test**: Invoke the CLI against `tests/fixtures/sample_history.csv` + `sample_feature_config.json` writing to fake-GCS; assert `v1/model.pkl`, `v1/metadata.json`, and `latest.json` all exist; assert metadata has model + baseline metrics, coverage in band, `improvement_smape_pct >= 10`. Re-run with the same `--output-version` and assert idempotent success without partial state.

### Tests for User Story 2 ⚠️ Write first

- [X] T043 [P] [US2] Unit test in `tests/unit/test_baselines.py`: SeasonalNaive forecasts match hand-computed expected values; sMAPE-gate raises when model loses to baseline by ≥ 10%
- [X] T044 [P] [US2] Integration test in `tests/integration/test_train_smoke.py`: end-to-end CLI run against the synthetic-series fixture writing to fake-GCS; assert artifact tree, metadata content (training_window, feature_config_hash, data_hash, library_versions, git_sha, manifest_hash), `latest.json` updated, `improvement_smape_pct >= 10` (constitution IV gate)
- [X] T045 [P] [US2] Integration test in `tests/integration/test_train_idempotent.py`: run CLI twice with same `--output-version`, assert second run succeeds (exit 0) with no partial writes and `latest.json` stable
- [X] T046 [P] [US2] Integration test in `tests/integration/test_train_failure_modes.py`: (a) history with all-NaN target → exit 3 + `error.json`, no `latest.json` update; (b) history with < `min_history_periods` → exit 2; (c) calibration regression scenario (force model to lose to baseline) → exit 4 + artifact written but `latest.json` unchanged
- [X] T047 [P] [US2] Integration test in `tests/integration/test_train_promotion_race.py`: simulate two writers racing the CAS → loser exits 5 cleanly with `latest.json` at the higher version
- [X] T047a [P] [US2] Integration test in `tests/integration/test_train_retention.py`: run 11 successful trainings against the synthetic fixture with `--output-version=1..11`; assert versions `v2..v11` and `latest.json` survive while `v1/` is pruned (FR-042); separately, force `latest.json` to point at `v3` and run a 12th promotion; assert `v3/` survives the prune even though `K=3 < N-9=12-9=3` boundary (defensive guarantee per FR-042)

### Implementation for User Story 2

- [X] T048 [P] [US2] Implement `model/baselines.py`: `seasonal_naive_forecast(series_df, h, season_length) -> df`, `compute_metrics(y_true, y_pred, levels=[80,95]) -> dict` using `utilsforecast.evaluation`, `enforce_baseline_gate(model_smape, baseline_smape, threshold=0.9) -> bool`
- [X] T049 [US2] Implement `model/train.py` `fit_model(history_df, feature_config) -> tuple[MLForecast, metadata]` (depends on T023, T014, T015): build mlforecast kwargs via `features.build_mlforecast_kwargs`, instantiate `LGBMRegressor` with `deterministic=True`, `seed`, `num_threads`, params overlaid on `configs/lightgbm_defaults.yaml`, fit with `prediction_intervals=PredictionIntervals(n_windows=10, h=horizon)`, then run `cross_validation` to compute coverage; assemble `metadata` dict per data-model §2.3
- [X] T050 [US2] Implement `model/train.py` `validate_history(history_df, feature_config) -> None` (depends on T020): enforces gap_policy, monotonic ds per unique_id, no duplicate `(unique_id, ds)`, freq-match, `n_periods >= min_history_periods`; raises `BadHistoryError` with offending rows for exit 2
- [X] T051 [US2] Implement `train_cli.py` (depends on T014, T015, T048, T049, T050, T020): Click command with the flags from `contracts/train_cli.md`; exit-code taxonomy 0/1/2/3/4/5; on success, joblib-dump model, write `metadata.json`, then `atomic_promote_latest`; on failure, write `error.json` and skip promotion; structlog phases `download/validate/fit/calibrate/upload/prune/done`. After successful promotion to version `N`, prune every `v{K}/` where `K < N - 9` AND `K` is not the version named by `latest.json` (FR-042); pruning errors are WARN-logged but non-fatal.
- [X] T052 [US2] Add `python -m forecast_sidecar.train_cli` resolution by creating `src/forecast_sidecar/__main__.py` that delegates to `train_cli.main()` (so `python -m forecast_sidecar.train_cli` works)
- [X] T053 [US2] Add training-job entrypoint script `docker/trainer.entrypoint.sh` (sources `.env` if present, execs `python -m forecast_sidecar.train_cli "$@"`)

**Checkpoint**: User Story 2 fully functional. CLI exits with the documented codes; `latest.json` is never torn; all five integration tests green. Together US1 + US2 form the MVP.

---

## Phase 5: User Story 3 — Scenario "What-If" Forecast (Priority: P2)

**Goal**: `/forecast` accepts a `scenario_overrides` payload that substitutes per-period feature values for what-if analysis without mutating the model or other periods.

**Independent Test**: Issue two requests with the same `(company, CO, horizon, future_features)` — one with overrides for one period+feature, one without — and assert the overridden period differs while every other period matches bit-for-bit.

### Tests for User Story 3 ⚠️ Write first

- [ ] T054 [P] [US3] Integration test in `tests/integration/test_scenario_overrides.py`: baseline vs override for `period=P5, feature=active_clients` → only P5 differs; empty `scenario_overrides` → identical to baseline; override naming a feature outside `future_exog` → 400 with explicit detail

### Implementation for User Story 3

- [ ] T055 [US3] Extend `model/predict.py` `predict()` (depends on T041) to accept `scenario_overrides`; before passing `future_df` to `MLForecast.predict`, validate every override key is in `metadata.feature_config.future_exog` (raise `BadScenarioOverrideError` → 400 if not), then in-place apply each `{period: {feature: value}}` to the `future_df`
- [ ] T056 [US3] Update `src/forecast_sidecar/schemas.py` `ForecastRequest.scenario_overrides` type if needed and add a `model_validator` ensuring all override-period keys appear in `future_features` periods (raise 400 with `bad_request` detail otherwise)

**Checkpoint**: User Story 3 fully functional. T054 green. US1 still passes (override path is opt-in via the `scenario_overrides` field).

---

## Phase 6: User Story 4 — Health and Readiness (Priority: P2)

**Goal**: Liveness and readiness probes available without auth so a load balancer / Cloud Run probe can route traffic safely; readiness reflects GCS reachability and cache state.

**Independent Test**: `GET /healthz` returns 200 OK without consulting GCS; `GET /readyz` returns 200 + `models_cached` when GCS reachable, 503 when GCS unreachable.

### Tests for User Story 4 ⚠️ Write first

- [ ] T057 [P] [US4] API test in `tests/api/test_health_ready.py`: `/healthz` returns `{"status":"ok"}` with no auth; `/readyz` returns 200 with `gcs_reachable: true` and `models_cached: 0` on a fresh app; `/readyz` returns 503 when GCS client raises on a cheap probe call

### Implementation for User Story 4

- [ ] T058 [US4] Implement `GET /healthz` in `src/forecast_sidecar/main.py` (depends on T022) — no auth dep, returns `HealthResponse(status="ok")`
- [ ] T059 [US4] Implement `GET /readyz` in `src/forecast_sidecar/main.py` — no auth dep, performs a cheap GCS probe (e.g. `bucket.exists()` against `FORECAST_BUCKET` with a 1s timeout) wrapped in `asyncio.to_thread`; returns `ReadyResponse(status, gcs_reachable, models_cached=len(cache._model_cache))`; on failure returns 503 with the same shape

**Checkpoint**: All four user stories independently functional. The local `compose.yaml` healthcheck targets `/healthz`; the readiness probe is wired in Cloud Run by Phase 8.

---

## Phase 7: Documentation Deliverables (FR-027 → FR-030, SC-012 / SC-013)

**Purpose**: User-facing README and architecture document required by the spec; CI check that the README→architecture link works.

- [ ] T060 [P] Author `README.md` per the section order in research R11 (summary; stack; layout; **Architecture** section linking to `docs/architecture.md`; local development; deployment; contracts; constitution pointer)
- [ ] T061 [P] Author `docs/architecture.md` per research R11's 8-section structure (system context, request lifecycle, training lifecycle, storage layout + atomic-promotion contract, cache semantics, auth & identity model, constitution → code map, out-of-scope), using Mermaid for the three diagrams
- [ ] T062 [P] Create `.lychee.toml` with relative-link checking on `README.md`, `docs/`, `specs/` and an external-link allow-list (Nixtla, microsoft/LightGBM, FastAPI, GCP docs) (FR-029, SC-013)
- [ ] T063 [P] Create `ci/lint.gitlab-ci.yml` `docs:link-check` job invoking `lycheeverse/lychee-action@v2`

**Checkpoint**: README opens to the architecture doc with one click; lychee CI fails any MR that breaks an internal link.

---

## Phase 8: Operations & Infrastructure Deliverables (FR-031 → FR-043, SC-014 → SC-020)

**Purpose**: Container, local stack, GitLab CI/CD pipeline, and Terraform modules + envs that make the service deployable to staging and production.

### Container + local stack

- [ ] T064 Create the multi-stage `Dockerfile` (per research R9): builder stage with `python:3.11-slim` + `uv` running `uv sync --frozen --no-dev`; runtime stage with `libgomp1`, copy `/app/.venv` and `/app/src`, drop to non-root `forecast` (uid 10001), `ARG GIT_SHA` → `ENV GIT_SHA`, `ENTRYPOINT []`, `CMD ["uvicorn", "forecast_sidecar.main:app", "--host", "0.0.0.0", "--port", "8080"]`
- [ ] T065 [P] Create `docker/service.entrypoint.sh` (sources `.env` if present, execs `uvicorn forecast_sidecar.main:app ...`)
- [ ] T066 [P] Create `docker/fake-gcs/seed.sh` that creates the local bucket and uploads `tests/fixtures/sample_history.csv` + `sample_feature_config.json` to it via `gsutil` against the fake-gcs endpoint
- [ ] T067 Create `compose.yaml` (FR-032, FR-037, SC-014) with services: `sidecar` (build .; ports 8080:8080; env_file .env; depends_on fake-gcs healthy), `trainer` (build .; profiles ["train"]; uses trainer entrypoint), `fake-gcs` (image `fsouza/fake-gcs-server`; ports 4443:4443; healthcheck via `/storage/v1/b`); `seed` one-shot service that runs `docker/fake-gcs/seed.sh` after fake-gcs is healthy
- [ ] T068 [P] Create `.gitleaks.toml` with default rules + an allow-list path for `tests/fixtures/`

### GitLab CI/CD pipeline

- [ ] T069 Create top-level `.gitlab-ci.yml`: declares stages `lint test build iac-validate deploy:staging iac-apply:production deploy:production drift-check`; `include:` the files in `ci/`; default workflow rules (run on MR, on `main` push, on tag push)
- [ ] T070 [P] Create `ci/lint.gitlab-ci.yml`: jobs `ruff`, `mypy`, `gitleaks`, `terraform-fmt`, `lychee` (T063 lives here)
- [ ] T071 [P] Create `ci/test.gitlab-ci.yml`: jobs `pytest:fast` (default markers, runs on every MR) and `pytest:slow` (markers `slow or integration`, runs on `main` and tags); both `uv run pytest` with junit-XML artifact upload
- [ ] T072 [P] Create `ci/build.gitlab-ci.yml`: `build:image` uses Docker Buildx, builds `--build-arg GIT_SHA=$CI_COMMIT_SHA`, pushes to staging Artifact Registry on every pipeline; on tag pipelines also pushes to production Artifact Registry
- [ ] T073 [P] Create `ci/iac.gitlab-ci.yml`: jobs `iac:fmt-check`, `iac:validate`, `iac:plan:staging`, `iac:plan:production` (each `cd infra/environments/<env> && terraform init -backend-config=... && terraform plan -out=plan.tfplan && terraform show -json plan.tfplan > plan.json`); upload `plan.tfplan` + `plan.json` as job artifacts so reviewers see the infra diff (FR-034)
- [ ] T074 [P] Create `ci/deploy.gitlab-ci.yml`: `deploy:staging` (on `main`, runs `terraform apply -auto-approve` on staging then `gcloud run deploy` + `gcloud run jobs update`), `iac-apply:production` (manual, `rules: - if: $CI_COMMIT_TAG =~ /^v\d+\.\d+\.\d+$/`, `environment: production`), `deploy:production` (manual, after iac-apply, same rules), `drift-check` (`schedule` + `terraform plan -detailed-exitcode` on both envs; non-zero exit → failure → Sentry alert)

### Terraform modules

- [ ] T075 [P] Implement `infra/modules/gcs_bucket/` (variables: name, location, lifecycle_rules; resources: `google_storage_bucket` with `versioning.enabled=true`, `uniform_bucket_level_access=true`, `force_destroy=false`)
- [ ] T076 [P] Implement `infra/modules/artifact_registry/` (one Docker repo per env, immutable tags)
- [ ] T077 [P] Implement `infra/modules/secret_manager/` (variables: secrets list; resources: `google_secret_manager_secret` per name + per-secret `secretAccessor` IAM binding to a passed-in service account email)
- [ ] T078 [P] Implement `infra/modules/cloud_tasks/` (queue for trainer fan-out): variable `max_concurrent_dispatches` (no default — must be set per env), bound via `google_cloud_tasks_queue.rate_limits.max_concurrent_dispatches`; envs set `5` (production) and `2` (staging) per FR-043
- [ ] T079 [P] Implement `infra/modules/iam/` (least-privilege role-bindings per FR-026: inference SA gets `roles/storage.objectViewer` on the bucket; trainer SA gets `roles/storage.objectAdmin` on the bucket and `roles/run.invoker` on the job; secretAccessor at the secret level via T077)
- [ ] T080 [P] Implement `infra/modules/network/` (FR-039, research R13, clarification 2026-04-28): Direct VPC egress on Cloud Run (no Serverless VPC Access connector), VPC peering between this project's VPC and the calling backend's VPC, Cloud NAT for non-Google outbound, Private Google Access on the subnet for GCS / Secret Manager / OIDC JWKS
- [ ] T081 Implement `infra/modules/cloud_run_service/` (FR-038, FR-041): `google_cloud_run_v2_service` with `ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"`, Direct VPC egress block referencing T080's network module, env block reading non-secrets from variables and binding secrets from Secret Manager via `value_source.secret_key_ref`, runtime SA from T079, container image from T076's Artifact Registry; add a `validation` block on `var.allowed_callers` requiring `length(var.allowed_callers) >= 1` so staging/production plans fail when the allow-list is empty (FR-041)
- [ ] T082 Implement `infra/modules/cloud_run_job/` mirroring T081 for the trainer (no ingress; image + entrypoint override + SA + secret bindings; max-retries=1; task-timeout=30m per spec §12.2)

### Terraform environments

- [ ] T083 Implement `infra/environments/staging/{main.tf, backend.tf, variables.tf, terraform.tfvars}` invoking modules T075–T082 with staging values; `backend.tf` uses `gs://{prefix}-tfstate-staging/forecast-sidecar/` (FR-036)
- [ ] T084 Implement `infra/environments/production/{main.tf, backend.tf, variables.tf, terraform.tfvars}` mirroring staging with production values and `gs://{prefix}-tfstate-production/forecast-sidecar/`

### CI-side compliance checks

- [ ] T085 [P] Add `ci/lint.gitlab-ci.yml` `env-contract` job that runs a small `tests/contract/test_env_contract.py` introspecting `Settings.model_fields` and asserting every field has a matching `KEY=` line in `.env.example` (SC-020)
- [ ] T086 [P] Add `ci/test.gitlab-ci.yml` `external-probe` scheduled job that resolves the staging and production `*.run.app` hostnames from the public internet and asserts the request fails before TLS terminates (SC-018)
- [ ] T087 [P] Add `ci/lint.gitlab-ci.yml` `terraform-secrets-check` job that fails the pipeline if any Cloud Run env binding in either env's `terraform plan` JSON has a plaintext value where a `secret_key_ref` is expected (SC-019)

**Checkpoint**: From a fresh clone, `docker compose up` produces a callable `/forecast` in under 5 minutes (SC-014); MR pipeline lint+test+build+iac-validate green; merge to main deploys to staging; tag + manual approval deploys to production.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Hardening, drift detection, performance verification, end-to-end smoke against `main`.

- [ ] T088 [P] Performance assertion in `tests/integration/test_perf_warm_cache.py` (marker `slow`): warm-cache `/forecast` p99 ≤ 500 ms over 200 requests against the synthetic fixture (SC-001)
- [ ] T089 [P] Performance assertion in `tests/integration/test_perf_cold_load.py` (marker `slow`): cold model load + predict p99 ≤ 3 s for a 5 MB artifact (SC-002)
- [ ] T090 [P] Performance assertion in `tests/integration/test_perf_train.py` (marker `slow`): full training run on synthetic 24mo × 5 series ≤ 60 s (SC-006)
- [ ] T091 [P] Coverage-band sweep in `tests/integration/test_coverage_band.py` (marker `slow`): trains 10 distinct synthetic series, asserts 80%-interval empirical coverage ∈ [0.75, 0.85] for ≥ 9/10 and 95%-interval ∈ [0.92, 0.97] for ≥ 9/10 (SC-003, SC-004)
- [ ] T092 [P] End-to-end quickstart validation in `tests/integration/test_quickstart.py` (marker `slow`): runs the exact commands from `specs/001-forecast-sidecar-mvp/quickstart.md` §3 inside compose and asserts the documented outcome (SC-012, SC-014)
- [ ] T093 Configure GitLab "protected environments" for `production` (manual approver list) and "protected branches" for `main` (no force-push, only-merge) — captured as a runbook entry in `docs/architecture.md` (T061) since it's not Terraform-able
- [ ] T094 Configure scheduled `drift-check` pipeline in GitLab (24h cadence) referenced by T074
- [ ] T095 [P] Add `CHANGELOG.md` skeleton with the v0.1.0 entry covering this MVP
- [ ] T096 Run `lychee` over the full repo and fix any broken internal links surfaced
- [ ] T097 Run `gitleaks detect --redact -v` over the full repo and remove any incidental leaks (also verifies SC-017 baseline)
- [ ] T098 [P] Profile the warm-cache forecast path with `py-spy` and capture a flamegraph in `docs/perf/` (only commit if it shows a non-trivial hotspot; otherwise discard)

**Final Checkpoint**: All 20 success criteria from spec.md are demonstrable; pre-commit + CI green on `main`; staging deploy succeeds end-to-end; production deploy succeeds end-to-end on a `v0.1.0` tag.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: no dependencies; entry point.
- **Phase 2 (Foundational)**: depends on Phase 1; **blocks all user-story phases**.
- **Phase 3 (US1)**: depends on Phase 2.
- **Phase 4 (US2)**: depends on Phase 2; can run in parallel with Phase 3.
- **Phase 5 (US3)**: depends on Phase 3 (extends predict).
- **Phase 6 (US4)**: depends on Phase 2; can run in parallel with Phases 3/4/5.
- **Phase 7 (Documentation)**: depends on enough of Phase 2/3/4 to describe shape; in practice authored during/after MVP (US1 + US2) freeze.
- **Phase 8 (Operations & Infrastructure)**: depends on Phase 1 (Dockerfile + compose only need scaffolding); the Terraform side can scaffold in parallel with user stories but `deploy:staging` requires a working image which requires US1 + US2.
- **Phase 9 (Polish)**: depends on all preceding phases.

### Within a User Story

- Tests written and FAILING first (TDD per the constitution).
- Modules before endpoints; endpoints last.
- Story self-contained: each story's checkpoint must pass without subsequent stories.

### Parallel Opportunities

- **Phase 1**: T008–T013 are file-disjoint and parallelizable.
- **Phase 2**: T014, T015, T017, T018, T023 are independent code modules; T016 (config) is a serial dependency of T019/T020/T021/T022. Test infra T024–T028 parallel with each other and with T029–T035.
- **Phase 3 (US1)**: T036–T039 (tests) all parallel; T040 → T041 → T042 sequential.
- **Phase 4 (US2)**: T043–T047 (tests) all parallel; T048, T049, T050 mostly parallel after T023 lands; T051 (CLI) depends on T048–T050; T052/T053 trivial follow-ups.
- **Phase 7**: T060, T061, T062, T063 all parallel.
- **Phase 8**: T075–T079 (Terraform leaf modules) parallel; T080 (network) reads agent-sidecar so serial; T081/T082 depend on T075–T080; T083/T084 depend on all modules; CI files T070–T074 parallel.
- **Phase 9**: T088–T092 + T095, T097, T098 parallel.

---

## Parallel Example: Phase 2 (Foundational)

```bash
# After T016 (config.py) lands, fan out independent modules:
Task: "Implement seeds in src/forecast_sidecar/seeds.py"                # T014
Task: "Implement manifest in src/forecast_sidecar/manifest.py"          # T015
Task: "Implement schemas in src/forecast_sidecar/schemas.py"            # T017
Task: "Implement observability in src/forecast_sidecar/observability.py" # T018
Task: "Implement model/features in src/forecast_sidecar/model/features.py" # T023

# Test infra in parallel:
Task: "GCS fake in tests/fakes/gcs.py"                                  # T024
Task: "Synthetic series in tests/conftest.py"                           # T025
Task: "Sample history fixture"                                          # T026
Task: "Sample feature_config fixture"                                   # T027
Task: "Sample request fixture"                                          # T028
```

## Parallel Example: User Story 1 tests (TDD-first)

```bash
# All must fail before any T040-T042 implementation work begins:
Task: "Contract test for ForecastResponse shape"     # T036
Task: "API happy-path test"                          # T037
Task: "API error-class test (401/400/404/409/503)"   # T038
Task: "Integration smoke for /forecast"              # T039
```

---

## Implementation Strategy

### MVP First (US1 + US2 — both P1)

1. Phase 1 (Setup) → 13 tasks.
2. Phase 2 (Foundational) → 22 tasks. **Critical**: blocks everything.
3. Phase 3 (US1) **and** Phase 4 (US2) in parallel → 17 tasks combined; one developer per story is the natural split.
4. **STOP and validate the MVP**: `docker compose up`; train via fixtures; call `/forecast`; observe a complete forecast with calibrated intervals.
5. Demo / accept the MVP before continuing.

### Incremental Delivery

- After MVP: add US3 (5 tasks) → demo what-if scenarios.
- Add US4 (3 tasks) → wire Cloud Run probes.
- Phase 7 (Documentation) and Phase 8 (Infra/CI) fold in next; staging deploy possible after Phase 8 completes for the first time.
- Phase 9 (Polish) happens against `main` and adds the verifiable success criteria suite.

### Parallel Team Strategy

With 3 developers post-Foundational:

- Dev A: US1 (T036–T042) → US3 (T054–T056)
- Dev B: US2 (T043–T053) → US4 (T057–T059)
- Dev C: Phase 8 Terraform + GitLab CI scaffolding (T064–T084) — can scaffold while US1/US2 are in flight; smoke-tests against the MVP image once it builds.

Documentation (Phase 7) is one engineer-day at the end of MVP and is a natural single-owner task.

---

## Notes

- Tests are MANDATORY for this project (constitution Principle III + IV gates run in pre-commit and CI). Every user story has TDD-first tests that must FAIL before implementation begins.
- The `[P]` markers reflect file-level disjointness only. They do not imply parallel staffing — most teams will run sequentially within a phase.
- Commit after each task or each tightly-coupled group. The git extension's `after_*` hooks make this cheap.
- Foundational network research (T080) requires reading the existing `toolsname-agent-sidecar` Terraform; if that repo is unavailable, default to Direct VPC egress + VPC peering per research R13.
- Stop at any **Checkpoint** to validate the increment independently before moving on.
- Avoid: vague tasks, same-file conflicts marked `[P]`, cross-story dependencies that break independence.
