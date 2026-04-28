# Training CLI Contract

**Module**: `forecast_sidecar.train_cli`
**Invocation**: `python -m forecast_sidecar.train_cli [OPTIONS]`
**Implementation**: `click`

## Arguments

| Flag | Required | Type | Description |
|---|---|---|---|
| `--company-id` | yes | UUID-string | passed through to log/Sentry tags; opaque |
| `--computed-object-id` | yes | UUID-string | as above |
| `--history-url` | yes | URL | `gs://...` (prod) or `file://...` (dev only) — CSV with `period`, `target`, declared feature columns |
| `--feature-config-url` | yes | URL | `gs://...` or `file://...` — JSON conforming to `feature_config.schema.json` |
| `--output-version` | yes | int >= 1 | the new version number; the GCS prefix `v{N}` |
| `--seed` | no | int | default 42; passed to `set_seed()` and LightGBM |
| `--dry-run` | no | flag | runs everything except the GCS writes; useful for local sanity |

## Behavior (high-level pipeline)

```
parse args
  → set_seed(seed)
  → log "phase=download"   ; storage.read_history(history_url)
                          ; storage.read_feature_config(feature_config_url)
  → log "phase=validate"   ; validate history schema vs feature config
                          ; refuse if n_periods < min_history_periods (exit 2)
  → log "phase=fit"        ; build MLForecast(models=[lgb_regressor, seasonal_naive])
                          ; fit(prediction_intervals=PredictionIntervals(n_windows=10, h=horizon))
  → log "phase=calibrate"  ; cross_validation(...) → utilsforecast metrics
                          ; check model_smape <= baseline_smape * 0.9 (exit 4 if not)
  → log "phase=upload"     ; storage.write_artifact(v{N}/model.pkl)
                          ; storage.write_metadata(v{N}/metadata.json)
                          ; storage.atomic_promote_latest(v{N})  -- CAS on generation
  → exit 0
```

## Exit codes

| Code | Meaning | Side effects |
|---|---|---|
| 0 | Success — `latest.json` updated | model + metadata written |
| 1 | Unhandled error | Sentry capture; partial writes possible (caller must retry safely — promotion did not happen) |
| 2 | Bad input (missing files, schema validation, insufficient history) | `error.json` written if `output-version` reachable; `latest.json` unchanged |
| 3 | Training failure (LightGBM error, all-NaN target, infeasible config) | `error.json` written; `latest.json` unchanged |
| 4 | Calibration regression (model lost to baseline by ≥ 10% sMAPE; constitution IV gate) | model + metadata written for inspection; `latest.json` NOT updated; `error.json` flags regression |
| 5 | Promotion CAS lost cleanly (another writer won) | logged; `latest.json` already at desired or higher version; treated as success |

## Idempotency

Re-running with the same `--output-version` after any failure (including
exit 4) is safe:
- If `v{N}/model.pkl` and `v{N}/metadata.json` already exist with the
  same `manifest_hash`, the trainer detects them and skips fit, only
  retrying promotion.
- If they exist with a different `manifest_hash`, the trainer overwrites
  them (the prior attempt is being explicitly redone with the same
  version number — caller's choice).
- The promotion CAS guarantees `latest.json` is never in a torn state.

## Required IAM

- Service account: `roles/storage.objectAdmin` on `${FORECAST_BUCKET}`.
  No other GCP roles. The trainer makes no other GCP API calls.

## Logging

JSON to stdout (Cloud Logging). Every log line carries:
`run_id, company_id, computed_object_id, output_version, phase,
duration_ms` (when applicable). Phases: `download`, `validate`, `fit`,
`calibrate`, `upload`, `done`.

## Local dev

`file://` URLs are accepted only when the env var
`FORECAST_ALLOW_FILE_URLS=1` is set; otherwise the trainer rejects
`file://` early with exit 2. CI sets this env var; production deploy
configs do not.
