"""
WS Gateway Endpoint — Endpoint WebSocket para comunicação entre Gateway TS e Brain Python.

Implementa o protocolo definido em schemas/ws-protocol.v1.json.

Endpoint: GET /ws/gateway  (upgrade para WebSocket)

Protocolo:
  Gateway→Brain:  { "type": "envelope", "data": { ...Envelope v1... } }
  Brain→Gateway:  { "type": "envelope", "data": { ...Envelope v1... } }
  Gateway→Brain:  { "type": "ack",      "id": "...", "delivered": true }
  Brain→Gateway:  { "type": "ping",     "timestamp": "..." }
  Gateway→Brain:  { "type": "pong",     "timestamp": "..." }

Segurança:
  - Token obrigatório via query-param ?token=... ou header Authorization: Bearer ...
  - Token configurado via env var RLM_GATEWAY_TOKEN (fallback: RLM_WS_TOKEN)
  - Conexão sem token válido recebe 4401 WebSocket close code

Compatibilidade:
  - Cada Envelope inbound é despachado via dispatch_runtime_prompt_sync
  - A resposta brain é empacotada em Envelope outbound com os campos target_*
    preenchidos a partir do Envelope inbound (source↔target invertidos)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from rlm.server.envelope import Envelope, create_reply_envelope

logger = logging.getLogger("rlm.ws_gateway")

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

_PING_INTERVAL_S = float(os.environ.get("RLM_WS_PING_INTERVAL", "20"))
_DISPATCH_TIMEOUT_S = float(os.environ.get("RLM_WS_DISPATCH_TIMEOUT", "120"))

# ---------------------------------------------------------------------------
# Router FastAPI
# ---------------------------------------------------------------------------

router = APIRouter(tags=["ws-gateway"])


# ---------------------------------------------------------------------------
# Autenticação WebSocket
# ---------------------------------------------------------------------------

def _get_expected_token() -> str | None:
    """
    Lê o token esperado do ambiente.

    Verifica (em ordem de prioridade):
      1. RLM_GATEWAY_TOKEN — token dedicado para o gateway TS
      2. RLM_WS_TOKEN       — token legado compartilhado
    """
    return (
        os.environ.get("RLM_GATEWAY_TOKEN", "").strip()
        or os.environ.get("RLM_WS_TOKEN", "").strip()
        or None
    )


def _extract_bearer_token(authorization: str | None) -> str | None:
    """Extrai token de 'Authorization: Bearer <token>'."""
    if not authorization:
        return None
    parts = authorization.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def _authenticate_ws(websocket: WebSocket) -> bool:
    """
    Verifica autenticação WebSocket.

    Aceita token via:
      - Query param: ?token=<valor>
      - Header Authorization: Bearer <valor>

    Se RLM_GATEWAY_TOKEN e RLM_WS_TOKEN não estiverem configurados,
    aceita conexão com aviso (modo desenvolvimento local).
    """
    expected = _get_expected_token()
    if not expected:
        logger.warning(
            "WS Gateway: RLM_GATEWAY_TOKEN não configurado. "
            "Conexões sem autenticação serão aceitas. NÃO use em produção."
        )
        return True

    # Verificar query param
    token_from_query = websocket.query_params.get("token", "")
    if token_from_query:
        return _constant_time_compare(token_from_query, expected)

    # Verificar Authorization header
    auth_header = websocket.headers.get("authorization", "")
    bearer = _extract_bearer_token(auth_header)
    if bearer:
        return _constant_time_compare(bearer, expected)

    return False


def _constant_time_compare(a: str, b: str) -> bool:
    """Comparação em tempo constante para evitar timing attacks."""
    import hmac
    return hmac.compare_digest(
        a.encode("utf-8", errors="replace"),
        b.encode("utf-8", errors="replace"),
    )


# ---------------------------------------------------------------------------
# Handler de mensagens
# ---------------------------------------------------------------------------

def _build_services(app_state: Any) -> Any:
    """Constrói RuntimeDispatchServices a partir do estado do app FastAPI."""
    from rlm.server.runtime_pipeline import RuntimeDispatchServices
    return RuntimeDispatchServices(
        session_manager=app_state.session_manager,
        supervisor=app_state.supervisor,
        plugin_loader=app_state.plugin_loader,
        event_router=app_state.event_router,
        hooks=app_state.hooks,
        skill_loader=app_state.skill_loader,
        runtime_guard=getattr(app_state, "runtime_guard", None),
        eligible_skills=getattr(app_state, "skills_eligible", []),
        skill_context=getattr(app_state, "skill_context", ""),
        exec_approval=getattr(app_state, "exec_approval", None),
        exec_approval_required=getattr(app_state, "exec_approval_required", False),
    )


def _envelope_to_payload(envelope: Envelope) -> dict[str, Any]:
    """
    Converte Envelope inbound para payload compatível com dispatch_runtime_prompt_sync.

    Extrai campos que o pipeline espera: text, from_user, channel, metadata, etc.
    """
    meta = dict(envelope.metadata or {})
    return {
        "text": envelope.text,
        "from_user": meta.get("from_user", envelope.source_id),
        "channel": envelope.source_channel,
        "content_type": envelope.message_type,
        "envelope_id": envelope.id,
        "correlation_id": envelope.correlation_id,
        "timestamp": envelope.timestamp,
        "channel_meta": meta,
    }


async def _dispatch_envelope(
    envelope: Envelope,
    app_state: Any,
) -> Envelope:
    """
    Despacha Envelope inbound através do pipeline brain e retorna Envelope outbound.

    Executa dispatch_runtime_prompt_sync em thread pool (é síncrono)
    para não bloquear o event loop do WebSocket.
    """
    from rlm.server.runtime_pipeline import dispatch_runtime_prompt_sync, RuntimeDispatchRejected

    services = _build_services(app_state)
    client_id = envelope.source_client_id
    payload = _envelope_to_payload(envelope)

    loop = asyncio.get_event_loop()
    start_ms = time.monotonic() * 1000

    try:
        result: dict[str, Any] = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                dispatch_runtime_prompt_sync,
                services,
                client_id,
                payload,
            ),
            timeout=_DISPATCH_TIMEOUT_S,
        )
    except RuntimeDispatchRejected as exc:
        logger.warning(
            "Envelope rejeitado pelo pipeline brain: %s (envelope_id=%s)",
            exc.detail,
            envelope.id,
        )
        result = {"reply": exc.detail, "error": True}
    except asyncio.TimeoutError:
        logger.error(
            "Timeout despachando Envelope %s (%.0fs)", envelope.id, _DISPATCH_TIMEOUT_S
        )
        result = {"reply": "Timeout: brain não respondeu a tempo.", "error": True}
    except Exception as exc:
        logger.exception("Erro ao despachar Envelope %s", envelope.id)
        result = {"reply": f"Erro interno: {exc}", "error": True}

    elapsed_ms = time.monotonic() * 1000 - start_ms
    reply_text = str(result.get("reply") or result.get("response") or "")

    reply_meta: dict[str, Any] = {
        "elapsed_ms": elapsed_ms,
        "brain_session_id": result.get("session_id", ""),
    }
    if result.get("error"):
        reply_meta["error"] = True

    return create_reply_envelope(
        inbound=envelope,
        reply_text=reply_text,
        metadata=reply_meta,
    )


# ---------------------------------------------------------------------------
# Ping manager
# ---------------------------------------------------------------------------

async def _ping_loop(websocket: WebSocket) -> None:
    """Envia pings periódicos ao Gateway TS para manter conexão viva."""
    from datetime import datetime as _dt, timezone as _tz
    while websocket.client_state == WebSocketState.CONNECTED:
        await asyncio.sleep(_PING_INTERVAL_S)
        if websocket.client_state != WebSocketState.CONNECTED:
            break
        try:
            await websocket.send_text(
                json.dumps({"type": "ping", "timestamp": _dt.now(_tz.utc).isoformat()})
            )
        except Exception:
            break


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ws/gateway")
async def ws_gateway(websocket: WebSocket) -> None:
    """
    WebSocket endpoint para o Gateway TypeScript.

    Mantém conexão persistente; roteia cada Envelope inbound pelo
    pipeline brain e devolve Envelope outbound de resposta.

    O Gateway TS (WsBridge) espera exatamente este endpoint.
    """
    # ── Auth ──────────────────────────────────────────────────────────────
    if not _authenticate_ws(websocket):
        await websocket.close(code=4401, reason="Unauthorized: token inválido ou ausente")
        logger.warning(
            "WS Gateway: conexão rejeitada — token inválido (client=%s)",
            websocket.client,
        )
        return

    await websocket.accept()
    logger.info("WS Gateway: conexão estabelecida (client=%s)", websocket.client)

    app_state = websocket.app.state
    ping_task = asyncio.create_task(_ping_loop(websocket))

    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as exc:
                logger.warning("WS Gateway: erro ao receber mensagem: %s", exc)
                break

            # ── Parse ────────────────────────────────────────────────────
            try:
                msg: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    "WS Gateway: payload não-JSON (%.150r...)", raw
                )
                await websocket.send_text(
                    json.dumps({"type": "error", "code": "invalid_json",
                                "message": "Payload não é JSON válido"})
                )
                continue

            msg_type: str = msg.get("type", "")

            # ── Envelope inbound ─────────────────────────────────────────
            if msg_type == "envelope":
                data = msg.get("data")
                if not isinstance(data, dict):
                    await websocket.send_text(
                        json.dumps({"type": "error", "code": "invalid_envelope",
                                    "message": "Campo 'data' ausente ou inválido"})
                    )
                    continue

                try:
                    envelope = Envelope.from_dict(data)
                except (ValueError, TypeError) as exc:
                    logger.warning(
                        "WS Gateway: Envelope inválido: %s", exc
                    )
                    await websocket.send_text(
                        json.dumps({"type": "error", "code": "invalid_envelope",
                                    "message": str(exc)})
                    )
                    continue

                # Despacha e retorna resposta
                reply_envelope = await _dispatch_envelope(envelope, app_state)
                await websocket.send_text(
                    json.dumps({"type": "envelope", "data": reply_envelope.to_dict()})
                )

            # ── Pong (resposta ao ping Brain→Gateway) ────────────────────
            elif msg_type == "pong":
                pass  # keepalive confirmado, nada a fazer

            # ── Ack (Gateway confirma entrega) ───────────────────────────
            elif msg_type == "ack":
                envelope_id = msg.get("id", "")
                delivered: bool = bool(msg.get("delivered", False))
                error_msg: str = msg.get("error") or ""
                if delivered:
                    logger.debug("WS Gateway: ack OK para envelope %s", envelope_id)
                else:
                    logger.warning(
                        "WS Gateway: ack FALHA para envelope %s: %s",
                        envelope_id, error_msg,
                    )

            # ── Health report (Gateway reporta status dos canais) ─────────
            elif msg_type == "health_report":
                channels = msg.get("data", {}).get("channels", {})
                logger.debug(
                    "WS Gateway: health_report recebido (%d canais)", len(channels)
                )
                # Futuramente: atualizar ChannelStatusRegistry com os dados
                _try_update_channel_health(app_state, channels)

            # ── Mensagem desconhecida ─────────────────────────────────────
            else:
                logger.debug(
                    "WS Gateway: tipo de mensagem desconhecido ignorado: %r", msg_type
                )

    except Exception as exc:
        logger.exception("WS Gateway: erro inesperado na conexão: %s", exc)
    finally:
        ping_task.cancel()
        logger.info("WS Gateway: conexão encerrada (client=%s)", websocket.client)


# ---------------------------------------------------------------------------
# Helper: atualizar saúde dos canais a partir do health_report
# ---------------------------------------------------------------------------

def _try_update_channel_health(app_state: Any, channels: dict[str, Any]) -> None:
    """
    Atualiza o ChannelStatusRegistry (se disponível) com dados do health_report.

    Falha silenciosamente — health_report é informativo, não crítico.
    """
    try:
        csr = getattr(app_state, "channel_status_registry", None)
        if csr is None:
            return
        for channel_id, info in channels.items():
            if not isinstance(info, dict):
                continue
            status: str = str(info.get("status", "unknown"))
            if status == "healthy":
                csr.mark_connected(channel_id)
            elif status in ("unhealthy", "error"):
                error = str(info.get("error", "gateway health check failed"))
                csr.mark_error(channel_id, error=error)
    except Exception:
        pass  # Health update é não-crítico
