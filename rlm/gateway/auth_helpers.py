from __future__ import annotations

import hmac
import os
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from rlm.core.auth import ClientIdentity


def configured_tokens(*env_names: str) -> tuple[str, ...]:
    seen: set[str] = set()
    values: list[str] = []
    for name in env_names:
        token = os.environ.get(name, "").strip()
        if token and token not in seen:
            seen.add(token)
            values.append(token)
    return tuple(values)


def configured_token(*env_names: str) -> str:
    values = configured_tokens(*env_names)
    return values[0] if values else ""


def extract_request_token(
    request: Request,
    *,
    allow_query: bool = False,
    header_names: tuple[str, ...] = ("X-RLM-Token",),
) -> str:
    for header_name in header_names:
        token = request.headers.get(header_name, "").strip()
        if token:
            return token

    auth = request.headers.get("Authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()

    if allow_query:
        return request.query_params.get("token", "").strip()

    return ""


def token_matches(received: str, expected_tokens: tuple[str, ...]) -> bool:
    if not received or not expected_tokens:
        return False
    received_bytes = received.encode()
    return any(hmac.compare_digest(received_bytes, token.encode()) for token in expected_tokens)


def require_token(
    request: Request,
    *,
    env_names: tuple[str, ...],
    scope: str,
    allow_query: bool = False,
    header_names: tuple[str, ...] = ("X-RLM-Token",),
) -> str:
    expected_tokens = configured_tokens(*env_names)
    if not expected_tokens:
        raise HTTPException(status_code=503, detail=f"{scope} authentication is not configured")

    received = extract_request_token(
        request,
        allow_query=allow_query,
        header_names=header_names,
    )
    if not token_matches(received, expected_tokens):
        raise HTTPException(
            status_code=401,
            detail=f"Invalid or missing {scope} token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return received


# ---------------------------------------------------------------------------
# Camada 3: hybrid auth (per-device → legacy fallback)
# ---------------------------------------------------------------------------

def authenticate_request(
    request: Request,
    *,
    db_path: str,
    env_names: tuple[str, ...] = ("RLM_API_TOKEN", "RLM_WS_TOKEN", "RLM_HOOK_TOKEN"),
    scope: str = "api",
    allow_query: bool = False,
    header_names: tuple[str, ...] = ("X-RLM-Token",),
    require: bool = True,
    extra_legacy_tokens: tuple[str, ...] = (),
) -> ClientIdentity | None:
    """
    Tenta autenticar request via tabela clients, fallback para tokens legados.

    Se ``require=True``, levanta 401/503 em falha.
    Se ``require=False``, retorna None silenciosamente.
    """
    from rlm.core.auth import authenticate_or_legacy

    received = extract_request_token(
        request, allow_query=allow_query, header_names=header_names,
    )

    legacy_tokens = configured_tokens(*env_names)
    if extra_legacy_tokens:
        seen = set(legacy_tokens)
        legacy_tokens = legacy_tokens + tuple(
            t for t in extra_legacy_tokens if t and t not in seen
        )

    if not received:
        if require:
            if not legacy_tokens:
                raise HTTPException(status_code=503, detail=f"{scope} authentication is not configured")
            raise HTTPException(
                status_code=401,
                detail=f"Invalid or missing {scope} token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return None

    identity = authenticate_or_legacy(db_path, received, legacy_tokens)
    if identity is None and require:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid or missing {scope} token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return identity


def build_internal_auth_headers() -> dict[str, str]:
    token = configured_token("RLM_INTERNAL_TOKEN", "RLM_WS_TOKEN", "RLM_API_TOKEN")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-RLM-Token"] = token
    return headers