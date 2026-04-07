"""
transport_router.py — Phase 0.3 da migração Python → TypeScript.

Encapsula a montagem condicional dos roteadores de canal Python (Discord,
WhatsApp, Slack, Webchat, OperatorBridge) em uma única função chamada por
api.py.

Quando ``RLM_GATEWAY_MODE=typescript`` os gateways de canal Python são
pulados; o Gateway TypeScript (porta 3000) assume todos os canais via
WsBridge. A bridge do operador continua no brain Python para o workbench
TUI consumir o payload nativo de runtime.

Uso em api.py::

    from rlm.server.transport_router import mount_channel_routers
    mount_channel_routers(app)
"""
from __future__ import annotations

import os
from typing import Any, cast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

from rlm.core import structured_log

_structured_log = cast(Any, structured_log)
_get_logger = _structured_log.get_logger
_log = _get_logger("transport_router")

# ---------------------------------------------------------------------------
# Importações opcionais de cada gateway
# ---------------------------------------------------------------------------

try:
    from rlm.server.discord_gateway import router as _discord_router
    _has_discord_gw = True
except ImportError:
    _discord_router = None
    _has_discord_gw = False

try:
    from rlm.server.whatsapp_gateway import router as _whatsapp_router
    _has_whatsapp_gw = True
except ImportError:
    _whatsapp_router = None
    _has_whatsapp_gw = False

try:
    from rlm.server.slack_gateway import router as _slack_router
    _has_slack_gw = True
except ImportError:
    _slack_router = None
    _has_slack_gw = False

try:
    from rlm.server.webchat import router as _webchat_router
    _has_webchat = True
except ImportError:
    _webchat_router = None
    _has_webchat = False

try:
    from rlm.server.operator_bridge import router as _operator_router
    _has_operator_bridge = True
except ImportError:
    _operator_router = None
    _has_operator_bridge = False


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def mount_channel_routers(app: "FastAPI") -> bool:
    """Monta os roteadores de canal Python na ``app`` FastAPI.

    Retorna ``True`` se os gateways Python foram montados, ``False`` se o
    modo TypeScript estiver ativo (``RLM_GATEWAY_MODE=typescript``).

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
            "RLM_GATEWAY_MODE=typescript — gateways de canal Python ignorados; "
            "Gateway TS (porta 3000) gerencia os canais via WsBridge."
        )
        if _has_operator_bridge:
            assert _operator_router is not None
            app.include_router(_operator_router)
            _log.info(
                "✓ OperatorBridge montado no brain Python para o workbench TUI"
            )
        return False

    # Discord
    if _has_discord_gw and (
        os.environ.get("DISCORD_APP_PUBLIC_KEY")
        or os.environ.get("RLM_DISCORD_SKIP_VERIFY", "").lower() == "true"
    ):
        assert _discord_router is not None
        app.include_router(_discord_router)
        _log.info("✓ Discord gateway montado: POST /discord/interactions")

    # WhatsApp
    if _has_whatsapp_gw and os.environ.get("WHATSAPP_VERIFY_TOKEN"):
        assert _whatsapp_router is not None
        app.include_router(_whatsapp_router)
        _log.info("✓ WhatsApp gateway montado")

    # Slack
    if _has_slack_gw and (
        os.environ.get("SLACK_BOT_TOKEN")
        or os.environ.get("SLACK_SIGNING_SECRET")
    ):
        assert _slack_router is not None
        app.include_router(_slack_router)
        _log.info("✓ Slack gateway montado")

    # Webchat
    if _has_webchat:
        assert _webchat_router is not None
        app.include_router(_webchat_router)
        _log.info("✓ Webchat gateway montado")

    # Operator Bridge
    if _has_operator_bridge:
        assert _operator_router is not None
        app.include_router(_operator_router)
        _log.info("✓ OperatorBridge montado")

    return True
