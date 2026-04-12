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
from datetime import datetime as _dt, timezone as _tz
import json
import logging
import os
from typing import Any, cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from rlm.server.brain_api import BrainAPI, RuntimePipelineBrainAPI
from rlm.gateway.envelope import Envelope

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


def _build_hello_ack() -> dict[str, Any]:
    """Monta o frame de confirmação do handshake inicial."""
    return {
        "type": "hello_ack",
        "data": {
            "accepted": True,
            "schema_version": "1",
            "server": "brain-python",
            "server_version": os.environ.get("RLM_VERSION", "dev"),
            "capabilities": [
                "envelope.v1",
                "ack.v1",
                "health_report.v1",
                "ping-pong.v1",
            ],
            "timestamp": _dt.now(_tz.utc).isoformat(),
        },
    }


# ---------------------------------------------------------------------------
# Handler de mensagens
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Ping manager
# ---------------------------------------------------------------------------

async def _ping_loop(websocket: WebSocket) -> None:
    """Envia pings periódicos ao Gateway TS para manter conexão viva."""
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
    brain_api: BrainAPI = RuntimePipelineBrainAPI(
        app_state,
        dispatch_timeout_s=_DISPATCH_TIMEOUT_S,
    )

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

            # ── Hello handshake ───────────────────────────────────────────
            if msg_type == "hello":
                data = msg.get("data")
                if not isinstance(data, dict):
                    await websocket.send_text(
                        json.dumps({
                            "type": "error",
                            "code": "invalid_hello",
                            "message": "Campo 'data' do hello ausente ou inválido",
                        })
                    )
                    continue

                data_dict = cast(dict[str, Any], data)

                schema_version = str(data_dict.get("schema_version") or "")
                client_name = str(data_dict.get("client") or "")
                capabilities_obj = data_dict.get("capabilities")
                capabilities = (
                    [str(item) for item in cast(list[Any], capabilities_obj)]
                    if isinstance(capabilities_obj, list)
                    else []
                )

                if not schema_version or not client_name:
                    await websocket.send_text(
                        json.dumps({
                            "type": "error",
                            "code": "invalid_hello",
                            "message": "hello requer schema_version e client",
                        })
                    )
                    continue

                logger.info(
                    "WS Gateway: hello recebido client=%s schema=%s capabilities=%s",
                    client_name,
                    schema_version,
                    capabilities,
                )
                await websocket.send_text(json.dumps(_build_hello_ack()))
                continue

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
                    envelope = Envelope.from_dict(cast(dict[str, Any], data))
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
                reply_envelope = await brain_api.dispatch_prompt(envelope)
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
            elif msg_type in ("health_report", "health.report"):
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
            info_dict = cast(dict[str, Any], info)
            status = str(info_dict.get("status", "unknown"))
            if status == "healthy":
                csr.mark_connected(channel_id)
            elif status in ("unhealthy", "error"):
                error = str(info_dict.get("error", "gateway health check failed"))
                csr.mark_error(channel_id, error=error)
    except Exception:
        pass  # Health update é não-crítico
