# Feature Specification: Forecast Sidecar MVP

**Feature Branch**: `001-forecast-sidecar-mvp`
**Created**: 2026-04-28
**Status**: Draft
**Input**: User description: "Standalone Python time-series forecasting sidecar
for the `toolsname` backend — per-company LightGBM models with conformal
intervals, served behind authenticated HTTP for inference and trained as a
batch job. Caller (Go API) brokers all data and orchestration; this service
holds no business logic and no database access."

## Clarifications

### Session 2026-04-28

- Q: Which network primitive should the forecast service use to be reachable only from the backend's VPC? → A: Direct VPC egress + VPC peering (modern GCP-recommended path); align with `toolsname-agent-sidecar` in a follow-up MR if they diverge
- Q: Must `ALLOWED_CALLERS` be a non-empty allow-list in production? → A: Required (non-empty) in staging + production; service refuses to start if unset. Local stays open for dev friction.
- Q: What retention policy applies to old model versions? → A: Keep the 10 most recent versions per `(company, CO)`; trainer prunes older `v{N}/` directories after a successful promotion.
- Q: What concurrency cap should the trainer queue enforce? → A: `max_concurrent_dispatches = 5` in production, `2` in staging. No per-second rate cap.
- Q: How does `/forecast` respond when a `(company, CO)` has never been trained? → A: HTTP 404 with `error: "not_yet_trained"`, distinct from `error: "model_not_found"` (which means an explicit `model_version=N` is missing). Both share status 404; the JSON `error` field lets the caller branch UX.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - On-Demand Forecast for a Computed Object (Priority: P1)

A backend operator (the calling Go API, acting on behalf of an end user
clicking "Forecast" in the product UI) submits a request for a previously
trained company × computed-object. They receive a per-period point forecast
plus calibrated 80% and 95% intervals over the requested horizon, along with
the model's identifying metadata.

**Why this priority**: This is the primary product surface — the UI
"Forecast" button, the chat agent's `forecast_series` tool, and the scenario
comparison flow all funnel into this endpoint. Without it, no other path
delivers user value. Training without inference is unobservable; everything
else (P2+) is in service of this.

**Independent Test**: With one trained `(company, CO)` model present in
durable storage, send a valid forecast request with prepared future features
for `H` periods. Verify the response contains `H` rows each with `point`,
`lo80`, `hi80`, `lo95`, `hi95`, plus `model_version` and `trained_at`. Verify
intervals widen monotonically with horizon. Verify the same request without
authentication is rejected.

**Acceptance Scenarios**:

1. **Given** a trained model exists for `(company, CO)` with `model_version`
   omitted in the request, **When** the request is authenticated and contains
   well-formed future features for `horizon_periods` rows, **Then** the
   service returns the latest model's point + 80% + 95% interval forecasts
   along with `trained_at` and the resolved `model_version`.
2. **Given** no model exists for `(company, CO)`, **When** any forecast
   request is made, **Then** the service returns a `not found` error
   distinguishable from "model exists but is not yet ready".
3. **Given** a trained model exists, **When** a request omits a feature
   column the model was trained on, **Then** the service returns a
   `bad request` error naming the missing columns and refuses to predict.
4. **Given** a request without a valid service-to-service identity token,
   **When** the request reaches any forecast endpoint, **Then** the service
   returns `unauthorized` and emits no model output.
5. **Given** a model exists but its training run is still in progress or
   ended in failure, **When** a forecast is requested for that version,
   **Then** the service returns a "not ready" status distinguishable from
   "not found".

---

### User Story 2 - Train a New Model Version (Priority: P1)

A scheduler or on-demand trigger (Go API enqueueing one task per active
company × CO, weekly) starts a training run that consumes staged historical
data and a feature configuration, fits a model, calibrates intervals, and
durably persists a new model version with metadata. After successful
completion, the new version becomes promotable as the latest model.

**Why this priority**: Inference (P1) is impossible without the artifacts P2
produces. Both are required for the MVP, but they are independently
deployable: training can run and write artifacts before any inference traffic
exists, and inference must work with artifacts produced manually for the
first end-to-end test. Both are P1.

**Independent Test**: Trigger the training mode against a fixture history
file and feature-config file. Verify a new versioned artifact directory is
written to durable storage containing the model, metadata, and that the
`latest` pointer is atomically promoted only after both files are present.
Verify a retry with the same `--output-version` produces no partial writes
and no duplicates.

**Acceptance Scenarios**:

1. **Given** a valid history file and feature config staged on object
   storage, **When** the training mode is invoked with an `output-version`
   `N`, **Then** it writes the model, the per-version metadata, and updates
   the `latest` pointer to `N` only after both succeed.
2. **Given** a previous training attempt for `output-version` `N` left
   partial state, **When** the same invocation is retried, **Then** it
   completes successfully with no duplicate or stale files and no readers
   ever see a partially-written `latest`.
3. **Given** an unrecoverable training failure (bad data, library error),
   **When** the job exits, **Then** it writes a structured error record next
   to the failed version, exits non-zero, and does NOT update `latest`.
4. **Given** training completes, **When** the new metadata is read, **Then**
   it contains the training window, declared frequency, target column,
   feature lists (static + exogenous), holdout MAE/sMAPE, empirical 80%
   and 95% coverage, and library-version provenance.
5. **Given** a job is invoked with input URLs that do not exist or that the
   trainer is not authorized to read, **When** it begins, **Then** it fails
   fast with a clear error and writes neither model nor metadata.

---

### User Story 3 - Scenario "What-If" Forecast (Priority: P2)

The same trained model can be invoked with per-period overrides on selected
input features ("what if calls drop 20% in June?"). The response is a
counterfactual forecast that differs from the baseline only for the periods
and features the caller chose to override.

**Why this priority**: The Scenario flow in the product depends on this, and
it's a small surface on top of P1. But P1 alone is a viable MVP for the
direct-forecast path, so this can ship in a fast-follow.

**Independent Test**: Issue two requests for the same `(company, CO,
horizon)` — one without overrides, one with overrides for a single period
and feature. Verify the overridden period's forecast differs and the other
periods match the baseline forecast bit-for-bit (modulo conformal-interval
recalibration noise, which must be zero for a fixed model).

**Acceptance Scenarios**:

1. **Given** a baseline forecast for periods `P1..PH`, **When** a second
   request supplies overrides for feature `X` only at period `Pk`, **Then**
   only the forecast at `Pk` differs from baseline.
2. **Given** an empty or null `scenario_overrides` field, **When** a request
   is made, **Then** the response is identical to the unfilled baseline.
3. **Given** an override naming a feature the model never saw at training,
   **When** the request is made, **Then** the service returns a `bad request`
   error.

---

### User Story 4 - Operational Health and Readiness (Priority: P2)

An operator or platform-owned health-check probe can determine whether the
service is reachable and ready to accept traffic, distinguishing liveness
("the process is up") from readiness ("the process can actually serve
forecasts because storage is reachable").

**Why this priority**: Required for safe deploys (load balancer needs a
distinct readiness signal) but not for first-cut functional value, so P2.

**Acceptance Scenarios**:

1. **Given** the process is running, **When** a liveness probe is sent,
   **Then** the service returns OK without consulting any dependency and
   without authentication.
2. **Given** the model-storage backend is reachable, **When** a readiness
   probe is sent, **Then** the service returns OK with a count of currently
   cached models.
3. **Given** the model-storage backend is unreachable, **When** a readiness
   probe is sent, **Then** the service returns `unavailable` so traffic is
   not routed to it.

---

### Edge Cases

- **Stale cache after promotion**: a new model version is promoted while an
  older version is still cached. The service MUST pick up the new version
  within one cache-TTL window without restart.
- **Missing or malformed future features**: the service MUST refuse to
  predict and report exactly which feature columns or rows are missing — no
  silent imputation.
- **Horizon mismatch**: `future_features` row count differs from
  `horizon_periods`. The service MUST reject the request with a clear
  mismatch error.
- **History gaps / duplicates / non-monotonic timestamps** during training:
  the trainer MUST detect at load time, log structured errors with offending
  rows, and exit non-zero rather than silently dropping or imputing.
- **Calibration drift**: empirical 80%/95% coverage on the latest holdout
  windows falls outside the acceptance band. The metadata MUST surface this;
  v1 does not auto-block promotion (operators decide), but the metric is
  observable.
- **Authentication edge cases**: token whose audience claim does not match
  this deployment is rejected. Token from an allowed identity but the
  service's allow-list (when configured) does not include the caller is also
  rejected.
- **Storage transient failures**: model load fails due to transient storage
  unavailability — the service MUST return a `service unavailable` response
  (not a generic 500) so the caller can retry without alarm.
- **Concurrent training of the same `(company, CO)`**: two jobs racing on the
  same `output-version` MUST not produce a half-written `latest` pointer; one
  loses cleanly.
- **Cold-start (no history)**: training is invoked for a `(company, CO)` with
  fewer than the configured minimum number of periods — the trainer MUST
  refuse and emit a structured error rather than producing an unreliable
  model. (The product's cold-start UX is out of scope; see Out of Scope.)

## Requirements *(mandatory)*

### Functional Requirements

**Inference (HTTP service mode)**

- **FR-001**: The service MUST accept authenticated forecast requests
  identified by `(company_id, computed_object_id, optional model_version,
  horizon_periods, future_features, optional scenario_overrides)` and return,
  per period, a point estimate plus 80% and 95% lower/upper interval bounds
  along with `model_version` and `trained_at`.
- **FR-002**: When `model_version` is omitted, the service MUST resolve to
  the currently-promoted "latest" version for that `(company, CO)`.
- **FR-003**: The service MUST consume only prepared future features; it
  MUST NOT compute lags, rolling stats, or other derived features from any
  history it does not receive in the request.
- **FR-004**: The service MUST reject requests that omit feature columns
  required by the resolved model, returning an error that names the missing
  columns.
- **FR-005**: The service MUST reject requests where the row count of
  `future_features` does not equal `horizon_periods`.
- **FR-006**: The service MUST distinguish six failure classes with
  distinct error responses: invalid input (400 `bad_request`),
  unauthenticated (401 `invalid_token`), no-model-yet for the entire
  `(company, CO)` pair (404 `not_yet_trained`), explicit version
  missing (404 `model_not_found`), version exists but not ready (409
  `model_not_ready`), and storage unreachable (503
  `storage_unavailable`). The `error` field of the response body MUST
  carry the machine-readable code regardless of the HTTP status.
- **FR-007**: The service MUST allow per-period feature overrides via
  `scenario_overrides` and apply each override only to the named period.
  Overriding a feature unknown to the model MUST be rejected.

**Training (job mode)**

- **FR-008**: The training mode MUST be reachable via an alternate entrypoint
  on the same deployable artifact as the inference service (one image, two
  modes).
- **FR-009**: The training mode MUST consume input data and configuration
  exclusively via signed object-storage URLs supplied by the caller; it MUST
  NOT connect to the calling system's database directly.
- **FR-010**: Training MUST fit a per-`(company, CO)` model using a tabular
  gradient-boosting forecaster with caller-declared lags, target transforms,
  static features, and exogenous regressors.
- **FR-011**: Training MUST calibrate prediction intervals at 80% and 95%
  using conformal cross-validation with a configurable number of holdout
  windows (default ≥ 10) and report empirical coverage in the metadata.
- **FR-012**: Training MUST compute and persist holdout MAE, sMAPE, and
  empirical coverage at 80% and 95% in the per-version metadata.
- **FR-013**: Each model version MUST be tagged with: training window
  (start, end, period count, series count), declared frequency, target
  column, list of static features, list of exogenous features, list of
  lags / rolling-window definitions, and library-version provenance
  (Python, mlforecast, lightgbm).
- **FR-014**: Training MUST be idempotent on retry given the same
  `output-version`: a retry produces the same final state and never leaves
  partial writes.
- **FR-015**: Training MUST atomically promote the new version as "latest"
  only after the model and metadata files for that version are both present
  in durable storage.
- **FR-016**: On unrecoverable failure, training MUST write a structured
  error record next to the failed version, exit non-zero, and leave
  "latest" pointing at the previous successful version.
- **FR-017**: Training MUST refuse to start (or fail fast with a structured
  error) when input history has fewer than the configured minimum periods,
  has duplicate `(series, period)` rows, or has non-monotonic timestamps.

**Cross-cutting**

- **FR-018**: All forecast and metadata endpoints MUST require valid
  service-to-service authentication. Only liveness and readiness probes are
  permitted to be unauthenticated.
- **FR-019**: The service MUST verify that the caller's audience claim
  matches this deployment; mismatched audiences MUST be rejected.
- **FR-020**: When an explicit allow-list of caller identities is
  configured, requests from any other identity MUST be rejected even if
  cryptographically valid.
- **FR-021**: The service MUST NOT persist any caller-supplied request
  payload to durable storage; it consumes payloads in memory and reads only
  previously trained model artifacts.
- **FR-022**: The service MUST cache loaded models in process memory with a
  bounded size and a time-to-live, so the next promoted version becomes
  effective without restart within one TTL window.
- **FR-023**: The service and trainer MUST emit structured logs containing,
  at minimum: request or run id, company id, computed-object id, resolved
  model version, latency or phase duration, and outcome status. The trainer
  MUST additionally log the lifecycle phase (download / fit / calibrate /
  upload).
- **FR-024**: The service MUST honor and forward distributed-trace context
  supplied by the caller so cross-service requests are correlatable.
- **FR-025**: All unhandled exceptions in either mode MUST be reported to
  the configured error tracker, tagged with `company_id`,
  `computed_object_id`, and execution mode (`service` / `train`).
- **FR-026**: The inference identity MUST hold read-only access to the
  model store; the trainer identity MUST hold write access. No identity may
  hold more privilege than its role requires.

**Documentation**

- **FR-027**: The repository MUST contain a `README.md` at the repo root
  covering, at minimum: project purpose, technology stack, repository
  layout, local development (install, run service, run trainer, run
  tests), deployment overview, and the public-contract pointers (HTTP
  contract, training-CLI contract, feature-config schema). The README MUST
  be discoverable as the landing page on the source-host (e.g. GitHub).
- **FR-028**: The repository MUST contain an architecture document at
  `docs/architecture.md` covering, at minimum: a system-context diagram
  showing the calling backend, this service, the training job, and
  durable storage; the request lifecycle for an inference call (auth →
  cache → load → predict → respond); the training-job lifecycle
  (download → validate → fit → calibrate → upload → atomic promote); the
  artifact storage layout and atomic-promotion contract; the in-memory
  cache semantics (size, TTL, latest-pointer resolution); the
  authentication and identity model; how the constitution's five
  principles are realized in the code; and links to the formal contracts
  (OpenAPI, training CLI, feature-config schema).
- **FR-029**: The `README.md` MUST link to `docs/architecture.md` from a
  prominent section (e.g. "Architecture" near the top, or a top-level
  table-of-contents entry). The link MUST be a relative-path link so it
  works on the source-host browser and on local clones.
- **FR-030**: Both documents MUST be kept in sync with code: any pull
  request that changes the public HTTP contract, the training-CLI
  contract, the artifact storage layout, the authentication model, or the
  cache semantics MUST update `docs/architecture.md` (and `README.md` if
  the change is user-visible) in the same PR. Pull requests violating
  this rule MUST be flagged by reviewers and not merged until the docs
  are updated.

**Operations & Infrastructure**

- **FR-031**: All cloud-platform infrastructure for this service MUST be
  declared as Terraform (HCL) source in an `infra/` directory at the repo
  root. Resources covered: the inference Cloud Run service, the training
  Cloud Run Job, the model-storage bucket (with object versioning), all
  IAM bindings (least-privilege per FR-026), the Artifact Registry
  repository for the container image, the trainer-trigger queue, and any
  Secret Manager secrets the service consumes. Manual ("clickops")
  resource changes in staging or production are forbidden.
- **FR-032**: Three environments MUST exist and be reproducible from
  source:
  1. **Local** — full stack runs on a developer's laptop via Docker
     Compose (`compose.yaml`), including: the inference service, an
     on-demand trainer container, and an in-cluster object-storage
     emulator (e.g. `fake-gcs-server`) so no cloud credentials are
     needed for the loop "edit → train → predict".
  2. **Staging** — a complete deployment on the cloud platform, owned
     by Terraform, used for integration with the calling backend's
     staging environment. Identical service contract to production.
  3. **Production** — the live deployment serving real callers, owned by
     Terraform.
  Local, staging, and production MUST share identical service-side code,
  configuration schema, and contracts; they differ only in scale,
  region, secrets, bucket names, and resource quotas.
- **FR-033**: CI/CD MUST be defined in a `.gitlab-ci.yml` file at the repo
  root, executed by GitLab CI/CD. The pipeline MUST include, at minimum,
  these stages in order: `lint`, `test`, `build` (container image),
  `iac-validate` (`terraform fmt -check`, `terraform validate`,
  `terraform plan`), `deploy:staging`, `iac-apply:production`,
  `deploy:production`.
- **FR-034**: Every merge request MUST run `lint`, `test`, `build`, and
  `iac-validate` and MUST block on any of those stages failing. The
  `terraform plan` output for both staging and production MUST be
  attached to the merge request as a job artifact so reviewers can see
  the infrastructure diff.
- **FR-035**: Merges to the default branch MUST automatically deploy to
  staging (idempotent re-deploy is acceptable). Production deploy MUST
  be a manual job in the pipeline, gated on (a) a release tag matching
  `vX.Y.Z` and (b) explicit operator approval in GitLab. Production
  deploy MUST NOT run automatically on merges.
- **FR-036**: Configuration values MUST be supplied per environment
  according to this strict source map; no other source is permitted:
  - **Local**: a `.env` file at the repo root, loaded by Docker Compose
    via `env_file:` and by the application via `pydantic-settings`. A
    committed `.env.example` MUST list every variable with a placeholder
    value and a one-line comment. The real `.env` MUST be gitignored and
    Docker-ignored.
  - **Staging** and **Production**: every secret (Sentry DSN, OIDC
    allow-list contents, any future credential) MUST live in **Google
    Secret Manager** in the same GCP project as the deployment, mounted
    into the Cloud Run service / Job as environment variables via the
    Cloud Run Secret integration. Non-secret env vars (port, audience,
    bucket name, log level) MUST be set on the Cloud Run resource by
    Terraform.
  Secrets MUST NOT appear in Terraform source, in committed files, in
  Terraform plan output, in container images, or in CI logs (CI vars
  used to bootstrap Secret Manager MUST be GitLab masked + protected).
  Terraform state itself MUST live in a private GCS bucket with object
  versioning enabled, one state-bucket per environment.
- **FR-037**: The local environment MUST NOT require any GCP credentials
  to run end-to-end. The default `compose.yaml` MUST start with
  `AUTH_BYPASS=1` and an in-cluster object-storage emulator so a fresh
  contributor can train + forecast against a fixture series with one
  command.
- **FR-038**: The inference service in staging and production MUST NOT
  be reachable from the public internet. Cloud Run ingress MUST be
  configured as `internal` (or `internal-and-cloud-load-balancing` only
  if a private internal load balancer is used). Direct DNS resolution
  to the `*.run.app` hostname from outside Google's network MUST NOT
  result in a successful TLS handshake to the service.
- **FR-039**: The inference service MUST be reachable from the calling
  backend's GCP project (and only from there). The network topology
  uses **Direct VPC egress on Cloud Run + VPC peering** between the
  forecast project's VPC and the calling backend's VPC, with Cloud NAT
  configured for any required outbound that is not covered by Private
  Google Access. Backend → forecast traffic MUST traverse the private
  VPC path via the peering; egress from the forecast service to GCS,
  Secret Manager, and the OIDC JWKS endpoint MUST use Private Google
  Access (no NAT hop). If the existing `toolsname-agent-sidecar`
  diverges from this primitive (e.g. uses a Serverless VPC Access
  connector or Shared VPC), alignment is handled in a follow-up MR
  rather than blocking this v1.
- **FR-040**: Reachability MUST be defended in depth: even from inside
  the permitted VPC, every request still requires a valid OIDC token
  (FR-018, FR-019, FR-020). Network isolation is additive to, not a
  replacement for, OIDC authentication.
- **FR-041**: In staging and production, `ALLOWED_CALLERS` MUST be a
  non-empty allow-list of caller service-account emails; the service
  MUST refuse to start (non-zero exit at startup, before binding any
  port) when this env var is empty or absent in those environments.
  Local development MAY leave `ALLOWED_CALLERS` unset (defaults to
  open) provided the local-bypass gate from FR-018 is also active
  (`AUTH_BYPASS=1` only with localhost audience and debug logging).
  Terraform MUST refuse `plan`/`apply` against staging or production
  when the corresponding `terraform.tfvars` does not declare an
  allow-list of length ≥ 1.
- **FR-042**: Per `(company_id, computed_object_id)`, the system MUST
  retain only the 10 most-recent training attempts (success or
  failure). After a successful promotion of `latest.json` to version
  `N`, the trainer MUST delete every `v{K}/` directory where `K < N -
  9`. The directory currently named by `latest.json` MUST always
  survive pruning even if its number falls outside the 10-most-recent
  window (defensive guarantee; under normal operation `latest` is the
  most recent successful version and is implicitly in the window).
  Pruning errors are non-fatal: they are logged at WARN with the
  offending paths, do not cause a non-zero exit, and do not roll back
  the promotion. Bucket-level object versioning remains on as
  defense-in-depth (FR-031).
- **FR-043**: The training-trigger queue MUST enforce a per-environment
  concurrency cap on simultaneously-dispatched training tasks:
  `max_concurrent_dispatches = 5` in production, `max_concurrent_dispatches
  = 2` in staging. No per-second dispatch-rate cap is required in v1.
  Caps are declared in `infra/environments/{staging,production}/
  terraform.tfvars` and bound to the `cloud_tasks` Terraform module so
  the values are part of the reviewed plan diff.

### Key Entities

- **Model artifact**: a self-contained, persisted forecasting model for one
  `(company, computed_object, version)` triple, including the calibration
  data needed to produce intervals.
- **Model metadata**: a per-version record describing how the artifact was
  trained — window, declared frequency, target, features, holdout metrics,
  library-version provenance.
- **Latest pointer**: a per-`(company, CO)` record naming the
  currently-promoted version. Atomically replaced; never partially written.
- **Forecast request**: identifiers, horizon, prepared future features,
  optional scenario overrides.
- **Forecast result**: per-period point estimate plus 80% and 95% interval
  bounds, with the resolved model identity and training timestamp.
- **Training job execution**: a single, idempotent invocation that consumes
  staged inputs and produces exactly one new model version.
- **Caller identity**: a service-to-service principal authorized to invoke
  inference; verified by audience claim and (optionally) an explicit
  allow-list.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a `(company, CO)` whose model is in the warm in-memory
  cache, 99% of forecast requests return in under 500 ms end-to-end.
- **SC-002**: For a forecast request that triggers a cold model load,
  99% return in under 3 seconds end-to-end (model artifact ≤ 5 MB).
- **SC-003**: Empirical coverage of 80% intervals on a 10-window backtest
  falls in the band 75%–85% for at least 90% of `(company, CO)` pairs.
- **SC-004**: Empirical coverage of 95% intervals on the same backtest falls
  in 92%–97% for at least 90% of `(company, CO)` pairs.
- **SC-005**: For every `(company, CO)` with at least 24 periods of history,
  the trained model improves on the seasonal-naive baseline (last-year-same
  -period) by at least 10% on sMAPE.
- **SC-006**: A training run for one `(company, CO)` over 24 monthly periods
  × up to 5 series completes within 60 seconds at the 95th percentile.
- **SC-007**: Zero unauthenticated requests succeed on any endpoint other
  than the liveness and readiness probes over any 7-day production window.
- **SC-008**: 100% of unhandled exceptions in both service and training
  modes appear in the error tracker tagged with `company_id` and
  `computed_object_id` within one minute of occurrence.
- **SC-009**: After a new version is promoted, inference requests resolve
  to the new version within one cache-TTL window without service restart,
  for 100% of cache-resident `(company, CO)` pairs.
- **SC-010**: After any failed training retry, durable storage contains
  zero partially-written model artifacts and zero `latest` pointers
  referencing nonexistent versions.
- **SC-011**: The end-to-end integration test from the calling Go API
  against a deployed instance passes on every pull request that touches
  this service.
- **SC-012**: A new contributor can go from a fresh clone to a running
  local service plus a trained sample model in under 15 minutes by
  following only `README.md` and the documents it links to (no tribal
  knowledge required).
- **SC-013**: `README.md` and `docs/architecture.md` both exist on
  `main`, the README contains a working relative link to the
  architecture document, and CI fails any pull request that breaks that
  link.
- **SC-014**: From a fresh clone, `docker compose up` produces a
  reachable `/forecast` endpoint serving a fixture-trained model in
  under 5 minutes, with zero cloud credentials configured.
- **SC-015**: Zero resources in staging or production exist outside
  Terraform's state. Drift (resources that Terraform did not create or
  that someone modified out of band) is detected by a scheduled
  `terraform plan` job and surfaced as a CI failure within 24 hours.
- **SC-016**: A merge request that passes review reaches staging within
  10 minutes of merge to the default branch. A tagged release reaches
  production within 15 minutes of operator approval.
- **SC-017**: No secret value (Sentry DSN, allow-list payload, any other
  credential) is present in any committed file in the repository,
  verified by a secret-scanning job (`gitleaks` or equivalent) in the
  `lint` stage.
- **SC-018**: A direct request from outside any backend project's VPC
  to the staging or production forecast hostname (raw `*.run.app` URL,
  curl-from-laptop) MUST NOT reach the application — verified by a
  scheduled external probe in CI that asserts the request fails before
  TLS terminates at the service.
- **SC-019**: Every Cloud Run env var that holds a secret resolves to a
  Secret Manager reference (`secretKeyRef`-equivalent on Cloud Run);
  zero secrets are stored as plaintext on the Cloud Run resource,
  verified by a Terraform-output assertion in CI.
- **SC-020**: `.env` is present in `.gitignore` and `.dockerignore` on
  `main`; `.env.example` is present and lists every variable the
  application reads — verified by a CI job that diffs the variable list
  against `pydantic-settings` field introspection.

## Assumptions

- **Tech stack pinned by the constitution**: MLForecast + LightGBM in
  Python ≥ 3.11, managed by `uv`. Polars preferred for ETL; pandas at the
  mlforecast API boundary. Quality bar: `ruff` + `mypy --strict` on `src/`,
  `pytest`, `pre-commit`. Run manifest captured per the constitution
  (Principle I).
- **Deployment shape**: managed serverless containers — one HTTP service
  unit, one batch-job unit, both built from the same image. Three
  environments owned by Terraform: **local** (Docker Compose, no cloud
  creds, env via `.env` file), **staging** (its own GCP project, secrets
  in Secret Manager), **production** (its own GCP project, secrets in
  Secret Manager). Cloud-deployed environments use Cloud Run with
  ingress = `internal`; the service is reachable only from the calling
  backend's VPC via the same network topology as the existing
  `toolsname-agent-sidecar` (Shared VPC or VPC peering between the
  backend project and the forecast project, plus Cloud NAT for any
  outbound not covered by Private Google Access). Repository is hosted
  on GitLab; CI/CD is GitLab CI/CD with `terraform plan` on every MR and
  a manual, tag-gated production deploy.
- **Configuration sourcing**: local reads from `.env` (gitignored;
  `.env.example` committed); staging and production read non-secrets
  from Terraform-managed Cloud Run env vars and read secrets from
  Google Secret Manager via the Cloud Run Secret integration. No env
  variable in a cloud environment is set from a GitLab CI variable at
  runtime — CI vars are used only to bootstrap Secret Manager entries
  and to authenticate Terraform.
- **Caller is the broker**: the calling Go API is responsible for (a)
  computing future features (lags, encodings) before invoking inference,
  (b) staging history and feature config to object storage before
  triggering training, (c) all multi-tenancy enforcement and end-user
  authorization, (d) persisting forecast/training run records in its own
  database. This service holds no business logic.
- **Per-`(company, CO)` model isolation**: no cross-company global model in
  v1; isolation is achieved by separate model artifacts per pair. (A global
  model would require a separate data-protection review and is explicitly
  out of scope.)
- **Synchronous request/response only**: no streaming, no long-poll, no
  websockets in v1.
- **Input shapes**: training history is a CSV with `period`, `target`, and
  feature columns; feature config is JSON declaring categorical columns,
  exogenous regressors, target column, and frequency.
- **Storage layout**: per-`(company, CO)` artifact tree on object storage
  with explicit version subdirectories and a `latest` pointer file
  alongside. Bucket-level versioning is enabled as defense-in-depth.
  Retention: 10 most-recent versions per pair, pruned by the trainer
  after successful promotion (clarification 2026-04-28 / FR-042).
- **Authentication**: service-to-service identity tokens issued by the
  cloud platform; this service verifies audience and (optionally) caller
  identity allow-list.
- **Performance envelope**: warm-cache p99 inference ≤ 500 ms; cold-load
  p99 ≤ 3 s; training p95 ≤ 60 s for 24-month × 5-series workloads.
- **Probabilistic intervals via conformal prediction** (mlforecast
  `PredictionIntervals`), not LightGBM-native quantile heads — the
  conformal approach was chosen for calibration guarantees per
  Constitution Principle IV.
- **Local development**: an environment-gated bypass exists for local
  authentication only when running with debug logging, never in any
  deployed environment.

### Stated Defaults Pending Stakeholder Confirmation

These choices are operational rather than scope-shaping; the spec assumes
the defaults below and the planning phase can revisit if needed:

- **Object-storage bucket layout**: one bucket per cloud environment
  (`{prefix}-forecast-models-staging`, `{prefix}-forecast-models-production`);
  bucket created and owned by Terraform per FR-031. Local dev uses an
  in-cluster `fake-gcs-server` named `fake-gcs` on the compose network,
  no real bucket.
- **Error tracker project**: a dedicated Sentry project for this service
  (mirrors the existing sidecar's pattern). One Sentry project shared
  across staging + production with `SENTRY_ENVIRONMENT` distinguishing
  them.
- **Region**: deploy in the same region as the calling backend's primary
  data residency (default `europe-west1`) to keep training-data
  transfer in-region and minimize egress cost. Same region used for both
  staging and production.
- **Training trigger**: Cloud Tasks queue (mirrors the existing pattern
  in the calling backend), provisioned by Terraform per FR-031, rather
  than a Scheduler → Pub/Sub → Job alternative.
- **OpenAPI / `/docs` endpoint**: enabled in local and staging,
  disabled in production by default (env-toggled).
- **GCP project layout**: one GCP project per cloud-deployed
  environment (`{prefix}-forecast-staging`, `{prefix}-forecast-production`).
  Terraform `infra/environments/{staging,production}/` invokes shared
  modules in `infra/modules/` against the matching project.
- **Production deploy gate**: a tag matching `vX.Y.Z` plus explicit
  manual approval in GitLab. No auto-deploy to production from `main`.
- **Network topology**: Direct VPC egress on Cloud Run + VPC peering
  with the calling backend's VPC + Cloud NAT for any non-Google
  outbound + Private Google Access for GCS / Secret Manager / OIDC
  JWKS (clarification 2026-04-28). If the existing
  `toolsname-agent-sidecar` uses a different primitive (e.g. Serverless
  VPC Access connector, Shared VPC), the divergence is reconciled in a
  follow-up MR, not blocking v1.
- **Secret Manager scope**: per-project Secret Manager (one set of
  secrets per cloud env). The Cloud Run service account holds
  `roles/secretmanager.secretAccessor` only on the secrets it actually
  consumes, granted at the secret level (not project level) by Terraform.

These items are tracked here so they are visible to planning. None
changes the spec's correctness or scope — they are settled in
`/speckit-plan` or in a deployment-config PR.
