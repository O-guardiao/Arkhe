"""Subsistema canônico de gateways Python.

Centraliza adapters de canal, protocolos de transporte e montagem de rotas
antes espalhados em ``rlm.server``.

Imports rápidos por canal:
    from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig
    from rlm.gateway.discord_gateway  import DiscordGateway
    from rlm.gateway.slack_gateway    import SlackGateway
    from rlm.gateway.whatsapp_gateway import WhatsAppGateway
    from rlm.gateway.webchat          import WebChatAdapter
    from rlm.gateway.transport_router import TransportRouter
"""
from rlm.gateway.envelope import Envelope
from rlm.gateway.auth_helpers import build_internal_auth_headers
from rlm.gateway.backoff import compute_backoff, sleep_sync, GATEWAY_RECONNECT

__all__ = [
    "Envelope",
    "build_internal_auth_headers",
    "compute_backoff",
    "sleep_sync",
    "GATEWAY_RECONNECT",
]
