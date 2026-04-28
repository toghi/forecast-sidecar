# forecast-sidecar

Per-(company, computed_object) time-series forecasting sidecar built on
[MLForecast](https://github.com/Nixtla/mlforecast) and
[LightGBM](https://github.com/microsoft/LightGBM). Internal-only HTTP service
plus a same-image batch trainer; deploys to GCP Cloud Run.

> **Status**: scaffolding (Phase 1). The full README — stack, layout, local
> dev, deployment, contracts, constitution pointer — lands in
> [task T060](specs/001-forecast-sidecar-mvp/tasks.md) per FR-027.

## Pointers

- [Constitution](.specify/memory/constitution.md) — binding governance
- [Spec](specs/001-forecast-sidecar-mvp/spec.md) — what we're building
- [Plan](specs/001-forecast-sidecar-mvp/plan.md) — how we'll build it
- [Tasks](specs/001-forecast-sidecar-mvp/tasks.md) — ordered work units
- [Quickstart](specs/001-forecast-sidecar-mvp/quickstart.md) — local dev recipe

## Architecture

See [docs/architecture.md](docs/architecture.md) (forthcoming, task T061).
