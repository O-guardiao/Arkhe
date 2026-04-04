"""
Channel Bootstrap — inicialização unificada de toda infraestrutura multichannel.

Antes deste módulo, a criação do ChannelStatusRegistry, MessageBus,
DeliveryWorker e registro de adapters estava embutida no lifespan do
api.py.  O resultado: qualquer runtime que NÃO passasse pelo lifespan
(ex: TUI local, worker, script, teste) ficava sem multichannel.

``bootstrap_channel_infrastructure()`` extrai essa lógica para uma
única função reutilizável.  Tanto o lifespan do servidor quanto o
``build_local_workbench_runtime()`` chamam a mesma função.

Para adicionar um canal futuro:
  1. Crie um ChannelAdapter em rlm/server/ ou rlm/plugins/
  2. Adicione uma entrada em ``_CHANNEL_DESCRIPTORS``
  3. Pronto — bootstrap detecta env vars e auto-registra
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from rlm.core.comms.channel_probe import NullProber
from rlm.core.comms.channel_status import (
    ChannelStatusRegistry,
    init_channel_status_registry,
)
from rlm.core.comms.delivery_worker import DeliveryWorker
from rlm.core.comms.message_bus import MessageBus, init_message_bus
from rlm.core.comms.outbox import OutboxStore
from rlm.core.comms.routing_policy import RoutingPolicy
from rlm.core.structured_log import get_logger
from rlm.plugins.channel_registry import ChannelAdapter, ChannelRegistry

_log = get_logger("channel_bootstrap")


# ── Channel Descriptor ───────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ChannelDescriptor:
    """Declaração de um canal — bootstrap usa para auto-discovery."""
    channel_id: str
    env_keys: tuple[str, ...]
    adapter_factory: str | None = None
    prober_factory: str | None = None
    gateway_module: str | None = None
    always_registered: bool = False


# Registro declarativo de TODOS os canais conhecidos.
# Para adicionar um canal futuro: basta adicionar uma entrada aqui.
_CHANNEL_DESCRIPTORS: tuple[ChannelDescriptor, ...] = (
    ChannelDescriptor(
        channel_id="telegram",
        env_keys=("TELEGRAM_BOT_TOKEN",),
        adapter_factory="rlm.plugins.telegram:TelegramAdapter",
        prober_factory="rlm.core.comms.channel_probe:TelegramProber",
        gateway_module="rlm.server.telegram_gateway",
    ),
    ChannelDescriptor(
        channel_id="discord",
        env_keys=("DISCORD_APP_PUBLIC_KEY", "RLM_DISCORD_SKIP_VERIFY"),
        prober_factory="rlm.core.comms.channel_probe:DiscordProber",
        gateway_module="rlm.server.discord_gateway",
    ),
    ChannelDescriptor(
        channel_id="whatsapp",
        env_keys=("WHATSAPP_VERIFY_TOKEN",),
        gateway_module="rlm.server.whatsapp_gateway",
    ),
    ChannelDescriptor(
        channel_id="slack",
        env_keys=("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET"),
        gateway_module="rlm.server.slack_gateway",
    ),
    ChannelDescriptor(
        channel_id="webchat",
        env_keys=(),
        adapter_factory="rlm.server.webchat:WebChatAdapter",
        gateway_module="rlm.server.webchat",
    ),
    ChannelDescriptor(
        channel_id="tui",
        env_keys=(),
        adapter_factory="rlm.server.operator_bridge:TuiAdapter",
        always_registered=True,
    ),
)


# ── Infrastructure result ────────────────────────────────────────────────

@dataclass(slots=True)
class ChannelInfrastructure:
    """Todas as dependências multichannel, inicializadas juntas."""
    csr: ChannelStatusRegistry
    message_bus: MessageBus
    outbox: OutboxStore
    delivery_worker: DeliveryWorker
    registered_channels: list[str] = field(default_factory=list)
    use_message_bus: bool = False

    def close(self) -> None:
        """Encerra DeliveryWorker. Chamado no shutdown."""
        self.delivery_worker.stop()


# ── Lazy module+attr import ──────────────────────────────────────────────

def _import_attr(dotted: str) -> Any:
    """Importa 'module.path:ClassName' -> classe."""
    module_path, attr_name = dotted.rsplit(":", 1)
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, attr_name)


def _try_import_module(module_path: str) -> bool:
    """Retorna True se o módulo é importável."""
    try:
        import importlib
        importlib.import_module(module_path)
        return True
    except ImportError:
        return False


# ── Channel detection ────────────────────────────────────────────────────

def _is_channel_configured(desc: ChannelDescriptor) -> bool:
    """Verifica env vars para saber se o canal está configurado."""
    if desc.always_registered:
        return True
    if not desc.env_keys:
        # Canais sem env_keys (webchat) dependem do gateway_module estar importável
        if desc.gateway_module:
            return _try_import_module(desc.gateway_module)
        return True
    return any(os.environ.get(k, "").strip() for k in desc.env_keys)


def _is_gateway_available(desc: ChannelDescriptor) -> bool:
    """Verifica se o módulo do gateway está disponível."""
    if not desc.gateway_module:
        return True
    return _try_import_module(desc.gateway_module)


# ── Bootstrap ────────────────────────────────────────────────────────────

def bootstrap_channel_infrastructure(
    *,
    session_manager: Any,
    event_bus: Any,
    db_path: str = "rlm_sessions.db",
    start_gateways: bool = False,
    config: Any | None = None,
) -> ChannelInfrastructure:
    """
    Inicializa TODA a infraestrutura multichannel de uma vez.

    Chamado pelo lifespan do api.py (start_gateways=True) e pelo
    build_local_workbench_runtime (start_gateways=False).

    Parameters
    ----------
    session_manager : SessionManager
        Instância compartilhada.
    event_bus : RLMEventBus
        Instância compartilhada para observabilidade.
    db_path : str
        Caminho do SQLite para Outbox.
    start_gateways : bool
        Se True, inicia gateways ativos (ex: Telegram polling thread).
        Falso para TUI local mode e testes.
    config : opcional
        Config object (rlm.toml parsed). Usado para message_bus.enabled.
    """
    _log.info("Bootstrapping channel infrastructure", start_gateways=start_gateways)

    # 1. ChannelStatusRegistry (singleton)
    csr = init_channel_status_registry()

    # 2. Outbox + MessageBus (singleton)
    outbox = OutboxStore(db_path=db_path)
    bus = init_message_bus(
        outbox=outbox,
        routing_policy=RoutingPolicy(),
        event_bus=event_bus,
    )

    # 3. DeliveryWorker
    delivery_worker = DeliveryWorker(
        outbox=outbox,
        channel_registry=ChannelRegistry,
        event_bus=event_bus,
    )

    # 4. Feature flag: MessageBus routing
    use_bus = (
        os.environ.get("RLM_USE_MESSAGE_BUS", "").lower() in ("true", "1", "yes")
    )
    if config is not None:
        mb_cfg = getattr(config, "message_bus", None)
        if mb_cfg is not None and getattr(mb_cfg, "enabled", False):
            use_bus = True

    # 5. Registra canais declarativamente
    registered: list[str] = []

    for desc in _CHANNEL_DESCRIPTORS:
        configured = _is_channel_configured(desc)
        gw_available = _is_gateway_available(desc)

        # Adapter registration in ChannelRegistry
        if desc.adapter_factory and configured and gw_available:
            try:
                adapter_cls = _import_attr(desc.adapter_factory)
                # Tenta com session_manager; se o adapter não aceitar, tenta sem args.
                try:
                    adapter = adapter_cls(session_manager)
                except TypeError:
                    adapter = adapter_cls()
                ChannelRegistry.register(desc.channel_id, adapter)
                _log.info(f"Adapter registered: {desc.channel_id}")
            except Exception as exc:
                _log.warn(f"Adapter registration failed: {desc.channel_id}", error=str(exc))

        # CSR registration (always — even unconfigured channels appear as disabled)
        prober = NullProber(desc.channel_id)
        if desc.prober_factory and configured:
            try:
                prober_cls = _import_attr(desc.prober_factory)
                # TelegramProber needs token, DiscordProber needs bot_token
                if desc.channel_id == "telegram":
                    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
                    if token:
                        prober = prober_cls(token=token)
                elif desc.channel_id == "discord":
                    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
                    if token:
                        prober = prober_cls(bot_token=token)
                    # else: NullProber stays
            except Exception as exc:
                _log.warn(f"Prober creation failed: {desc.channel_id}", error=str(exc))

        meta: dict[str, Any] = {}
        if desc.channel_id == "telegram" and configured:
            api_port = os.environ.get("RLM_API_PORT", os.environ.get("PORT", "8000"))
            meta["api_base_url"] = f"http://127.0.0.1:{api_port}"

        csr.register(
            desc.channel_id,
            prober=prober,
            configured=configured,
            enabled=configured,
            meta=meta or None,
        )

        if configured and gw_available:
            csr.mark_running(desc.channel_id)
            registered.append(desc.channel_id)
        else:
            _log.info(
                f"Channel {desc.channel_id}: configured={configured}, "
                f"gw_available={gw_available}"
            )

    summary = csr.summary()
    _log.info(
        f"Channel infrastructure ready: {summary['total']} channels "
        f"({summary['running']} running, {summary['healthy']} healthy)"
    )

    return ChannelInfrastructure(
        csr=csr,
        message_bus=bus,
        outbox=outbox,
        delivery_worker=delivery_worker,
        registered_channels=registered,
        use_message_bus=use_bus,
    )
