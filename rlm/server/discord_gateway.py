"""
Discord Gateway — rlm/server/discord_gateway.py

Receptor de Interactions do Discord para o RLM.

Arquitetura RLM-nativa:
    Mensagem Discord (Slash Command / botão)
        → POST /discord/interactions
        → Verificação Ed25519 da assinatura
        → Roteamento para POST /webhook/discord:{guild_id}:{user_id}
        → EventRouter "discord:*" → agente RLM processa
        → Resposta de texto de volta ao Discord

Discord Interactions vs Bot Polling:
    O RLM usa o modelo Interactions Endpoint, onde o Discord POSTa eventos
    HTTP para nosso servidor — sem WebSocket permanente, sem discord.py.
    Vantagem: funciona 100% em arquitetura webhook-first do RLM.

Setup Discord (única vez):
    1. No Discord Developer Portal: Applications → seu app → Interactions Endpoint URL
    2. Preencha: https://seu-dominio.com/discord/interactions
    3. O Discord chamará GET+POST para validar (este endpoint retorna PONG)
    4. Crie um slash command /rlm em Applications → Commands

Variáveis de ambiente:
    DISCORD_APP_PUBLIC_KEY  — Chave pública Ed25519 do app (hex, 64 chars)
    DISCORD_APP_ID          — ID da aplicação Discord
    RLM_DISCORD_SKIP_VERIFY — 'true' para desenvolvimento local sem HTTPS

Verificação Ed25519:
    Requer pacote 'cryptography'. Se não instalado, define
    RLM_DISCORD_SKIP_VERIFY=true no desenvolvimento.
    Produção: pip install cryptography
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from rlm.logging import get_runtime_logger

log = get_runtime_logger("discord_gateway")

router = APIRouter(prefix="/discord", tags=["discord"])

# ---------------------------------------------------------------------------
# Verificação de assinatura Ed25519
# ---------------------------------------------------------------------------

def _verify_discord_signature(
    public_key_hex: str,
    timestamp: str,
    body: bytes,
    signature_hex: str,
) -> bool:
    """
    Verifica a assinatura Ed25519 de um request Discord Interactions.

    Tenta usar o pacote 'cryptography'. Se ausente e
    RLM_DISCORD_SKIP_VERIFY=true, passa sem verificação (apenas dev).

    Raises:
        RuntimeError: quando cryptography não está instalado e skip não ativado.
        ValueError: quando a assinatura é inválida.
    """
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        pub_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        message = timestamp.encode("utf-8") + body
        try:
            pub_key.verify(bytes.fromhex(signature_hex), message)
            return True
        except InvalidSignature:
            raise ValueError("Assinatura Ed25519 inválida — request não é do Discord.")

    except ImportError:
        skip = os.environ.get("RLM_DISCORD_SKIP_VERIFY", "false").lower()
        if skip == "true":
            log.warn(
                "RLM_DISCORD_SKIP_VERIFY=true — verificação Ed25519 desativada",
                recommendation="Instale cryptography para produção",
            )
            return True
        raise RuntimeError(
            "Pacote 'cryptography' é necessário para verificar requests Discord. "
            "Instale com: pip install cryptography\n"
            "Em desenvolvimento use: RLM_DISCORD_SKIP_VERIFY=true"
        )


# ---------------------------------------------------------------------------
# Extração de dados da Interaction
# ---------------------------------------------------------------------------

def _extract_interaction_data(interaction: dict) -> dict:
    """
    Extrai campos relevantes de um objeto Discord Interaction.

    Tipos suportados:
    - PING (1) — apenas validação do endpoint
    - APPLICATION_COMMAND (2) — slash commands como /rlm
    - MESSAGE_COMPONENT (3) — cliques em botões e menus

    Returns:
        Dict com: type, command, args, user_id, username, guild_id, channel_id
    """
    itype = interaction.get("type", 0)

    # Dados do usuário: pode estar em "member.user" (guild) ou "user" (DM)
    member = interaction.get("member", {})
    user = member.get("user") or interaction.get("user") or {}
    user_id = user.get("id", "unknown")
    username = user.get("global_name") or user.get("username", "unknown")

    guild_id = interaction.get("guild_id", "dm")
    channel_id = interaction.get("channel_id", "")

    # Dados do comando
    data = interaction.get("data", {})
    command = data.get("name", "")
    options = data.get("options", [])

    # Monta string com todos os args do slash command
    args_parts = []
    for opt in options:
        val = opt.get("value", "")
        args_parts.append(str(val))
    args = " ".join(args_parts)

    # Para MESSAGE_COMPONENT: custom_id identifica o botão/menu
    custom_id = data.get("custom_id", "")

    return {
        "type": itype,
        "command": command,
        "args": args,
        "custom_id": custom_id,
        "user_id": user_id,
        "username": username,
        "guild_id": guild_id,
        "channel_id": channel_id,
    }


# ---------------------------------------------------------------------------
# Endpoint principal
# ---------------------------------------------------------------------------

@router.post("/interactions")
async def discord_interactions(request: Request) -> JSONResponse:
    """
    Endpoint Discord Interactions — recebe todos os eventos de interação.

    Discord verifica:
    1. Header X-Signature-Ed25519 — assinatura da mensagem
    2. Header X-Signature-Timestamp — timestamp para evitar replay

    O endpoint responde de duas formas:
    - PING (type=1): retorna {"type": 1} imediatamente (validação do endpoint)
    - Command/Component: despacha para o RLM via /webhook/{client_id},
      retorna resposta diferida {"type": 5} e envia followup assíncrono.
    """
    public_key = os.environ.get("DISCORD_APP_PUBLIC_KEY", "")
    if not public_key and os.environ.get("RLM_DISCORD_SKIP_VERIFY", "false").lower() != "true":
        log.error("DISCORD_APP_PUBLIC_KEY não configurada.")
        raise HTTPException(status_code=500, detail="Discord gateway não configurado.")

    # Verificação de assinatura
    body = await request.body()
    timestamp = request.headers.get("X-Signature-Timestamp", "")
    signature = request.headers.get("X-Signature-Ed25519", "")

    if public_key:
        try:
            _verify_discord_signature(public_key, timestamp, body, signature)
        except (ValueError, RuntimeError) as exc:
            log.warn("Discord signature check falhou", error=str(exc))
            raise HTTPException(status_code=401, detail="Invalid request signature")

    # Decodifica payload
    try:
        interaction = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="JSON inválido")

    itype = interaction.get("type", 0)

    # --- PING: responde imediatamente para validar endpoint ---
    if itype == 1:
        return JSONResponse(content={"type": 1})

    # Extrai dados da interaction
    info = _extract_interaction_data(interaction)

    # Monta client_id no padrão RLM: "discord:{guild_id}:{user_id}"
    client_id = f"discord:{info['guild_id']}:{info['user_id']}"

    # Texto que vai ao agente RLM
    if itype == 2:  # APPLICATION_COMMAND
        prompt_text = info["args"] or f"/{info['command']}"
    elif itype == 3:  # MESSAGE_COMPONENT
        prompt_text = info["custom_id"]
    else:
        # Tipos não suportados → ignora silenciosamente
        log.debug("Discord interaction type ignorada", interaction_type=itype)
        return JSONResponse(content={"type": 1})

    # Envia para o agente RLM de forma assíncrona
    # A resposta será entregue via followup usando o interaction token
    interaction_token = interaction.get("token", "")
    app_id = os.environ.get("DISCORD_APP_ID", "")

    # Responde ao Discord imediatamente com "deferred" (mostra "pensando...")
    # BackgroundTask despacha _run_rlm_and_followup após enviar a resposta
    return JSONResponse(
        content={"type": 5},  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
        background=BackgroundTask(
            _run_rlm_and_followup,
            request=request,
            client_id=client_id,
            prompt_text=prompt_text,
            username=info["username"],
            interaction_token=interaction_token,
            app_id=app_id,
        ),
    )


async def _run_rlm_and_followup(
    request: Request,
    client_id: str,
    prompt_text: str,
    username: str,
    interaction_token: str,
    app_id: str,
) -> None:
    """
    Executa o agente RLM para a interação Discord e envia o resultado
    como followup de volta ao Discord.

    Fluxo:
    1. POST /webhook/{client_id} com o prompt → RLM processa → retorna texto
    2. POST https://discord.com/api/v10/webhooks/{app_id}/{token} → followup
    """
    import urllib.request as urequest
    import urllib.error as uerror

    # Monta payload para o webhook do RLM
    rlm_payload = {
        "from_user": username,
        "text": prompt_text,
        "channel": "discord",
    }

    # Chama o proxy interno (/webhook/) via HTTP local
    rlm_host = os.environ.get("RLM_INTERNAL_HOST", "http://127.0.0.1:5000")
    ws_token = os.environ.get("RLM_WS_TOKEN", "")
    rlm_url = f"{rlm_host}/webhook/{client_id}"
    if ws_token:
        rlm_url += f"?token={ws_token}"

    rlm_response_text = "(sem resposta)"
    try:
        data = json.dumps(rlm_payload, ensure_ascii=False).encode("utf-8")
        req = urequest.Request(
            rlm_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urequest.urlopen(req, timeout=110) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            rlm_response_text = result.get("result") or result.get("output") or str(result)
    except Exception as exc:
        log.error("Erro ao chamar RLM para Discord interaction", error=str(exc), client_id=client_id)
        rlm_response_text = f"Erro interno ao processar sua solicitação: {exc}"

    # Envia followup ao Discord
    if not app_id or not interaction_token:
        log.warn("DISCORD_APP_ID ou interaction_token ausentes — followup cancelado")
        return

    followup_url = (
        f"https://discord.com/api/v10/webhooks/{app_id}/{interaction_token}"
    )
    # Discord limita mensagens a 2000 chars
    content = rlm_response_text[:1990]
    followup_payload = {"content": content}
    fdata = json.dumps(followup_payload).encode("utf-8")
    freq = urequest.Request(
        followup_url,
        data=fdata,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urequest.urlopen(freq, timeout=15):
            pass
    except uerror.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log.error("Discord followup falhou HTTP", status_code=e.code, body_preview=body[:200])
    except Exception as exc:
        log.error("Discord followup falhou", error=str(exc))
