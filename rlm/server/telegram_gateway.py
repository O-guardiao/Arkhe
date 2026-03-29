"""
Telegram Gateway — rlm/server/telegram_gateway.py

Conecta o Telegram Bot como canal de entrada/saída para um agente RLM.

Arquitetura:
    Telegram update → TelegramGateway.poll() → RLM.completion(msg) → reply

Funcionalidades:
    - Long-polling de mensagens (sem webhook necessário)
    - Sessão persistente por chat_id (contexto preservado entre mensagens)
    - Comandos especiais: /reset, /status, /help
    - Feedback de "digitando..." enquanto o RLM processa
    - Truncagem automática de respostas longas (limite Telegram: 4096 chars)
    - Isolamento de sessões: cada chat_id tem seu próprio contexto RLM
    - Graceful shutdown via Ctrl+C

Uso:
    # Configurar variáveis de ambiente
    export TELEGRAM_BOT_TOKEN="123456:ABC..."
    export OPENAI_API_KEY="sk-..."

    # Iniciar gateway
    from rlm.server.telegram_gateway import TelegramGateway
    from rlm.core.rlm import RLM

    rlm = RLM(backend="openai", backend_kwargs={"model_name": "gpt-4o"},
              max_depth=2, persistent=False)
    gw = TelegramGateway(rlm=rlm)
    gw.run()  # inicia loop de polling

    # Ou via CLI:
    # python -m rlm.server.telegram_gateway

Segurança:
    - ALLOWED_CHAT_IDS: lista de chat_ids autorizados (vazio = todos)
    - MAX_MESSAGE_LENGTH: trunca inputs muito longos
    - Rate limiting por chat_id: MAX_REQUESTS_PER_MIN
"""
from __future__ import annotations

import json
import os
import signal
import threading
import time
import traceback
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, cast
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from rlm.core.types import ClientBackend
from rlm.logging import get_runtime_logger
from rlm.server.backoff import GATEWAY_RECONNECT, compute_backoff, sleep_sync
from rlm.server.heartbeat import SyncHeartbeat

logger = get_runtime_logger("telegram_gateway")


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

@dataclass
class GatewayConfig:
    """Configuração do TelegramGateway."""

    # Bot
    bot_token: str = ""                      # TELEGRAM_BOT_TOKEN

    # Sessão RLM
    max_depth: int = 2                       # profundidade máxima do RLM filho
    persistent_per_chat: bool = True         # reutiliza contexto RLM por chat_id
    max_iterations: int = 30                  # max iterações por resposta

    # Segurança
    allowed_chat_ids: list[int] = field(default_factory=list)  # vazio = todos
    max_message_length: int = 4000           # trunca mensagens de entrada

    # Rate limiting
    max_requests_per_min: int = 10           # por chat_id
    rate_window_s: float = 60.0

    # Polling
    poll_timeout_s: int = 30                 # long-polling timeout
    error_backoff_s: float = 5.0             # espera após erro de rede
    max_consecutive_errors: int = 10         # erros antes de parar

    # Resposta
    max_response_length: int = 4000          # limite Telegram: 4096
    typing_feedback: bool = True             # envia "typing..." enquanto processa

    # Logs
    log_messages: bool = True                # loga mensagens recebidas/enviadas


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

def _tg_request(token: str, method: str, data: dict | None = None, timeout: int = 35) -> dict:
    """Faz uma chamada à Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    if data:
        body = json.dumps(data).encode("utf-8")
        req = urllib_request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
    else:
        req = urllib_request.Request(url)

    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib_error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.warn("Telegram API HTTP error", status_code=e.code, body_preview=body[:200])
        return {"ok": False, "description": f"HTTP {e.code}"}
    except Exception as e:
        logger.warn("Telegram API request failed", error=str(e))
        return {"ok": False, "description": str(e)}


def _send_message(token: str, chat_id: int, text: str, parse_mode: str = "Markdown") -> dict:
    """Envia mensagem, truncando se necessário."""
    MAX = 4000
    if len(text) > MAX:
        text = text[:MAX - 50] + "\n\n[... resposta truncada ...]"
    return _tg_request(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    })


def _send_typing(token: str, chat_id: int):
    """Envia indicador 'digitando...' (feedback visual enquanto processa)."""
    _tg_request(token, "sendChatAction", {
        "chat_id": chat_id,
        "action": "typing",
    })


def _get_updates(token: str, offset: int | None, timeout_s: int) -> list[dict]:
    """Long-polling — retorna lista de updates."""
    params: dict[str, Any] = {
        "timeout": timeout_s,
        "allowed_updates": ["message"],
    }
    if offset is not None:
        params["offset"] = offset

    result = _tg_request(token, "getUpdates", params, timeout=timeout_s + 5)
    if result.get("ok"):
        return result.get("result", [])
    return []


# ---------------------------------------------------------------------------
# Session Manager — contexto RLM por chat_id
# ---------------------------------------------------------------------------

class SessionManager:
    """
    Mantém uma instância RLM por chat_id com opção de persistência.

    Cada chat_id recebe um RLM separado com contexto isolado.
    Quando persistent_per_chat=True, o ambiente REPL é reutilizado entre
    mensagens do mesmo chat (memória de conversação no contexto REPL).
    """

    def __init__(self, rlm_factory, config: GatewayConfig):
        self._factory = rlm_factory   # callable() → nova instância RLM
        self._config = config
        self._sessions: dict[int, Any] = {}   # chat_id → RLM instance
        self._lock = threading.Lock()

    def get_or_create(self, chat_id: int) -> Any:
        with self._lock:
            if chat_id not in self._sessions:
                logger.debug("Criando nova sessão RLM", chat_id=chat_id)
                self._sessions[chat_id] = self._factory()
            return self._sessions[chat_id]

    def reset(self, chat_id: int):
        with self._lock:
            if chat_id in self._sessions:
                del self._sessions[chat_id]
                logger.info("Sessão resetada", chat_id=chat_id)

    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Token bucket por chat_id."""

    def __init__(self, max_per_window: int, window_s: float):
        self._max = max_per_window
        self._window = window_s
        self._history: dict[int, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, chat_id: int) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._history[chat_id]
            cutoff = now - self._window
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self._max:
                return False
            q.append(now)
            return True


# ---------------------------------------------------------------------------
# Gateway principal
# ---------------------------------------------------------------------------

class TelegramGateway:
    """
    Gateway que conecta Telegram Bot ao RLM via long-polling.

    Cada mensagem recebida é processada pelo RLM e a resposta é enviada de volta.
    Comandos especiais (/reset, /status, /help) são tratados diretamente.

    Args:
        rlm:          Instância RLM configurada (ou callable factory)
        config:       GatewayConfig com opções de comportamento
        bot_token:    Token do bot (override de config.bot_token e TELEGRAM_BOT_TOKEN)

    Uso básico:
        from rlm.core.rlm import RLM
        from rlm.server.telegram_gateway import TelegramGateway

        rlm = RLM(backend="openai", backend_kwargs={"model_name": "gpt-4o"},
                  persistent=True)
        gw = TelegramGateway(rlm=rlm)
        gw.run()
    """

    def __init__(
        self,
        rlm: Any,
        config: GatewayConfig | None = None,
        bot_token: str | None = None,
    ):
        self.config = config or GatewayConfig()

        # Token: prioridade → argumento > config > env
        self.token = (
            bot_token
            or self.config.bot_token
            or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        )
        if not self.token:
            raise ValueError(
                "Token do Telegram não configurado. "
                "Defina TELEGRAM_BOT_TOKEN ou passe bot_token="
            )

        # RLM: pode ser instância ou factory callable
        if callable(rlm) and not hasattr(rlm, "completion"):
            # É uma factory
            self._rlm_factory = rlm
        else:
            # É uma instância — factory retorna sempre a mesma (sem persistência por chat)
            _rlm_instance = rlm
            self._rlm_factory = lambda: _rlm_instance

        # Sessões isoladas por chat_id
        self._sessions = SessionManager(self._rlm_factory, self.config)

        # Rate limiting
        self._rate_limiter = RateLimiter(
            max_per_window=self.config.max_requests_per_min,
            window_s=self.config.rate_window_s,
        )

        # Estado do polling
        self._offset: int | None = None
        self._running = False
        self._error_count = 0

        # Stats
        self._stats = {
            "messages_received": 0,
            "messages_processed": 0,
            "errors": 0,
            "start_time": 0.0,
        }

    # ── Comandos especiais ────────────────────────────────────────────────

    def _handle_command(self, chat_id: int, command: str, username: str) -> str | None:
        """Retorna resposta de comando ou None se não for comando."""
        cmd = command.split()[0].lower().lstrip("/").split("@")[0]

        if cmd == "start" or cmd == "help":
            return (
                "🤖 *RLM Agent* — Agente de IA recursivo\n\n"
                "Envie qualquer mensagem e o agente processará sua solicitação.\n\n"
                "Comandos disponíveis:\n"
                "• /reset — Reinicia o contexto da conversa\n"
                "• /status — Mostra estatísticas do agente\n"
                "• /help — Esta mensagem\n\n"
                "_Powered by RLM (Recursive Language Model) — MIT_"
            )
        elif cmd == "reset":
            self._sessions.reset(chat_id)
            return "✅ Contexto da conversa reiniciado."
        elif cmd == "status":
            uptime = time.time() - self._stats["start_time"]
            return (
                f"📊 *Status do Agente*\n\n"
                f"• Sessões ativas: {self._sessions.session_count()}\n"
                f"• Mensagens recebidas: {self._stats['messages_received']}\n"
                f"• Mensagens processadas: {self._stats['messages_processed']}\n"
                f"• Erros: {self._stats['errors']}\n"
                f"• Uptime: {int(uptime // 60)}min {int(uptime % 60)}s"
            )
        return None

    # ── Processamento de mensagem ─────────────────────────────────────────

    def _process_message(self, chat_id: int, text: str, username: str) -> str:
        """Passa a mensagem pelo RLM e retorna a resposta."""
        # Truncar inputs muito longos
        if len(text) > self.config.max_message_length:
            text = text[: self.config.max_message_length] + "\n[... mensagem truncada ...]"

        rlm_instance = self._sessions.get_or_create(chat_id)

        try:
            # Suporta RLMSession conversacional (.chat) e bare RLM (.completion)
            if hasattr(rlm_instance, "chat"):
                return rlm_instance.chat(text)
            result = rlm_instance.completion(text)
            # RLMChatCompletion tem .response ou é string diretamente
            if hasattr(result, "response"):
                return result.response
            return str(result)
        except Exception as e:
            logger.error(
                "Erro no RLM ao processar mensagem",
                chat_id=chat_id,
                error=str(e),
                traceback=traceback.format_exc(),
            )
            return f"⚠️ Erro ao processar: {type(e).__name__}: {e}"

    # ── Handler de update ────────────────────────────────────────────────

    def _handle_update(self, update: dict):
        """Processa um update do Telegram."""
        message = update.get("message", {})
        if not message:
            return

        chat_id: int = message["chat"]["id"]
        text: str = message.get("text", "").strip()
        username: str = message.get("from", {}).get("username", "user")

        if not text:
            return  # ignora mensagens sem texto (fotos, stickers, etc.)

        self._stats["messages_received"] += 1

        if self.config.log_messages:
            logger.info("Mensagem recebida", username=username, chat_id=chat_id, text_preview=text[:100])

        # Verificar acesso
        if self.config.allowed_chat_ids and chat_id not in self.config.allowed_chat_ids:
            logger.warn("Chat_id não autorizado", chat_id=chat_id)
            _send_message(self.token, chat_id, "❌ Acesso não autorizado.")
            return

        # Rate limiting
        if not self._rate_limiter.allow(chat_id):
            _send_message(
                self.token, chat_id,
                f"⚠️ Limite de {self.config.max_requests_per_min} mensagens/min atingido. "
                "Aguarde um momento."
            )
            return

        # Comandos especiais
        if text.startswith("/"):
            resp = self._handle_command(chat_id, text, username)
            if resp:
                _send_message(self.token, chat_id, resp)
                return

        # Feedback visual de processamento
        if self.config.typing_feedback:
            _send_typing(self.token, chat_id)

        # Processar via RLM em thread separada (não bloqueia polling)
        def process_and_reply():
            try:
                hb = None
                if self.config.typing_feedback:
                    hb = SyncHeartbeat(
                        action=lambda: _send_typing(self.token, chat_id),
                        interval_s=4.0,
                    )
                    hb.start()

                response = self._process_message(chat_id, text, username)

                if hb is not None:
                    hb.dispose()

                self._stats["messages_processed"] += 1

                if self.config.log_messages:
                    logger.info("Mensagem enviada", username=username, chat_id=chat_id, text_preview=response[:100])

                _send_message(self.token, chat_id, response)

            except Exception as e:
                self._stats["errors"] += 1
                logger.error(
                    "Erro em process_and_reply",
                    chat_id=chat_id,
                    error=str(e),
                    traceback=traceback.format_exc(),
                )
                _send_message(self.token, chat_id, f"⚠️ Erro interno: {e}")

        thread = threading.Thread(target=process_and_reply, daemon=True)
        thread.start()

    # ── Loop de polling ──────────────────────────────────────────────────

    def poll_once(self) -> int:
        """Executa um ciclo de long-polling. Retorna número de updates processados."""
        updates = _get_updates(self.token, self._offset, self.config.poll_timeout_s)

        for update in updates:
            update_id: int = update.get("update_id", 0)
            self._offset = update_id + 1
            try:
                self._handle_update(update)
            except Exception as e:
                logger.error(
                    "Erro ao processar update do Telegram",
                    update_id=update_id,
                    error=str(e),
                    traceback=traceback.format_exc(),
                )

        return len(updates)

    def run(self, until_stopped: bool = True):
        """
        Inicia o loop de polling.

        Args:
            until_stopped: Se True, roda indefinidamente até Ctrl+C ou stop().
        """
        self._running = True
        self._stats["start_time"] = time.time()
        self._error_count = 0

        # Verificar token
        me = _tg_request(self.token, "getMe")
        if not me.get("ok"):
            raise RuntimeError(f"Token inválido ou sem conexão: {me.get('description')}")

        bot_name = me["result"].get("username", "?")
        logger.info("Telegram Gateway iniciado", bot_name=bot_name)
        logger.info("Configuração de sessão", persistent_per_chat=self.config.persistent_per_chat)
        logger.info("Configuração de rate limit", max_requests_per_min=self.config.max_requests_per_min)

        # Graceful shutdown
        def _shutdown(sig, frame):
            logger.info("Shutdown solicitado...")
            self._running = False

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        logger.info("Aguardando mensagens... (Ctrl+C para parar)")

        while self._running and until_stopped:
            try:
                self.poll_once()
                self._error_count = 0  # reset após sucesso
            except KeyboardInterrupt:
                break
            except Exception as e:
                self._error_count += 1
                delay = compute_backoff(GATEWAY_RECONNECT, self._error_count)
                logger.error(
                    "Erro no polling do Telegram",
                    error_count=self._error_count,
                    backoff_s=round(delay, 1),
                    error=str(e),
                    traceback=traceback.format_exc(),
                )
                if self._error_count >= self.config.max_consecutive_errors:
                    logger.error(
                        "Muitos erros consecutivos — parando gateway",
                        max_consecutive_errors=self.config.max_consecutive_errors,
                    )
                    break
                sleep_sync(delay)  # exponential backoff com jitter

        self._running = False
        logger.info("Gateway encerrado", messages_processed=self._stats["messages_processed"])

    def stop(self):
        """Para o loop de polling."""
        self._running = False


# ---------------------------------------------------------------------------
# CLI entrypoint: python -m rlm.server.telegram_gateway
# ---------------------------------------------------------------------------

def main():
    """
    Entrypoint CLI.

    Variáveis de ambiente necessárias:
        TELEGRAM_BOT_TOKEN   — token do bot
        OPENAI_API_KEY       — ou outro backend

    Variáveis opcionais:
        RLM_BACKEND          — "openai" (padrão), "anthropic", etc.
        RLM_MODEL            — nome do modelo (padrão: "gpt-4o")
        RLM_MAX_DEPTH        — profundidade máxima (padrão: 2)
        RLM_ALLOWED_CHATS    — lista CSV de chat_ids permitidos (vazio = todos)
        RLM_RATE_LIMIT       — máx requisições/min por chat (padrão: 10)
    """
    # Lazy import evita erro se RLM não está no path
    try:
        from rlm.core.rlm import RLM
    except ImportError as e:
        print(f"Erro: não foi possível importar RLM — {e}")
        print("Execute dentro do virtualenv do rlm-main.")
        raise SystemExit(1)

    backend = os.environ.get("RLM_BACKEND", "openai")
    model = os.environ.get("RLM_MODEL", "gpt-4o")
    max_depth = int(os.environ.get("RLM_MAX_DEPTH", "2"))
    rate_limit = int(os.environ.get("RLM_RATE_LIMIT", "10"))

    allowed_raw = os.environ.get("RLM_ALLOWED_CHATS", "")
    allowed_chats: list[int] = (
        [int(x.strip()) for x in allowed_raw.split(",") if x.strip()]
        if allowed_raw else []
    )

    # Cada chat_id recebe sua própria instância RLM (fábrica)
    def rlm_factory():
        return RLM(
            backend=cast(ClientBackend, backend),
            backend_kwargs={"model_name": model},
            max_depth=max_depth,
            persistent=True,  # contexto persistente dentro de cada sessão
        )

    config = GatewayConfig(
        allowed_chat_ids=allowed_chats,
        max_requests_per_min=rate_limit,
        persistent_per_chat=True,
    )

    gw = TelegramGateway(rlm=rlm_factory, config=config)
    gw.run()


if __name__ == "__main__":
    main()
