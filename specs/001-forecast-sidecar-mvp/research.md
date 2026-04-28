# Phase 0 Research: Forecast Sidecar MVP

**Feature**: [spec.md](spec.md)
**Plan**: [plan.md](plan.md)
**Date**: 2026-04-28

This document records the technical decisions taken before design (Phase 1)
and resolves every implicit unknown raised by the spec + constitution. The
spec was thorough; the questions below are the *implementation-level*
decisions that the spec deliberately left to planning.

---

## R1. mlforecast feature taxonomy and the inference-time contract

**Question**: mlforecast offers `lags`, `lag_transforms`, `date_features`,
`target_transforms`, `static_features`, plus `historic_exog` vs
`future_exog`. The spec says the *caller* computes future features and we
consume only what they send. Where does each kind of feature get computed?

**Decision**:

| Feature kind | Defined where | Computed at training | Computed at inference |
|---|---|---|---|
| `lags=[1, 3, 6, 12]` | `feature_config.lags` | mlforecast (internal) | mlforecast (from history *embedded in the fitted model* via `MLForecast.fit(...).static_features_` and the lag buffer mlforecast carries) |
| `lag_transforms` (rolling means, etc.) | `feature_config.lag_transforms` | mlforecast | mlforecast (same buffer) |
| `date_features` (`month`, `quarter`, `dayofyear`) | `feature_config.date_features` | mlforecast | mlforecast |
| `target_transforms` (e.g. `Differences([1])`, `LocalStandardScaler`) | `feature_config.target_transforms` | mlforecast | mlforecast (inverse applied on `.predict`) |
| `static_features` (segment, region) | `feature_config.static_features` | mlforecast (kept on the fitted object) | from `static_features` attached to the fitted object |
| `historic_exog` | declared in feature config; values come from history CSV | passed to `fit` | not needed at inference |
| `future_exog` | declared in feature config; values come from caller's `future_features` | passed to `fit` from history | passed to `predict(X_df=...)` |

**What this means for the API contract**: `future_features` from the caller
must contain *only* the columns declared as `future_exog` in the feature
config (plus `unique_id` and `ds`). The lag, lag_transform, date, and
target_transform features are mlforecast's responsibility on both ends —
we do not re-compute them in our code.

**Rationale**: This is exactly what Constitution Principle II requires —
all temporal features routed through mlforecast primitives. The caller's
"prepared future features" misrepresented the design slightly in the spec
input doc; in practice the caller only sends `future_exog`, which is a
much smaller surface than "lags + rolling stats + everything".

**Alternatives considered**:
- *Caller sends pre-computed lags* (literal reading of the input doc) —
  rejected because it duplicates mlforecast's job, leaks training-time
  feature definitions to a different language runtime, and makes
  feature-config changes a cross-repo change.
- *Service re-fetches history from the caller at inference time* — rejected
  because the spec's "no database access" rule rules it out, and it would
  also blow the latency budget.

**Spec impact**: clarify the `future_features` schema in `contracts/openapi.yaml`
to admit only declared `future_exog` columns plus `unique_id` and `ds`.
This is a tightening, not a change in scope.

---

## R2. Conformal prediction intervals — calibration and persistence

**Question**: How does mlforecast persist the conformal residuals so that
intervals at inference are bit-stable across cold loads?

**Decision**: Use `MLForecast.fit(prediction_intervals=PredictionIntervals(
n_windows=10, h=horizon))` at training time. mlforecast stores the
per-step conformal residuals on the `MLForecast` object itself; `joblib`
pickling of the whole `MLForecast` object preserves them. Intervals at
inference time are computed by `MLForecast.predict(h, level=[80, 95])` —
no separate calibration file required.

**Empirical-coverage check** (constitution IV gate): we run
`MLForecast.cross_validation(n_windows=10, h=horizon)` once after fit,
on the same data, to compute empirical coverage at the requested levels
via `utilsforecast.evaluation.coverage`. Those numbers go into
`metadata.json.metrics.coverage_80` / `coverage_95`.

**Rationale**: Conformal calibration is the only way to get *guaranteed*
coverage under exchangeability assumptions (LightGBM-native quantile
heads do not give calibration guarantees and were ruled out by the
constitution). Re-running CV after fit is cheap (the windows are already
in scope) and gives us per-series breakdowns SC-003/SC-004 require.

**Alternatives considered**:
- *LightGBM `objective='quantile'` with q=0.025/0.5/0.975* — rejected per
  Constitution Principle IV, which requires calibrated coverage.
- *Persisting residuals separately* — rejected; mlforecast already
  serializes them in the `MLForecast` object, separate persistence is
  redundant complexity.

---

## R3. Model serialization — `joblib` vs `cloudpickle` vs `pickle`

**Question**: How do we persist a fitted `MLForecast` object — and is it
deterministically loadable across our deploy targets?

**Decision**: `joblib.dump(model, path, compress=3)` for write, `joblib.load`
for read. mlforecast's own quickstart uses `joblib`, which dispatches to
`pickle` under the hood but adds compression and large-array efficiency.

**Cross-version stability**: pickle is sensitive to library versions of the
classes inside the object. To bound this, the trainer pins
`mlforecast==X.Y.Z` and `lightgbm==X.Y.Z` from `uv.lock`; the inference
service uses the *same* `uv.lock`. A library version mismatch between a
trained model and an inferring runtime is a release-engineering bug; we
detect it via `metadata.json.library_versions` vs `importlib.metadata`
at load time and refuse to use the model with a 503 if the major version
mismatches.

**Rationale**: Aligns with mlforecast's own conventions, supports
compression for the ≤ 5 MB target in SC-002, and gives us a structured
way to detect version-skew bugs.

**Alternatives considered**:
- *cloudpickle* — overkill; we never pickle local closures.
- *ONNX export* — LightGBM has an `onnx` exporter, but mlforecast's
  feature-engineering scaffolding is not ONNX-representable, so we'd lose
  conformal residuals and target transforms. Rejected.

---

## R4. GCS atomic promotion of `latest.json`

**Question**: How do we make `latest.json` promotion atomic so concurrent
writers can't leave half-written state (FR-014, FR-015, SC-010)?

**Decision**:
1. Write `model.pkl` and `metadata.json` to `gs://{bucket}/forecasts/{co}/{vN}/`.
2. Use GCS `If-Generation-Match: 0` precondition to write
   `gs://{bucket}/forecasts/{co}/v{N}/_PROMOTED` as a marker — fails fast
   on retry without overwriting. (Alternatively: skip this marker if the
   model files exist and metadata parses; the marker is belt-and-braces.)
3. Read current `latest.json` (if any) including its GCS `generation`.
4. Write a new `latest.json` with `If-Generation-Match: <previous_gen>`
   (or `0` if none exists). On precondition failure, re-read and decide:
   - if the existing `latest.json` already names version `N`, treat as
     idempotent success (we lost a race, the other writer succeeded);
   - if it names `> N`, we are a stale retry — log and exit 0;
   - if it names `< N`, retry our write with the new generation.

GCS object versioning (bucket-level) is enabled as defense-in-depth: even
on a buggy write, the previous `latest.json` is recoverable.

**Rationale**: GCS precondition-on-generation is the documented atomic
write primitive for "compare-and-swap on object". This is the same
pattern Terraform GCS state and `gsutil cp -n` use.

**Alternatives considered**:
- *Cloud Storage object holds (legal hold)* — too coarse; would block
  retries.
- *Postgres-backed pointer* — violates spec's "no DB access" rule for
  this service.
- *Filename-based timestamp-only "latest" via lexicographic listing* —
  rejected because it requires a list-objects call on every inference
  request, eating into the cold-load latency budget (SC-002).

---

## R5. In-process model cache: data structure and TTL semantics

**Question**: Bounded LRU + TTL per spec (FR-022, SC-009). What library?

**Decision**: `cachetools.TTLCache(maxsize=MODEL_CACHE_SIZE, ttl=MODEL_CACHE_TTL_SECONDS)`
keyed on `(company_id, computed_object_id, model_version)`. A second small
cache `cachetools.TTLCache(maxsize=MODEL_CACHE_SIZE, ttl=60)` keyed on
`(company_id, computed_object_id)` resolves "latest" → version with a
short TTL so promotion takes effect within ~1 min, well under the
hourly model TTL (SC-009 holds with margin).

Cache access wrapped in an `asyncio.Lock` per key (using `asyncio.Lock`
from a `defaultdict`) so concurrent first-loads do not double-fetch from
GCS.

**Rationale**: cachetools is small, pure-Python, has the exact TTL+LRU
semantics we need. Async-locked first-load is a well-known pattern
("singleflight"); we keep it inline rather than adding `aiocache`.

**Alternatives considered**:
- *aiocache* — more deps, async-only, we don't need Redis backends.
- *functools.lru_cache* — no TTL.
- *Custom LRU+TTL* — yagni; cachetools is 100 LOC of well-tested code.

---

## R6. OIDC verification — sync vs async, JWKS caching

**Question**: `google.oauth2.id_token.verify_oauth2_token` is synchronous.
Per-request JWKS fetches would blow the latency budget. What do we do?

**Decision**:
1. Wrap the verify call in `asyncio.to_thread(...)` so it doesn't block the
   event loop.
2. Use `google.auth.transport.requests.Request()` with a `requests.Session`
   that has connection pooling enabled.
3. Set `cachecontrol` on the session (or rely on google-auth's built-in
   JWKS caching — it caches public keys in-process). On cold start, one
   JWKS fetch happens; subsequent requests within the cache window do
   not refetch.
4. Implement a FastAPI dependency `verify_oidc_token(request)` that runs
   on every protected route. `/healthz` and `/readyz` are exempt.

**Audience and allow-list checks**: after `verify_oauth2_token` returns
the claims, check `claims["aud"] == settings.expected_audience` and (if
`settings.allowed_callers` is set) `claims["email"] in allowed_callers`.

**Local-dev bypass**: `AUTH_BYPASS=1` honored only when `LOG_LEVEL=debug`
*and* `EXPECTED_AUDIENCE` starts with `http://localhost`. In production
the bypass code path raises at startup if both env vars suggest a real
deployment. (This implements the spec's "never in prod" guarantee with a
*static* check, not a runtime opt-in.)

**Rationale**: This is exactly the pattern from the existing
`toolsname-agent-sidecar`, which is already in production. Mirroring it
keeps ops uniform.

**Alternatives considered**:
- *Run sync verify inline in async route* — rejected, blocks the loop.
- *Custom JWT verifier* — rejected; google-auth is the canonical lib.

---

## R7. FastAPI app lifespan, dependency wiring, and graceful shutdown

**Decision**: Use `FastAPI(lifespan=...)` (the modern replacement for
`@app.on_event`). The `lifespan` async context manager:
1. Initializes structlog (JSON renderer, stdout, level from env).
2. Initializes Sentry (`sentry_sdk.init(...)`) with `traces_sample_rate=0`
   in v1 (errors only).
3. Constructs the GCS client (`storage.Client()`).
4. Constructs the cache (`cachetools.TTLCache`).
5. Yields control.
6. On shutdown: closes the GCS client's underlying session, flushes Sentry.

The cache, GCS client, and settings are stored on `app.state` and exposed
via FastAPI dependencies (`get_cache`, `get_storage`, `get_settings`).
`uvicorn` is started with `--lifespan=on`.

**Rationale**: `lifespan` is FastAPI's documented modern surface; the
`on_event` API is deprecated. `app.state` + DI is the idiomatic way to
share clients without resorting to module-level globals (which would
hurt testability).

---

## R8. Trainer entrypoint — Click vs Typer vs argparse

**Decision**: `click`. Trainer args (per spec §7) are flat strings, no
nested subcommands needed. `click` is mature, has good error messages,
and integrates cleanly with `python -m forecast_sidecar.train_cli`.

The CLI is *thin* — it parses args, calls `model.train.run(args)`, and
maps Python exceptions to exit codes. Business logic lives in
`model/train.py`.

**Exit codes**:
- `0` — success, `latest.json` updated
- `1` — generic / unhandled (re-raised after Sentry capture)
- `2` — bad input (missing files, schema validation failed)
- `3` — training failure (LightGBM error, all-NaN target, etc.)
- `4` — calibration regression (model lost to baseline; constitution IV gate)
- `5` — promotion race lost cleanly (no error; another writer won)

`error.json` is written for codes 2–4. Code 5 logs and exits 0
(idempotent retry success).

**Alternatives considered**: `typer` is fine but pulls in `click` anyway
and adds Pydantic gymnastics for what is a trivial flat arg surface.
`argparse` works but click's UX is nicer for the exit-code/error-mapping.

---

## R9. Container image — single image, two entrypoints

**Decision**: A multi-stage Dockerfile:

1. **Builder stage**: `python:3.11-slim` + `uv`. Run `uv sync --frozen
   --no-dev`. Install LightGBM's runtime deps (`libgomp1`).
2. **Runtime stage**: `python:3.11-slim`. Copy `/app/.venv` and
   `/app/src` from builder. `WORKDIR /app`. `ENV PATH=/app/.venv/bin:$PATH`.
   Drop to a non-root user (`forecast`, uid 10001).
3. `ENTRYPOINT []` — leave empty. `CMD ["uvicorn", "forecast_sidecar.main:app",
   "--host", "0.0.0.0", "--port", "8080"]` is the service default.
4. The trainer is invoked by overriding `CMD` in the Cloud Run Job
   definition: `CMD ["python", "-m", "forecast_sidecar.train_cli", "--", ...]`.

`GIT_SHA` is baked in at build time as an `ARG → ENV` so the run manifest
can reference it (Constitution I).

**Rationale**: Mirrors the existing sidecar pattern; one image is simpler
to deploy, version, and Sentry-tag.

---

## R10. Test strategy and the synthetic-series fixture

**Decision**:
- **Unit tests**: pure functions (features.py, baselines.py, manifest.py,
  cache.py, seeds.py). Fast; default-selected.
- **Contract tests**: validate Pydantic schemas serialize to the
  `contracts/openapi.yaml` shape; validate `feature_config.json` against
  `contracts/feature_config.schema.json`.
- **Integration tests**: drive the trainer end-to-end against a synthetic
  series fixture (24 monthly periods × 3 series with a known seasonal
  pattern + noise) writing to an in-memory `tests/fakes/gcs.py` stand-in.
  Then drive `/forecast` against the same fake and assert (a) the
  response schema, (b) intervals widen with horizon, (c) sMAPE beats
  seasonal-naive on the fixture (constitution IV gate verified in CI).
- **API tests**: FastAPI `TestClient` with `AUTH_BYPASS=1`+localhost,
  asserting all error classes (401, 400, 404, 409, 503) by injecting
  fake GCS states.
- **Markers**: `@pytest.mark.slow` for the full integration smoke run
  (5–15s); excluded from pre-commit per the constitution.

**Synthetic series**: generated in `conftest.py` with a fixed seed so the
sMAPE-beats-baseline assertion is deterministic across CI runs.

---

## R11. Documentation: README + architecture doc + link CI

**Question** (from FR-027 / FR-028 / FR-029 / FR-030, SC-012 / SC-013):
how do we structure these docs and enforce that they stay in sync?

**Decision**:

1. **`README.md`** — landing page. Sections in this order so it skims well:
   - One-paragraph summary (what + why).
   - Stack (one line per major dep, links to upstream).
   - Repository layout (truncated tree pointing at `src/`, `docs/`,
     `tests/`, and the spec dir).
   - **Architecture** (3–5 sentences) → link to
     [docs/architecture.md](../../docs/architecture.md).
   - Local development (the procedure already in `quickstart.md`,
     condensed).
   - Deployment (build → Artifact Registry → Cloud Run service + Job).
   - Contracts (links to `specs/.../contracts/openapi.yaml`,
     `train_cli.md`, `feature_config.schema.json`).
   - Constitution (one-line pointer to `.specify/memory/constitution.md`).

2. **`docs/architecture.md`** — single document, no nested directory in
   v1. Sections:
   1. *System context* — Mermaid `flowchart LR` showing Go backend → this
      service ↔ GCS, plus the Cloud Run Job side.
   2. *Inference request lifecycle* — Mermaid `sequenceDiagram` for the
      `/forecast` happy path: caller → OIDC verify → cache lookup →
      (cache miss → GCS load) → mlforecast.predict → response.
   3. *Training job lifecycle* — Mermaid `sequenceDiagram` for the
      Click CLI: download → validate → fit → calibrate → upload →
      atomic-promote.
   4. *Storage layout & atomic-promotion contract* — text + the GCS tree
      from data-model.md §2.1 + the CAS sequence from R4.
   5. *Cache semantics* — the two-tier TTL design from R5.
   6. *Authentication & identity model* — verifier flow from R6, audience
      and allow-list semantics, the local-dev bypass guardrail.
   7. *Constitution → code map* — table mapping each of the five
      principles to the modules / configs that enforce it (`seeds.py`,
      `manifest.py`, `model/features.py`, `model/baselines.py`,
      `feature_config.schema.json`).
   8. *Out-of-scope and future evolution* — mirrors spec §15 with one
      line each on what would change for hierarchical / cold-start /
      neural / global-model.

   Diagrams use **Mermaid** (rendered natively by GitHub). No external
   build step. ASCII diagrams are acceptable as fallback where Mermaid
   would be cumbersome (e.g. the GCS tree).

3. **Link CI** — `.github/workflows/docs.yml` runs
   [`lychee`](https://github.com/lycheeverse/lychee) on push and on PRs
   that touch `**/*.md`, with config in `.lychee.toml`:
   - Check relative links inside the repo (catches broken
     README → `docs/architecture.md` → contracts pointers).
   - External-link checks limited to a small allow-list of canonical
     upstreams (Nixtla, microsoft/LightGBM, FastAPI, GCP docs) to avoid
     CI flakes from random third-party 404s.
   - Action: `lycheeverse/lychee-action@v2` with cache enabled.

**Rationale**:
- Mermaid is the lowest-friction diagramming approach that renders on
  GitHub without local tooling — keeps the doc reviewable in PRs.
- A single `docs/architecture.md` (vs a `docs/` tree of separate pages)
  is right-sized for v1; we can split later if it grows past ~600 lines.
- `lychee` is fast, cacheable, and supports the allow-list pattern. The
  alternative `markdown-link-check` is unmaintained and Node-based,
  which adds runtime cost we don't otherwise need.

**Alternatives considered**:
- *Sphinx / MkDocs* — way too much machinery for one architecture doc.
  Defer until we have a reason (e.g. an SDK we publish to PyPI).
- *PlantUML diagrams* — needs a render step in CI; rejected for the same
  reason as Sphinx.
- *Per-section docs in a `docs/` tree* (`docs/inference.md`,
  `docs/training.md`, `docs/auth.md`) — rejected for v1; one page is
  easier to keep coherent and discover from the README. Splitting later
  is mechanical.
- *Manual link-checking* — rejected; FR-030 is about automated
  enforcement, and the spec's SC-013 explicitly requires CI to fail on
  broken links.

**Spec impact**: none — this is the implementation choice that satisfies
FR-027 → FR-030 / SC-012 / SC-013. The README and architecture doc
themselves are deliverables of `/speckit-implement`; this research entry
locks the *shape* so tasks can be enumerated against it.

---

## Open items deferred to plan / tasks

- **Region defaults / bucket naming / Sentry project / trainer trigger /
  `/docs` exposure**: tracked in `spec.md` "Stated Defaults Pending
  Stakeholder Confirmation". These are deployment-config decisions that
  do not affect code in `src/`.
- **`/metrics` Prometheus endpoint**: spec says recommended-not-required
  for v1. Deferred to a v1.1 task; `prometheus-fastapi-instrumentator`
  is the planned dep when added.
- **Hierarchical reconciliation, cold-start zero-shot, online learning,
  SHAP, cross-company global model, GPU/neural models**: explicitly out
  of scope per spec §15.

No `[NEEDS CLARIFICATION]` markers remain.
