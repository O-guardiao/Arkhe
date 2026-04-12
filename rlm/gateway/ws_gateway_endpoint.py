"""Shim backward compat -- canonico em rlm.server.ws_gateway_endpoint."""
from rlm.server.ws_gateway_endpoint import router  # noqa: layer  # noqa: F401

__all__ = ["router"]