<!--
Sync Impact Report
==================
Version change: (unratified template) → 1.0.0
Bump rationale: Initial ratification — first concrete content replaces all
placeholder tokens. MAJOR per spec-kit semver because the document moves from
template state to a binding governance artifact.

Modified principles (template slot → concrete principle):
- [PRINCIPLE_1_NAME]              → I. Reproducibility (NON-NEGOTIABLE)
- [PRINCIPLE_2_NAME]              → II. Temporal Integrity (NON-NEGOTIABLE)
- [PRINCIPLE_3_NAME]              → III. Data Contract
- [PRINCIPLE_4_NAME]              → IV. Baseline-Beating Evaluation
- [PRINCIPLE_5_NAME]              → V. Configuration Over Code

Added sections:
- Technology Stack & Standards (replaces [SECTION_2_NAME])
- Development Workflow & Quality Gates (replaces [SECTION_3_NAME])

Removed sections: none.

Templates requiring updates:
- ✅ .specify/templates/plan-template.md — references the constitution
  generically ("Gates determined based on constitution file"); no rewrite
  needed, the gates resolve to the principles below at /speckit-plan time.
- ✅ .specify/templates/spec-template.md — no constitution references; aligned.
- ✅ .specify/templates/tasks-template.md — no constitution references; aligned.
- ✅ .specify/templates/checklist-template.md — no constitution references.
- ✅ Command files under .claude/commands/ — none present (skills-based
  integration); skill SKILL.md files reference the constitution only by path.

Follow-up TODOs: none.
-->

# Forecast Sidecar Constitution

## Core Principles

### I. Reproducibility (NON-NEGOTIABLE)

Every training, evaluation, and inference run MUST be bit-reproducible from a
recorded run manifest. Concretely:

- Dependencies pinned via a `uv` lockfile (`uv.lock`) committed to the repo.
- A single `set_seed(seed)` helper seeds `random`, `numpy`, and any model RNG;
  invoked at the entry point of every training/CV script.
- LightGBM is configured with `deterministic=True`, an explicit integer `seed`,
  and a fixed `num_threads` (parallel non-determinism is forbidden in
  promoted models).
- Every run emits a manifest capturing: git SHA (with dirty flag), config
  hash, input data hash (e.g. content hash of the source parquet/partition
  set), library versions (`mlforecast`, `lightgbm`, `numpy`, `polars`/`pandas`),
  Python version, and host OS.
- Models without a manifest MUST NOT be deployed or registered.

Rationale: time-series models silently rot when data, code, or library
versions drift. Reproducibility is the only way to attribute regressions.

### II. Temporal Integrity (NON-NEGOTIABLE)

No future information may leak into training or evaluation features.

- Lag, rolling, and seasonal features MUST be expressed via mlforecast's
  `lags`, `lag_transforms`, `target_transforms`, and `date_features` APIs.
  Manual `.shift()`, `.rolling()`, or groupby trickery on the target column
  in user code is forbidden outside of explicitly reviewed exceptions.
- Evaluation MUST use `MLForecast.cross_validation` with rolling-origin or
  expanding-window splits. Random k-fold CV on time-series data is forbidden.
- Exogenous features MUST be classified as one of: `static_features`,
  `historic_exog`, or `future_exog`. Any feature flagged `future_exog` MUST
  have a documented production source that is available at prediction time
  for the full forecast horizon.
- Train/validation/test boundaries are defined by `ds` cutoffs in config; the
  smallest split unit is one full forecast horizon `h`.

Rationale: leakage is the dominant failure mode in forecasting projects and
is invisible in offline metrics. The mlforecast primitives are the project's
single source of truth for temporal correctness.

### III. Data Contract

All data crossing a module boundary MUST conform to the mlforecast long
format and a validated schema.

- Required columns: `unique_id` (string/categorical), `ds` (datetime, tz-aware
  or explicitly tz-naive UTC), `y` (float). Exogenous columns explicitly typed.
- A `freq` (e.g. `D`, `H`, `W-MON`) is declared per dataset and validated:
  detect gaps, duplicates, and out-of-order timestamps at load time.
- Gap policy is explicit per dataset: one of `drop`, `impute(method)`, or
  `leave_with_nan` — recorded in the dataset config, not chosen ad hoc.
- Schema validation runs at every I/O boundary (`pandera` or `pydantic` for
  metadata). Loading code raises on contract violation; it does not "fix
  silently".
- Polars is the preferred dataframe library for ETL and feature pipelines;
  pandas is acceptable at mlforecast's API boundary where required.

Rationale: silent schema drift is the #2 source of forecasting incidents
after leakage. Explicit contracts catch it at the door.

### IV. Baseline-Beating Evaluation

A model is not a candidate for promotion until it beats a naive baseline on
the same CV folds, with the same metrics, on the same data hash.

- Baselines: at minimum `SeasonalNaive` with the data's natural seasonality;
  `Naive` and `WindowAverage` recorded for context.
- Metric set is fixed in config and computed via `utilsforecast.evaluation`:
  MAE, sMAPE, MASE for point forecasts; pinball loss / CRPS / coverage for
  probabilistic forecasts.
- Reports MUST include per-horizon and per-`unique_id` breakdowns, not only
  globally averaged metrics. A model that beats baseline on average while
  losing on a majority of series is a regression and is rejected.
- Probabilistic intervals MUST be calibrated using mlforecast's
  `PredictionIntervals` (conformal) or LightGBM quantile objectives, with
  empirical coverage reported alongside nominal coverage.

Rationale: "model is better than nothing" is a low bar that gradient boosting
ensembles routinely fail at on series-by-series granularity. Per-series
breakdowns expose this.

### V. Configuration Over Code

Hyperparameters, feature recipes, horizons, splits, series filters, and
metric definitions live in versioned config files — not in code branches.

- Configs are YAML driven by Hydra (preferred) or an equivalent typed loader
  (e.g. `pydantic-settings`). One config = one experiment.
- Tuning produces new configs (and/or a sweep manifest), never new code paths
  guarded by `if model_version == "v3":` style branching.
- Feature definitions (lags, lag transforms, date features, target
  transforms) are declared in config and bound to mlforecast at startup; ad
  hoc inline definitions in scripts are forbidden in promoted code.
- Configs are content-addressed: the hash that lands in the run manifest is
  computed over the resolved (post-override) config.

Rationale: forecasting projects accumulate dozens of variants. Treating
variants as configs (data, not code) keeps the codebase small and makes
sweeps, ablations, and rollbacks trivial.

## Technology Stack & Standards

Mandatory:

- **Language**: Python ≥ 3.11.
- **Environment**: `uv` for dependency management and virtualenvs;
  `uv.lock` is the source of truth.
- **Core ML stack**: `mlforecast` (forecasting framework), `lightgbm` (default
  model), `utilsforecast` (evaluation), `polars` (preferred dataframe lib;
  `pandas` permitted at API boundaries).
- **Quality tooling**: `ruff` (lint + format), `mypy --strict` on `src/`,
  `pytest` for tests, `pre-commit` for hook orchestration.
- **Experiment tracking**: every training run is logged to MLflow (or
  equivalent) with the run manifest fields from Principle I.

Project layout:

```
src/forecast_sidecar/   # importable package
tests/                  # pytest, includes synthetic-data fixtures
configs/                # Hydra config tree
notebooks/              # exploration ONLY — never imported by src/
data/                   # gitignored; canonical data lives in object storage
models/                 # gitignored; artifacts addressed by manifest hash
```

LightGBM defaults that MUST be set explicitly in any training entry point:

- `objective` matched to the task (`regression`, `regression_l1`, `quantile`,
  `tweedie`, etc.) — never left implicit.
- `categorical_feature` passed explicitly; categoricals encoded as pandas
  `category` or polars `Categorical` dtype, not label-encoded ints.
- Early stopping enabled with a temporal validation split — never a random
  split.
- `deterministic=True`, `seed`, and `num_threads` set per Principle I.

Notebooks may import from `src/`; `src/` MUST NOT import from notebooks. Code
graduating from a notebook is moved into `src/` and covered by tests before
it can be referenced from a config.

## Development Workflow & Quality Gates

Local gate (pre-commit, blocking on commit):

- `ruff check --fix` and `ruff format` clean.
- `mypy --strict src/` clean.
- `pytest -m "not slow and not gpu"` green.

Pull request gates (blocking on merge):

- All local gates pass in CI on a clean checkout.
- Full `pytest` suite green, including a smoke training run on a synthetic
  series fixture (catches mlforecast/LightGBM API breakage).
- A schema contract test runs against a representative data sample.
- PRs that change model code, configs, or feature pipelines MUST attach a CV
  report (Principle IV) comparing the new candidate to the current production
  config and to the seasonal-naive baseline. PRs without a report are not
  reviewed.

Artifact discipline:

- Model artifacts are written to `models/<run_id>/` where `run_id` is the
  manifest hash. They are never overwritten in place.
- Promotion to production is a config change pointing at a `run_id`, reviewed
  like any other PR. Rollback is the inverse change.

Test markers:

- `slow`, `gpu`, and `requires_data` markers exclude long/external tests from
  the default selection. CI runs the full set; pre-commit runs the fast set.

## Governance

This constitution supersedes ad-hoc preferences expressed in code review,
issues, or chat. The `/speckit-plan` Constitution Check resolves to the
principles above; violations MUST be enumerated in the plan's Complexity
Tracking table with a justification and a rejected simpler alternative.

Amendments:

- An amendment is a PR that modifies `.specify/memory/constitution.md`,
  bumps the version line per semver (MAJOR for principle removal/redefinition,
  MINOR for added principle/section, PATCH for clarifications), updates the
  `Last Amended` date, and prepends an updated Sync Impact Report.
- The reviewer MUST verify dependent templates under `.specify/templates/`
  remain consistent and update them in the same PR if not.
- Amendments touching NON-NEGOTIABLE principles require a written rationale
  in the PR description and explicit acknowledgement from at least one other
  maintainer.

Compliance review:

- `/speckit-analyze` is the standing compliance check; its failure blocks
  `/speckit-implement`.
- Any code that bypasses a principle (e.g. manual `.shift()` for a documented
  reason) MUST be tagged with a `# constitution-exception: <principle> —
  <reason>` comment and tracked in the plan's complexity table.

**Version**: 1.0.0 | **Ratified**: 2026-04-28 | **Last Amended**: 2026-04-28
