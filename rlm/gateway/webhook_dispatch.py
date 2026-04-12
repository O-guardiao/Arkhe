"""Shim backward compat -- canonico em rlm.server.webhook_dispatch."""
from rlm.server.webhook_dispatch import (  # noqa: layer  # noqa: F401
    create_webhook_router,
    HookDispatchBody,
    _RateLimiter,
    DEFAULT_HOOK_CLIENT_ID,
    DEFAULT_RATE_LIMIT_RPM,
    DEFAULT_MAX_BODY_BYTES,
)

__all__ = ["create_webhook_router", "HookDispatchBody"]