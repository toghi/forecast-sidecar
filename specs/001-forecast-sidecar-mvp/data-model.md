# Phase 1 Data Model: Forecast Sidecar MVP

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

This service has no relational database. The "data model" here is the set
of structured payloads that cross module / process / network boundaries:

1. Wire schemas (HTTP request/response) — Pydantic v2.
2. Persisted artifact schemas (GCS) — JSON / pickle.
3. Internal training-time dataframe contract — `(unique_id, ds, y, …)`.
4. Configuration entities (env, feature config) — Pydantic / JSON Schema.

---

## 1. HTTP wire schemas (Pydantic v2)

All schemas use `pydantic.BaseModel` with `model_config = ConfigDict(extra="forbid")`
unless noted otherwise. Datetimes are ISO-8601 strings on the wire and
`datetime` objects in Python; tz-aware UTC is the project default.

### 1.1 `ForecastRequest`

| Field | Type | Required | Notes |
|---|---|---|---|
| `company_id` | `UUID` | yes | passed through verbatim; opaque to the service |
| `computed_object_id` | `UUID` | yes | as above |
| `model_version` | `int \| None` | no | if `None`, resolved to `latest.json.version` |
| `horizon_periods` | `int >= 1, <= 60` | yes | 60 is hard cap (sanity; can be raised by config) |
| `future_features` | `list[FuturePeriodFeatures]` | yes | length MUST equal `horizon_periods` (FR-005) |
| `scenario_overrides` | `dict[str, dict[str, Any]] \| None` | no | keyed by ISO date string → `{feature: value}` (FR-007) |

**Validation rules (request-level)**:
- `len(future_features) == horizon_periods` (else 400).
- All `period` values in `future_features` are unique and strictly increasing.
- `scenario_overrides` keys MUST appear in `future_features` periods.

### 1.2 `FuturePeriodFeatures`

`extra="allow"` — feature column names are not known statically; the
allowed set is the model's declared `future_exog`. Validation that
columns match the model's expectations happens after model load (deferred
to `model.predict.predict()`), producing a 400 with the missing-column
list (FR-004).

| Field | Type | Required | Notes |
|---|---|---|---|
| `period` | `date` (or tz-naive `datetime` at midnight UTC) | yes | mapped to mlforecast `ds` |
| `<feature_name>` | `float \| str` | varies | declared by feature_config; `str` columns → pandas `category` |

### 1.3 `ForecastResponse`

| Field | Type |
|---|---|
| `model_version` | `int` |
| `trained_at` | `datetime` (UTC) |
| `forecast` | `list[ForecastPoint]` |
| `model_metrics` | `ModelMetricsSummary` |

### 1.4 `ForecastPoint`

| Field | Type | Notes |
|---|---|---|
| `period` | `date` | mlforecast `ds` |
| `point` | `float` | mlforecast median / mean prediction |
| `lo80` | `float` | conformal lower bound at 80% level |
| `hi80` | `float` | conformal upper bound at 80% level |
| `lo95` | `float` | conformal lower bound at 95% level |
| `hi95` | `float` | conformal upper bound at 95% level |

**Invariants**: `lo95 ≤ lo80 ≤ point ≤ hi80 ≤ hi95`. Enforced by a
`model_validator(mode="after")` on `ForecastPoint`.

### 1.5 `ModelMetricsSummary`

Subset of `metadata.json.metrics` that is safe to expose to the caller.

| Field | Type |
|---|---|
| `training_mae` | `float` |
| `training_smape` | `float` |
| `coverage_80` | `float` |
| `coverage_95` | `float` |

### 1.6 `ErrorResponse`

| Field | Type | Notes |
|---|---|---|
| `error` | `str` | machine-readable code: `invalid_token`, `bad_request`, `model_not_found`, `model_not_ready`, `storage_unavailable` |
| `detail` | `str` | human-readable message |
| `missing_columns` | `list[str] \| None` | only on `bad_request` for FR-004 |
| `expected_rows` | `int \| None` | only on FR-005 violation |
| `actual_rows` | `int \| None` | only on FR-005 violation |

### 1.7 `HealthResponse` / `ReadyResponse`

| Field | Type |
|---|---|
| `status` | `Literal["ok", "unavailable"]` |
| `models_cached` | `int` (ready only) |
| `gcs_reachable` | `bool` (ready only) |

---

## 2. Persisted artifact schemas (GCS)

### 2.1 Storage layout

```
gs://{FORECAST_BUCKET}/forecasts/{company_id}/{computed_object_id}/
├── latest.json                      # atomic pointer (FR-015)
├── v{N}/
│   ├── model.pkl                    # joblib-pickled MLForecast object
│   ├── metadata.json                # see §2.3
│   └── error.json                   # ONLY if training failed (FR-016)
└── …
```

### 2.2 `latest.json`

```json
{
  "version": 3,
  "trained_at": "2026-04-15T03:00:00Z",
  "model_path": "forecasts/{company_id}/{co_id}/v3/model.pkl"
}
```

Atomic write via GCS `If-Generation-Match` (research R4).

### 2.3 `metadata.json` (per version)

```json
{
  "version": 3,
  "trained_at": "2026-04-15T03:00:00Z",
  "training_window": {
    "from": "2024-01-01",
    "to": "2026-04-01",
    "n_periods": 28,
    "n_series": 5
  },
  "feature_config": { /* canonicalized feature_config.json */ },
  "feature_config_hash": "sha256:…",
  "data_hash": "sha256:…",
  "metrics": {
    "model": {
      "mae": 1234.5,
      "smape": 0.087,
      "coverage_80": 0.81,
      "coverage_95": 0.94
    },
    "baseline": {
      "name": "SeasonalNaive",
      "season_length": 12,
      "mae": 1530.0,
      "smape": 0.105
    },
    "improvement_smape_pct": 17.1,
    "n_holdout_windows": 10,
    "per_series": [
      {"unique_id": "s_0", "model_smape": 0.072, "baseline_smape": 0.095}
    ]
  },
  "library_versions": {
    "python": "3.11.9",
    "mlforecast": "0.13.0",
    "lightgbm": "4.5.0",
    "numpy": "2.0.1",
    "pandas": "2.2.2",
    "polars": "1.5.0"
  },
  "git_sha": "f7aac78…",
  "manifest_hash": "sha256:…"
}
```

The `manifest_hash` is sha256 over the canonical JSON of all fields above
*except itself*. It is what `loader` checks first to short-circuit
identical reloads.

### 2.4 `error.json` (only on training failure)

```json
{
  "version": 3,
  "failed_at": "2026-04-15T03:00:31Z",
  "exit_code": 3,
  "phase": "fit",
  "error_type": "ValueError",
  "error_message": "Target column contains all NaN values for unique_id=s_2",
  "git_sha": "f7aac78…",
  "trace_id": "X-Cloud-Trace-Context value if available"
}
```

`latest.json` is NOT updated when `error.json` is written.

---

## 3. Internal training-time dataframe contract

Every dataframe inside the trainer crossing a module boundary uses
mlforecast's long format:

| Column | Type | Required | Notes |
|---|---|---|---|
| `unique_id` | `str` / `category` | yes | one row per series |
| `ds` | `datetime64[ns, UTC]` (or tz-naive UTC) | yes | matches declared `freq` |
| `y` | `float64` | yes (training only) | target column |
| `<static_feature>` | varies | optional | constant per `unique_id` |
| `<historic_exog>` | varies | optional | values present in history only |
| `<future_exog>` | varies | optional | values present in history AND future |

**Validation at load**: `storage.read_history()` checks:
- All required columns present.
- `ds` strictly increasing per `unique_id`.
- No duplicate `(unique_id, ds)` rows.
- `freq` matches declared frequency (uses `pd.infer_freq` per series).
- Gap policy applied per `feature_config.gap_policy`:
  `drop` | `impute_zero` | `impute_ffill` | `error`.

Validation failure → exit code 2, `error.json` with `phase=download`.

---

## 4. Configuration entities

### 4.1 Service config (`config.Settings`, pydantic-settings)

Loaded from env vars. See spec §10. All env vars get a Pydantic field
with type and (where applicable) validation.

| Field | Env var | Type | Default |
|---|---|---|---|
| `port` | `PORT` | `int` | 8080 |
| `forecast_bucket` | `FORECAST_BUCKET` | `str` | (required) |
| `expected_audience` | `EXPECTED_AUDIENCE` | `str` | (required, service mode) |
| `allowed_callers` | `ALLOWED_CALLERS` | `set[str]` | empty (any valid OIDC) |
| `model_cache_size` | `MODEL_CACHE_SIZE` | `int >= 1` | 100 |
| `model_cache_ttl_seconds` | `MODEL_CACHE_TTL_SECONDS` | `int >= 1` | 3600 |
| `latest_pointer_ttl_seconds` | (derived) | `int` | 60 |
| `sentry_dsn` | `SENTRY_DSN` | `str \| None` | None |
| `sentry_environment` | `SENTRY_ENVIRONMENT` | `str` | "production" |
| `log_level` | `LOG_LEVEL` | `Literal["debug","info","warn","error"]` | "info" |
| `auth_bypass` | `AUTH_BYPASS` | `bool` | False (gated; see R6) |
| `git_sha` | `GIT_SHA` | `str` | "unknown" (build-time injected) |

### 4.2 Per-`(company, CO)` feature config (`feature_config.json`)

This is the contract between the calling Go API and the trainer. Its JSON
Schema is published as `contracts/feature_config.schema.json`. Fields:

```json
{
  "freq": "MS",
  "target": "sales",
  "horizon": 12,
  "min_history_periods": 24,
  "static_features": ["segment", "region"],
  "historic_exog": ["calls"],
  "future_exog": ["active_clients", "bizdev_id"],
  "categorical_features": ["segment", "region", "bizdev_id"],
  "lags": [1, 3, 6, 12],
  "lag_transforms": {
    "1": [{"name": "rolling_mean", "window_size": 3},
          {"name": "rolling_mean", "window_size": 6}]
  },
  "date_features": ["month", "quarter"],
  "target_transforms": [
    {"name": "Differences", "args": [[1]]}
  ],
  "gap_policy": "error",
  "lightgbm_params": {
    "n_estimators": 500,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "max_depth": -1,
    "min_data_in_leaf": 20,
    "objective": "regression",
    "metric": "mae",
    "verbosity": -1
  },
  "calibration": {
    "n_windows": 10
  }
}
```

`lightgbm_params` is overlaid on `configs/lightgbm_defaults.yaml` so the
caller only has to override what they care about. `deterministic`, `seed`,
and `num_threads` are always set by our code, never by the caller (FR
deterministic baseline per Constitution I).

---

## 5. State transitions

The only stateful thing in this service is the artifact tree on GCS. Per-
`(company, CO)`, the lifecycle is:

```
no-state ──► training ──► v1 (with metadata)  ──► latest=1
                │
                ├── failure ──► v1/error.json (latest unchanged)
                ▼
            training ──► v2 (with metadata)  ──► latest=2 (atomic CAS)
                │
                ├── promotion lost race ──► no-op (caller is idempotent)
                ▼
            …
```

Inference reads either `latest.json` (no `model_version` requested) or
`v{N}/{model.pkl,metadata.json}` directly. A request for an existing
`v{N}` whose `error.json` is present and whose `model.pkl` is *missing*
returns 409 (model_not_ready / failed); a request for a `v{N}` that
doesn't exist at all returns 404.
