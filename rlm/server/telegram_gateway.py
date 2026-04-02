"""
Telegram Gateway — rlm/server/telegram_gateway.py

**Bridge Architecture**: thin client que faz long-polling do Telegram e
encaminha mensagens para o RLM API Server via HTTP POST /webhook/telegram:{chat_id}.

Toda lógica pesada (Skills, Supervisor, Security Auditor, SIF, EventBus,
sessões persistentes SQLite) fica no api.py. Este módulo só faz:
    1. Long-polling do Telegram
    2. Filtragem local (rate limit, access control, comandos /help /status /reset)
    3. POST payload para o api.py
    4. Envio da resposta de volta ao Telegram

Pode rodar:
    - Embutido no api.py (thread daemon no lifespan) — modo padrão
    - Standalone: python -m rlm.server.telegram_gateway

Segurança:
    - ALLOWED_CHAT_IDS: lista de chat_ids autorizados (vazio = todos)
    - Rate limiting local por chat_id
    - Auth via X-RLM-Token no POST para api.py
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
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

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

    # Bridge — API endpoint
    api_base_url: str = "http://127.0.0.1:8000"  # RLM API Server
    api_timeout_s: int = 120                      # timeout do POST (LLM pode demorar)

    # Segurança
    allowed_chat_ids: list[int] = field(default_factory=list)  # vazio = todos
    max_message_length: int = 4000           # trunca mensagens de entrada

    # Rate limiting
    max_requests_per_min: int = 10           # por chat_id
    rate_window_s: float = 60.0

    # Polling
    poll_timeout_s: int = 30                 # long-polling timeout
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
    result = _tg_request(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    })
    # Fallback: se Markdown falhar (caracteres não escapados), reenvia como texto puro
    if not result.get("ok") and parse_mode == "Markdown":
        return _tg_request(token, "sendMessage", {
            "chat_id": chat_id,
            "text": text,
        })
    return result


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
# Bridge HTTP Client — POST para api.py /webhook
# ---------------------------------------------------------------------------

def _build_auth_headers() -> dict[str, str]:
    """Constrói headers de autenticação para o POST interno."""
    from rlm.server.auth_helpers import build_internal_auth_headers
    return build_internal_auth_headers()


def _bridge_post(
    api_base_url: str,
    client_id: str,
    payload: dict,
    timeout_s: int = 120,
) -> dict:
    """
    Envia payload para POST /webhook/{client_id} do api.py e retorna resposta.

    Returns:
        dict com a resposta JSON do api.py ou {"error": "..."} em caso de falha.
    """
    url = f"{api_base_url.rstrip('/')}/webhook/{client_id}"
    headers = _build_auth_headers()
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib_request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib_error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        logger.error(
            "Bridge POST failed",
            url=url,
            status_code=e.code,
            body_preview=error_body[:300],
        )
        return {"error": f"HTTP {e.code}", "detail": error_body[:300]}
    except Exception as e:
        logger.error("Bridge POST exception", url=url, error=str(e))
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Gateway principal (thin client)
# ---------------------------------------------------------------------------

class TelegramGateway:
    """
    Thin client: long-polling → bridge HTTP → api.py → resposta → Telegram.

    Toda inferência RLM é delegada ao api.py que tem:
    - SessionManager SQLite (persistência + event log)
    - Supervisor (timeout, max_errors)
    - SkillLoader (52+ skills + SIF)
    - Security Auditor
    - EventBus (observabilidade via WebSocket)
    - Hooks

    Args:
        config:    GatewayConfig com token, API URL, rate limits, etc.
        bot_token: Token do bot (override de config.bot_token e TELEGRAM_BOT_TOKEN)
    """

    def __init__(
        self,
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

        # Rate limiting local (protege Telegram API e api.py)
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
            "bridge_errors": 0,
            "start_time": 0.0,
        }

    # ── Comandos locais ───────────────────────────────────────────────────

    def _handle_command(self, chat_id: int, command: str, username: str) -> str | None:
        """Retorna resposta de comando ou None se não for comando."""
        cmd = command.split()[0].lower().lstrip("/").split("@")[0]

        if cmd == "start" or cmd == "help":
            return (
                "🤖 *Arkhe Agent* — Agente de IA recursivo\n\n"
                "Envie qualquer mensagem e o agente processará sua solicitação.\n\n"
                "Comandos disponíveis:\n"
                "• /reset — Reinicia o contexto da conversa\n"
                "• /status — Mostra estatísticas do gateway\n"
                "• /help — Esta mensagem\n\n"
                "_Powered by RLM Engine — Bridge Mode_"
            )
        elif cmd == "reset":
            # Envia comando de reset via bridge — o api.py controla sessões
            result = _bridge_post(
                self.config.api_base_url,
                f"telegram:{chat_id}",
                {"text": "/reset", "from_user": username, "chat_id": chat_id, "command": "reset"},
                timeout_s=15,
            )
            if "error" not in result:
                return "✅ Sessão reiniciada no servidor."
            return "✅ Comando de reset enviado."
        elif cmd == "status":
            uptime = time.time() - self._stats["start_time"]
            return (
                f"📊 *Status do Gateway*\n\n"
                f"• Modo: Bridge → {self.config.api_base_url}\n"
                f"• Mensagens recebidas: {self._stats['messages_received']}\n"
                f"• Processadas (via API): {self._stats['messages_processed']}\n"
                f"• Erros bridge: {self._stats['bridge_errors']}\n"
                f"• Erros totais: {self._stats['errors']}\n"
                f"• Uptime: {int(uptime // 60)}min {int(uptime % 60)}s"
            )
        return None

    # ── Bridge: encaminha para api.py ─────────────────────────────────────

    def _process_via_bridge(self, chat_id: int, text: str, username: str) -> tuple[str, bool]:
        """Encaminha mensagem para o api.py via HTTP e retorna (resposta, already_replied)."""
        if len(text) > self.config.max_message_length:
            text = text[: self.config.max_message_length] + "\n[... truncada ...]"

        client_id = f"telegram:{chat_id}"
        payload = {
            "text": text,
            "from_user": username,
            "chat_id": chat_id,
        }

        result = _bridge_post(
            self.config.api_base_url,
            client_id,
            payload,
            timeout_s=self.config.api_timeout_s,
        )

        if "error" in result:
            self._stats["bridge_errors"] += 1
            error_detail = result.get("detail", result["error"])
            logger.error("Bridge response error", chat_id=chat_id, error=error_detail)
            return f"⚠️ Erro no servidor: {error_detail[:200]}", False

        already_replied = result.get("already_replied", False)

        # Extrair resposta do resultado do api.py
        # O dispatch_runtime_prompt_sync retorna dict com chave "response"
        response = result.get("response", "")
        if not response:
            # Fallback: tenta outras chaves comuns
            response = result.get("result", result.get("output", ""))
        if not response:
            response = json.dumps(result, ensure_ascii=False, indent=2)

        return str(response), already_replied

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

        # Comandos locais
        if text.startswith("/"):
            resp = self._handle_command(chat_id, text, username)
            if resp:
                _send_message(self.token, chat_id, resp)
                return

        # Feedback visual
        if self.config.typing_feedback:
            _send_typing(self.token, chat_id)

        # Processar via bridge em thread separada (não bloqueia polling)
        def process_and_reply():
            try:
                hb = None
                if self.config.typing_feedback:
                    hb = SyncHeartbeat(
                        action=lambda: _send_typing(self.token, chat_id),
                        interval_s=4.0,
                    )
                    hb.start()

                response, already_replied = self._process_via_bridge(chat_id, text, username)

                if hb is not None:
                    hb.dispose()

                self._stats["messages_processed"] += 1

                if self.config.log_messages:
                    logger.info("Resposta enviada", username=username, chat_id=chat_id, text_preview=response[:100])

                if not already_replied:
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
        logger.info(
            "Telegram Gateway iniciado (bridge mode)",
            bot_name=bot_name,
            api_url=self.config.api_base_url,
        )
        logger.info("Rate limit", max_requests_per_min=self.config.max_requests_per_min)

        # Graceful shutdown (apenas quando rodando standalone na main thread)
        if until_stopped:
            try:
                def _shutdown(sig, frame):
                    logger.info("Shutdown solicitado...")
                    self._running = False
                signal.signal(signal.SIGINT, _shutdown)
                signal.signal(signal.SIGTERM, _shutdown)
            except ValueError:
                pass  # signal only works in main thread

        logger.info("Aguardando mensagens... (Ctrl+C para parar)")

        while self._running and until_stopped:
            try:
                self.poll_once()
                self._error_count = 0
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
                sleep_sync(delay)

        self._running = False
        logger.info("Gateway encerrado", messages_processed=self._stats["messages_processed"])

    def run_in_thread(self) -> threading.Thread:
        """Inicia o gateway em thread daemon. Retorna a thread."""
        t = threading.Thread(target=self.run, daemon=True, name="telegram-gateway")
        t.start()
        return t

    def stop(self):
        """Para o loop de polling."""
        self._running = False


# ---------------------------------------------------------------------------
# CLI entrypoint: python -m rlm.server.telegram_gateway
# ---------------------------------------------------------------------------

def main():
    """
    Entrypoint CLI standalone.

    Variáveis de ambiente necessárias:
        TELEGRAM_BOT_TOKEN       — token do bot

    Variáveis opcionais:
        RLM_API_URL              — URL do api.py (padrão: http://127.0.0.1:8000)
        RLM_INTERNAL_TOKEN       — token auth para POST /webhook (ou RLM_WS_TOKEN/RLM_API_TOKEN)
        RLM_ALLOWED_CHATS        — lista CSV de chat_ids permitidos (vazio = todos)
        RLM_RATE_LIMIT           — máx requisições/min por chat (padrão: 10)
        RLM_TG_API_TIMEOUT       — timeout do POST para api.py em segundos (padrão: 120)
    """
    api_url = os.environ.get("RLM_API_URL", "http://127.0.0.1:8000")
    rate_limit = int(os.environ.get("RLM_RATE_LIMIT", "10"))
    api_timeout = int(os.environ.get("RLM_TG_API_TIMEOUT", "120"))

    allowed_raw = os.environ.get("RLM_ALLOWED_CHATS", "")
    allowed_chats: list[int] = (
        [int(x.strip()) for x in allowed_raw.split(",") if x.strip()]
        if allowed_raw else []
    )

    config = GatewayConfig(
        api_base_url=api_url,
        api_timeout_s=api_timeout,
        allowed_chat_ids=allowed_chats,
        max_requests_per_min=rate_limit,
    )

    gw = TelegramGateway(config=config)
    gw.run()


if __name__ == "__main__":
    main()
