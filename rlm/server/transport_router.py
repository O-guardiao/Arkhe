"""
transport_router.py — Phase 0.3 da migração Python → TypeScript.

Encapsula a montagem condicional dos roteadores de canal Python (Discord,
WhatsApp, Slack, Webchat, OperatorBridge) em uma única função chamada por
api.py.

Quando ``RLM_GATEWAY_MODE=typescript`` esses routers são pulados; o Gateway
TypeScript (porta 3000) assume todos os canais via WsBridge.

Uso em api.py::

    from rlm.server.transport_router import mount_channel_routers
    mount_channel_routers(app)
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

from rlm.core.structured_log import get_logger

_log = get_logger("transport_router")

# ---------------------------------------------------------------------------
# Importações opcionais de cada gateway
# ---------------------------------------------------------------------------

try:
    from rlm.server.discord_gateway import router as _discord_router
    _HAS_DISCORD_GW = True
except ImportError:
    _discord_router = None
    _HAS_DISCORD_GW = False

try:
    from rlm.server.whatsapp_gateway import router as _whatsapp_router
    _HAS_WHATSAPP_GW = True
except ImportError:
    _whatsapp_router = None
    _HAS_WHATSAPP_GW = False

try:
    from rlm.server.slack_gateway import router as _slack_router
    _HAS_SLACK_GW = True
except ImportError:
    _slack_router = None
    _HAS_SLACK_GW = False

try:
    from rlm.server.webchat import router as _webchat_router
    _HAS_WEBCHAT = True
except ImportError:
    _webchat_router = None
    _HAS_WEBCHAT = False

try:
    from rlm.server.operator_bridge import router as _operator_router
    _HAS_OPERATOR_BRIDGE = True
except ImportError:
    _operator_router = None
    _HAS_OPERATOR_BRIDGE = False


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def mount_channel_routers(app: "FastAPI") -> bool:
    """Monta os roteadores de canal Python na ``app`` FastAPI.

    Retorna ``True`` se os routers foram montados, ``False`` se o modo
    TypeScript estiver ativo (``RLM_GATEWAY_MODE=typescript``).

    Controle via variável de ambiente:

    .. code-block:: bash

        # Python lida com todos os canais (padrão histórico)
        RLM_GATEWAY_MODE=python   # ou omitir a var

        # Gateway TypeScript assume todos os canais
        RLM_GATEWAY_MODE=typescript
    """
    gateway_mode = os.environ.get("RLM_GATEWAY_MODE", "python").lower()

    if gateway_mode == "typescript":
        _log.info(
            "RLM_GATEWAY_MODE=typescript — roteadores de canal Python ignorados; "
            "Gateway TS (porta 3000) gerencia todos os canais via WsBridge."
        )
        return False

    # Discord
    if _HAS_DISCORD_GW and (
        os.environ.get("DISCORD_APP_PUBLIC_KEY")
        or os.environ.get("RLM_DISCORD_SKIP_VERIFY", "").lower() == "true"
    ):
        assert _discord_router is not None
        app.include_router(_discord_router)
        _log.info("✓ Discord gateway montado: POST /discord/interactions")

    # WhatsApp
    if _HAS_WHATSAPP_GW and os.environ.get("WHATSAPP_VERIFY_TOKEN"):
        assert _whatsapp_router is not None
        app.include_router(_whatsapp_router)
        _log.info("✓ WhatsApp gateway montado")

    # Slack
    if _HAS_SLACK_GW and (
        os.environ.get("SLACK_BOT_TOKEN")
        or os.environ.get("SLACK_SIGNING_SECRET")
    ):
        assert _slack_router is not None
        app.include_router(_slack_router)
        _log.info("✓ Slack gateway montado")

    # Webchat
    if _HAS_WEBCHAT:
        assert _webchat_router is not None
        app.include_router(_webchat_router)
        _log.info("✓ Webchat gateway montado")

    # Operator Bridge
    if _HAS_OPERATOR_BRIDGE:
        assert _operator_router is not None
        app.include_router(_operator_router)
        _log.info("✓ OperatorBridge montado")

    return True
