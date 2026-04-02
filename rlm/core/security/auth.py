"""
RLM JWT Authentication — Phase 9.4 (CiberSeg)

Zero-dependency JWT implementation using Python stdlib only.
Provides ``issue_token()`` and ``verify_token()`` for per-device/client
authentication with expiration and profile injection.

Token format: base64url(header).base64url(payload).HMAC-SHA256(signature)

Security properties:
  - HMAC-SHA256 signature (timing-safe comparison via ``hmac.compare_digest``)
  - Mandatory expiration (``exp`` claim)
  - No ``alg=none`` accepted
  - Secret read from ``RLM_JWT_SECRET`` env var (minimum 32 chars enforced)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALGORITHM = "HS256"
_HEADER = {"alg": _ALGORITHM, "typ": "JWT"}
_HEADER_B64 = base64.urlsafe_b64encode(
    json.dumps(_HEADER, separators=(",", ":")).encode()
).rstrip(b"=")

_MIN_SECRET_LENGTH = 32
_DEFAULT_TTL_HOURS = 24


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> bytes:
    """URL-safe base64 encode without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _b64url_decode(data: bytes | str) -> bytes:
    """URL-safe base64 decode with padding restoration."""
    if isinstance(data, str):
        data = data.encode("ascii")
    # Restore padding
    padding = 4 - len(data) % 4
    if padding != 4:
        data += b"=" * padding
    return base64.urlsafe_b64decode(data)


def _get_secret() -> bytes:
    """Read and validate the JWT secret from environment."""
    secret = os.environ.get("RLM_JWT_SECRET", "")
    if len(secret) < _MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"RLM_JWT_SECRET must be at least {_MIN_SECRET_LENGTH} characters. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    return secret.encode("utf-8")


def _sign(header_payload: bytes, secret: bytes) -> bytes:
    """Compute HMAC-SHA256 signature."""
    return _b64url_encode(
        hmac.new(secret, header_payload, hashlib.sha256).digest()
    )


def hash_token(raw_token: str) -> str:
    """SHA-256 hash of a raw token/API key for storage.

    Never store raw tokens — store this hash instead and compare
    incoming tokens against it.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def issue_token(
    client_id: str,
    profile: str = "default",
    permissions: list[str] | None = None,
    ttl_hours: float = _DEFAULT_TTL_HOURS,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Issue a signed JWT for a client.

    Args:
        client_id:   Unique identifier for the client/device.
        profile:     Behavior profile (e.g. ``"default"``, ``"readonly"``, ``"admin"``).
        permissions: List of permission strings (e.g. ``["execute", "read"]``).
        ttl_hours:   Token validity in hours (default 24).
        extra_claims: Additional claims to embed in the payload.

    Returns:
        Signed JWT string: ``header.payload.signature``

    Raises:
        RuntimeError: If ``RLM_JWT_SECRET`` is not set or too short.
    """
    secret = _get_secret()
    now = time.time()

    payload: dict[str, Any] = {
        "sub": client_id,
        "prf": profile,
        "prm": permissions or ["execute", "read"],
        "iat": int(now),
        "exp": int(now + ttl_hours * 3600),
    }
    if extra_claims:
        payload.update(extra_claims)

    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":")).encode()
    )

    header_payload = _HEADER_B64 + b"." + payload_b64
    signature = _sign(header_payload, secret)

    return (header_payload + b"." + signature).decode("ascii")


def verify_token(token: str) -> dict[str, Any] | None:
    """Verify a JWT and return the payload if valid.

    Args:
        token: The raw JWT string.

    Returns:
        Decoded payload dict if valid, ``None`` if invalid or expired.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        header_b64, payload_b64, sig_b64 = parts

        # Verify header — only accept HS256
        header = json.loads(_b64url_decode(header_b64))
        if header.get("alg") != _ALGORITHM:
            return None

        # Verify signature (timing-safe comparison)
        secret = _get_secret()
        header_payload = f"{header_b64}.{payload_b64}".encode("ascii")
        expected_sig = _sign(header_payload, secret)

        if not hmac.compare_digest(
            expected_sig,
            sig_b64.encode("ascii"),
        ):
            return None

        # Decode payload
        payload = json.loads(_b64url_decode(payload_b64))

        # Check expiration
        exp = payload.get("exp")
        if exp is None or time.time() > exp:
            return None

        return payload

    except Exception:
        return None
