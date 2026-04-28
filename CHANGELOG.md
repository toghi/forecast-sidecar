# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **MVP runtime** (US1–US4 from
  [spec.md](specs/001-forecast-sidecar-mvp/spec.md)):
  - `POST /forecast` with the FR-006 six-class error taxonomy
    (`invalid_token` / `bad_request` / `not_yet_trained` /
    `model_not_found` / `model_not_ready` / `storage_unavailable`)
  - `python -m forecast_sidecar.train_cli` Click CLI with the full exit
    code taxonomy (0/1/2/3/4/5) including the constitution-IV gate that
    refuses promotion when the model loses to a SeasonalNaive baseline
    by < 10% sMAPE
  - Scenario-overrides path on `/forecast` (US3)
  - `/healthz` + `/readyz` probes (US4)
- **In-process model cache** with two-tier TTL semantics
  (model 1h, latest-pointer 60s) and asyncio-singleflight on first load
- **GCS atomic-promotion** of `latest.json` via `If-Generation-Match` CAS
  + 10-version retention pruning per `(company, CO)` (FR-042)
- **Constitution v1.0.0** at `.specify/memory/constitution.md` —
  reproducibility, temporal integrity, data contract, baseline-beating
  evaluation, configuration-over-code
- **Local stack** via `compose.yaml` + `fake-gcs-server` —
  `docker compose up` boots the service end-to-end with no GCP creds
- **Documentation**: `README.md` + `docs/architecture.md` (8-section
  including a constitution-→-code map) + lychee link-check CI
- **GitHub Actions CI/CD** under `.github/workflows/`:
  `lint`, `test`, `build`, `iac`, `deploy-staging` (auto on `main`),
  `deploy-production` (manual on `vX.Y.Z` tag with required reviewers),
  `drift-check` + `external-probe` (scheduled). Auth via Workload
  Identity Federation (no JSON service-account keys).
- **Terraform**: 8 modules (`gcs_bucket`, `artifact_registry`,
  `secret_manager`, `cloud_tasks`, `iam`, `network`, `cloud_run_service`,
  `cloud_run_job`) + 2 environments (`staging`, `production`). Network
  uses Direct VPC egress + VPC peering + Cloud NAT + Private Google
  Access. Cloud Run ingress = `internal`. Secrets via Secret Manager
  `secret_key_ref`. Validation block on `var.allowed_callers` enforces
  FR-041 startup gate.
- **108 tests** (unit / contract / integration / api) + 5 slow tests
  (perf, coverage, quickstart). `mypy --strict` clean on `src/`.

### Constitution amendments

This is the initial ratification. See
[.specify/memory/constitution.md](.specify/memory/constitution.md).

### Clarification log

Both clarifications recorded in
[spec.md § Clarifications](specs/001-forecast-sidecar-mvp/spec.md):

- 2026-04-28: Network primitive = Direct VPC egress + VPC peering;
  `ALLOWED_CALLERS` mandatory in cloud envs; 10-version retention;
  trainer queue concurrency cap 5/2; `not_yet_trained` ≠
  `model_not_found`.
- 2026-04-29: CI/CD platform reversed from GitLab CI/CD to GitHub
  Actions.

[Unreleased]: https://github.com/REPLACE-ME/forecast-sidecar/compare/...HEAD
