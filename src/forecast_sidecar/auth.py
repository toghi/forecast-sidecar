"""OIDC verification dependency for the FastAPI app (FR-018, FR-019, FR-020, FR-040, FR-041)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from forecast_sidecar.config import Settings, get_settings


@dataclass(frozen=True)
class Claims:
    aud: str
    email: str | None
    sub: str
    raw: dict[str, Any]


_BYPASS_CLAIMS = Claims(
    aud="local-bypass",
    email="local-dev@bypass.invalid",
    sub="local-bypass",
    raw={"bypass": True},
)


def _http_request_factory() -> google_requests.Request:
    return google_requests.Request()


async def _verify_with_google(token: str, audience: str) -> dict[str, Any]:
    return await asyncio.to_thread(
        id_token.verify_oauth2_token,
        token,
        _http_request_factory(),
        audience,
    )


async def verify_oidc_token(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Claims:
    s = settings

    if s.auth_bypass and s.is_local_audience and s.log_level == "debug":
        return _BYPASS_CLAIMS

    raw = request.headers.get("authorization") or request.headers.get("Authorization")
    if not raw or not raw.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token", "detail": "missing or non-bearer Authorization"},
        )

    token = raw.split(" ", 1)[1].strip()

    try:
        claims = await _verify_with_google(token, s.expected_audience)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token", "detail": str(exc)},
        ) from exc

    aud = claims.get("aud")
    if aud != s.expected_audience:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token", "detail": "audience mismatch"},
        )

    email = claims.get("email")
    if s.allowed_callers and email not in s.allowed_callers:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token", "detail": "caller not in allow-list"},
        )

    return Claims(
        aud=str(aud),
        email=str(email) if email else None,
        sub=str(claims.get("sub", "")),
        raw=claims,
    )
