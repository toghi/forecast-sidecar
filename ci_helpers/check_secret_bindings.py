"""T087 / SC-019 — assert that secret-typed env bindings on Cloud Run
resources are bound by reference (`secret_key_ref`), never as plaintext.

Reads a `terraform plan -json` output and walks each
`google_cloud_run_v2_service` / `google_cloud_run_v2_job` planned change.
For every container env entry whose name matches a known-secret pattern,
fail unless the binding has a `value_source.secret_key_ref` block.

Invoked from `.github/workflows/lint.yml`:

    uv run python ci_helpers/check_secret_bindings.py infra/environments/staging/plan.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# Env-var names that MUST come from Secret Manager. Add to this list as new
# secrets are introduced.
_SECRET_NAME_PATTERNS = [
    re.compile(r"^SENTRY_DSN$"),
    re.compile(r".*_API_KEY$"),
    re.compile(r".*_TOKEN$"),
    re.compile(r".*_PASSWORD$"),
    re.compile(r".*_CREDENTIALS$"),
]

_TARGET_TYPES = {
    "google_cloud_run_v2_service",
    "google_cloud_run_v2_job",
}


def _is_secret_name(name: str) -> bool:
    return any(p.match(name) for p in _SECRET_NAME_PATTERNS)


def _walk_envs(resource: dict[str, Any]) -> list[dict[str, Any]]:
    """Yield each env entry across all template containers."""
    out: list[dict[str, Any]] = []
    template = resource.get("template") or {}
    if isinstance(template, list):
        template = template[0] if template else {}

    inner = template.get("template") if isinstance(template, dict) else None
    if inner:
        if isinstance(inner, list):
            inner = inner[0] if inner else {}
        containers = inner.get("containers") if isinstance(inner, dict) else None
    else:
        containers = template.get("containers") if isinstance(template, dict) else None

    if not containers:
        return out
    for container in containers:
        for env in container.get("env", []) or []:
            out.append(env)
    return out


def check(plan: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for change in plan.get("resource_changes", []):
        if change.get("type") not in _TARGET_TYPES:
            continue
        actions = set(change.get("change", {}).get("actions", []))
        if not (actions & {"create", "update", "no-op", "read"}):
            continue
        after = change.get("change", {}).get("after") or {}
        for env in _walk_envs(after):
            name = env.get("name")
            if not name or not _is_secret_name(name):
                continue
            value_source = env.get("value_source") or env.get("value_source", [])
            if isinstance(value_source, list):
                value_source = value_source[0] if value_source else {}
            if not (isinstance(value_source, dict) and value_source.get("secret_key_ref")):
                failures.append(
                    f"{change['address']}: env var {name!r} must use "
                    f"value_source.secret_key_ref (got plaintext)"
                )
    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_secret_bindings.py <plan.json>", file=sys.stderr)
        return 2
    plan_path = Path(sys.argv[1])
    if not plan_path.exists():
        print(f"FAIL: {plan_path} not found", file=sys.stderr)
        return 2
    plan = json.loads(plan_path.read_text())
    failures = check(plan)
    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return 1
    print(f"OK: all secret env bindings in {plan_path} use secret_key_ref")
    return 0


if __name__ == "__main__":
    sys.exit(main())
