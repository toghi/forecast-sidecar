"""Cloud Run Job entrypoint. Click CLI per `contracts/train_cli.md`.

Exit codes (from contracts/train_cli.md):
- 0 success
- 1 unhandled error
- 2 bad input (validation, missing files)
- 3 training failure
- 4 calibration regression (model lost to baseline)
- 5 promotion CAS lost cleanly (caller is idempotent; treat as success-noop)
"""

from __future__ import annotations

import io
import json
import sys
import time
import traceback
from datetime import UTC, datetime
from typing import Any

import click
import joblib
import structlog

from forecast_sidecar.config import get_settings
from forecast_sidecar.manifest import compute_config_hash
from forecast_sidecar.model.train import (
    BadHistoryError,
    assemble_metadata,
    run_fit_pipeline,
    validate_history,
)
from forecast_sidecar.observability import init_sentry, init_structlog, tag_sentry_scope
from forecast_sidecar.storage import (
    FileUrlNotAllowedError,
    GCSStorage,
    StorageError,
    StorageUnavailableError,
)

log = structlog.get_logger()


def _phase(name: str, start: float, **extra: Any) -> None:
    log.info(
        "trainer.phase",
        phase=name,
        duration_ms=int((time.monotonic() - start) * 1000),
        **extra,
    )


def _write_error_json(
    storage: GCSStorage,
    *,
    company: str,
    co: str,
    version: int,
    exit_code: int,
    phase: str,
    error: BaseException,
) -> None:
    payload = {
        "version": version,
        "failed_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "exit_code": exit_code,
        "phase": phase,
        "error_type": type(error).__name__,
        "error_message": str(error),
    }
    try:
        storage.write_error_marker(company, co, version, payload)
    except Exception as exc:
        log.warning("trainer.error_marker_write_failed", reason=str(exc))


def _prune_old_versions(
    storage: GCSStorage,
    *,
    company: str,
    co: str,
    promoted_version: int,
    keep_n: int = 10,
) -> list[int]:
    """FR-042: keep the 10 most recent versions; always preserve the one
    named by `latest.json`."""
    try:
        versions = storage.list_versions(company, co)
    except StorageError as exc:
        log.warning("trainer.prune_list_failed", reason=str(exc))
        return []

    pointer = storage.read_latest_pointer(company, co)
    latest_version = pointer[0]["version"] if pointer is not None else promoted_version

    to_keep = set(sorted(versions)[-keep_n:])
    to_keep.add(int(latest_version))
    to_keep.add(promoted_version)

    pruned: list[int] = []
    for v in versions:
        if v in to_keep:
            continue
        try:
            storage.delete_version_dir(company, co, v)
            pruned.append(v)
        except StorageError as exc:
            log.warning("trainer.prune_delete_failed", version=v, reason=str(exc))
    return pruned


@click.command()
@click.option("--company-id", required=True, type=str)
@click.option("--computed-object-id", required=True, type=str)
@click.option("--history-url", required=True, type=str)
@click.option("--feature-config-url", required=True, type=str)
@click.option("--output-version", required=True, type=int)
@click.option("--seed", type=int, default=42)
@click.option("--dry-run", is_flag=True, default=False)
def main(
    company_id: str,
    computed_object_id: str,
    history_url: str,
    feature_config_url: str,
    output_version: int,
    seed: int,
    dry_run: bool,
) -> None:
    settings = get_settings()
    init_structlog(settings.log_level)
    init_sentry(settings.sentry_dsn, settings.sentry_environment, release=settings.git_sha)

    structlog.contextvars.bind_contextvars(
        company_id=company_id,
        computed_object_id=computed_object_id,
        output_version=output_version,
    )

    storage = GCSStorage(settings)
    overall_start = time.monotonic()

    with tag_sentry_scope(
        company_id=company_id, computed_object_id=computed_object_id, mode="train"
    ):
        # ---- download -------------------------------------------------------
        phase_start = time.monotonic()
        try:
            history_bytes = storage.fetch_url_bytes(history_url)
            feature_config_bytes = storage.fetch_url_bytes(feature_config_url)
        except FileUrlNotAllowedError as exc:
            log.error("trainer.bad_input", reason=str(exc))
            sys.exit(2)
        except StorageUnavailableError as exc:
            log.error("trainer.storage_unavailable", reason=str(exc))
            sys.exit(1)
        except StorageError as exc:
            log.error("trainer.bad_input", reason=str(exc))
            sys.exit(2)

        try:
            feature_config = json.loads(feature_config_bytes)
        except json.JSONDecodeError as exc:
            log.error("trainer.bad_input", reason=f"feature_config not JSON: {exc}")
            sys.exit(2)
        _phase("download", phase_start)

        # ---- validate -------------------------------------------------------
        phase_start = time.monotonic()
        try:
            import polars as pl

            history_df_pl = pl.read_csv(io.BytesIO(history_bytes))
            training_df = validate_history(history_df_pl, feature_config)
        except BadHistoryError as exc:
            log.error("trainer.bad_input", reason=str(exc))
            _write_error_json(
                storage,
                company=company_id,
                co=computed_object_id,
                version=output_version,
                exit_code=2,
                phase="validate",
                error=exc,
            )
            sys.exit(2)
        _phase("validate", phase_start, n_rows=len(training_df))

        # ---- fit + calibrate -----------------------------------------------
        phase_start = time.monotonic()
        try:
            mlf, metrics_block, gate = run_fit_pipeline(training_df, feature_config, seed=seed)
        except (ValueError, RuntimeError) as exc:
            log.error("trainer.fit_failed", reason=str(exc), tb=traceback.format_exc())
            _write_error_json(
                storage,
                company=company_id,
                co=computed_object_id,
                version=output_version,
                exit_code=3,
                phase="fit",
                error=exc,
            )
            sys.exit(3)
        _phase("fit", phase_start)

        # ---- baseline gate (Constitution IV) -------------------------------
        if not gate.passed:
            log.error(
                "trainer.calibration_regression",
                model_smape=gate.model_smape,
                baseline_smape=gate.baseline_smape,
                improvement_pct=gate.improvement_pct * 100.0,
                threshold_pct=gate.threshold * 100.0,
            )
            metadata = assemble_metadata(
                version=output_version,
                feature_config=feature_config,
                metrics=metrics_block,
                history_bytes=history_bytes,
                git_sha=settings.git_sha,
                training_df=training_df,
            )
            buf = io.BytesIO()
            joblib.dump(mlf, buf)
            try:
                storage.write_model_bundle(
                    company_id,
                    computed_object_id,
                    output_version,
                    model_bytes=buf.getvalue(),
                    metadata=metadata,
                )
            except StorageError as exc:
                log.error("trainer.upload_failed", reason=str(exc))
            _write_error_json(
                storage,
                company=company_id,
                co=computed_object_id,
                version=output_version,
                exit_code=4,
                phase="calibrate",
                error=RuntimeError(
                    f"model_smape={gate.model_smape:.4f} did not beat "
                    f"baseline_smape={gate.baseline_smape:.4f} by "
                    f"≥{gate.threshold * 100:.0f}%"
                ),
            )
            sys.exit(4)

        # ---- upload + atomic promote ---------------------------------------
        if dry_run:
            log.info(
                "trainer.dry_run_complete",
                duration_ms=int((time.monotonic() - overall_start) * 1000),
                feature_config_hash=compute_config_hash(feature_config),
            )
            sys.exit(0)

        phase_start = time.monotonic()
        metadata = assemble_metadata(
            version=output_version,
            feature_config=feature_config,
            metrics=metrics_block,
            history_bytes=history_bytes,
            git_sha=settings.git_sha,
            training_df=training_df,
        )
        buf = io.BytesIO()
        joblib.dump(mlf, buf)

        try:
            storage.write_model_bundle(
                company_id,
                computed_object_id,
                output_version,
                model_bytes=buf.getvalue(),
                metadata=metadata,
            )
        except StorageError as exc:
            log.error("trainer.upload_failed", reason=str(exc))
            sys.exit(1)
        _phase("upload", phase_start)

        # ---- promote --------------------------------------------------------
        phase_start = time.monotonic()
        existing = storage.read_latest_pointer(company_id, computed_object_id)
        if existing is not None and existing[0]["version"] >= output_version:
            log.info(
                "trainer.promotion_noop",
                reason="latest already at this or higher version",
                latest_version=existing[0]["version"],
            )
            sys.exit(5)
        expected_gen = existing[1] if existing is not None else 0
        promoted = storage.write_latest_pointer_cas(
            company_id,
            computed_object_id,
            {
                "version": output_version,
                "trained_at": metadata["trained_at"],
                "model_path": (
                    f"forecasts/{company_id}/{computed_object_id}/v{output_version}/model.pkl"
                ),
            },
            expected_generation=expected_gen,
        )
        if not promoted:
            log.info("trainer.promotion_lost_race", expected_generation=expected_gen)
            sys.exit(5)
        _phase("promote", phase_start)

        # ---- prune (FR-042) ------------------------------------------------
        phase_start = time.monotonic()
        pruned = _prune_old_versions(
            storage, company=company_id, co=computed_object_id, promoted_version=output_version
        )
        _phase("prune", phase_start, pruned_versions=pruned)

        log.info(
            "trainer.done",
            duration_ms=int((time.monotonic() - overall_start) * 1000),
            output_version=output_version,
            improvement_pct=gate.improvement_pct * 100.0,
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
