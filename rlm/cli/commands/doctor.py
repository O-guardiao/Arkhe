from __future__ import annotations

import argparse
import json
import os
import shutil
from collections.abc import Mapping
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from rlm.cli.context import CliContext, doctor_runtime_requirement, resolve_operator_api_base_url
from rlm.cli.json_output import build_cli_json_envelope


def _doctor_preferred_token(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        token = env.get(name, "").strip()
        if token:
            return token
    return ""


def _doctor_auth_headers(env: Mapping[str, str], *names: str) -> dict[str, str]:
    token = _doctor_preferred_token(env, *names)
    return {"X-RLM-Token": token} if token else {}


def _doctor_runtime_requirement() -> tuple[bool, str]:
    return doctor_runtime_requirement()


def _doctor_channel_status(env: dict[str, str], channel: str) -> tuple[str, str]:
    if channel == "Telegram":
        required = ["TELEGRAM_BOT_TOKEN"]
        missing = [name for name in required if not env.get(name)]
        return ("✓", "configurado") if not missing else ("·", f"não configurado ({', '.join(required)})")

    if channel == "Discord":
        skip_verify = env.get("RLM_DISCORD_SKIP_VERIFY", "").lower() == "true"
        present = any(env.get(name) for name in ("DISCORD_APP_ID", "DISCORD_APP_PUBLIC_KEY", "RLM_DISCORD_SKIP_VERIFY"))
        missing: list[str] = []
        if not env.get("DISCORD_APP_ID"):
            missing.append("DISCORD_APP_ID")
        if not env.get("DISCORD_APP_PUBLIC_KEY") and not skip_verify:
            missing.append("DISCORD_APP_PUBLIC_KEY ou RLM_DISCORD_SKIP_VERIFY=true")
        if not present:
            return "·", "não configurado (DISCORD_APP_ID + DISCORD_APP_PUBLIC_KEY)"
        return ("✓", "configurado") if not missing else ("⚠", f"configuração incompleta: falta {', '.join(missing)}")

    if channel == "WhatsApp":
        required = ["WHATSAPP_VERIFY_TOKEN", "WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID"]
        present = any(env.get(name) for name in required + ["WHATSAPP_APP_SECRET"])
        missing = [name for name in required if not env.get(name)]
        if not present:
            return "·", f"não configurado ({', '.join(required)})"
        return ("✓", "configurado") if not missing else ("⚠", f"configuração incompleta: falta {', '.join(missing)}")

    if channel == "Slack":
        required = ["SLACK_SIGNING_SECRET"]
        present = any(env.get(name) for name in ("SLACK_SIGNING_SECRET", "SLACK_APP_ID", "SLACK_BOT_TOKEN", "SLACK_WEBHOOK_URL"))
        missing = [name for name in required if not env.get(name)]
        if not present:
            return "·", "não configurado (SLACK_SIGNING_SECRET)"
        return ("✓", "configurado") if not missing else ("⚠", f"configuração incompleta: falta {', '.join(missing)}")

    return "·", "canal desconhecido"


def _doctor_http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 8.0,
) -> tuple[int, bytes]:
    req = urllib_request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        return getattr(resp, "status", 200), resp.read()


def _doctor_json_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict | None = None,
    timeout: float = 8.0,
) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    merged_headers = {"Content-Type": "application/json", **(headers or {})} if payload is not None else (headers or {})
    status, body = _doctor_http_request(
        url,
        method=method,
        headers=merged_headers,
        data=data,
        timeout=timeout,
    )
    return status, json.loads(body.decode("utf-8") or "{}")


def _doctor_channel_handshake(
    env: dict[str, str],
    channel: str,
    *,
    server_online: bool,
    rlm_host: str,
) -> tuple[str, str]:
    if channel == "Telegram":
        token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            return "·", "não configurado"
        try:
            status, payload = _doctor_json_request(
                f"https://api.telegram.org/bot{token}/getMe",
                timeout=8.0,
            )
            if status == 200 and payload.get("ok"):
                username = payload.get("result", {}).get("username", "?")
                return "✓", f"getMe OK (@{username})"
            return "✗", payload.get("description", f"status {status}")
        except urllib_error.HTTPError as exc:
            return "✗", f"Telegram HTTP {exc.code}"
        except Exception as exc:
            return "✗", f"Telegram handshake falhou: {exc}"

    if channel == "Discord":
        bot_token = env.get("DISCORD_BOT_TOKEN", "").strip()
        if bot_token:
            auth = bot_token if bot_token.lower().startswith("bot ") else f"Bot {bot_token}"
            try:
                status, payload = _doctor_json_request(
                    "https://discord.com/api/v10/users/@me",
                    headers={"Authorization": auth},
                    timeout=8.0,
                )
                if status == 200 and payload.get("id"):
                    return "✓", f"Discord API OK ({payload.get('username', 'bot')})"
                return "✗", f"Discord status {status}"
            except urllib_error.HTTPError as exc:
                return "✗", f"Discord HTTP {exc.code}"
            except Exception as exc:
                return "✗", f"Discord handshake falhou: {exc}"

        if server_online and env.get("RLM_DISCORD_SKIP_VERIFY", "").lower() == "true" and not env.get("DISCORD_APP_PUBLIC_KEY"):
            try:
                status, payload = _doctor_json_request(
                    f"{rlm_host}/discord/interactions",
                    method="POST",
                    payload={"type": 1},
                    timeout=5.0,
                )
                if status == 200 and payload.get("type") == 1:
                    return "✓", "PING local OK (skip verify)"
                return "✗", f"Discord PING local status {status}"
            except Exception as exc:
                return "✗", f"Discord PING local falhou: {exc}"

        if env.get("DISCORD_APP_ID") or env.get("DISCORD_APP_PUBLIC_KEY"):
            return "⚠", "sem DISCORD_BOT_TOKEN; handshake real depende do Discord ou de skip verify local"
        return "·", "não configurado"

    if channel == "WhatsApp":
        verify_token = env.get("WHATSAPP_VERIFY_TOKEN", "").strip()
        token = env.get("WHATSAPP_TOKEN", "").strip()
        phone_id = env.get("WHATSAPP_PHONE_ID", "").strip()
        if server_online and verify_token:
            challenge = "rlm-doctor"
            query = urllib_parse.urlencode(
                {
                    "hub.mode": "subscribe",
                    "hub.verify_token": verify_token,
                    "hub.challenge": challenge,
                }
            )
            try:
                status, body = _doctor_http_request(
                    f"{rlm_host}/whatsapp/webhook?{query}",
                    timeout=5.0,
                )
                if status == 200 and body.decode("utf-8") == challenge:
                    return "✓", "challenge local OK"
                return "✗", f"challenge local status {status}"
            except Exception as exc:
                return "✗", f"challenge local falhou: {exc}"

        if token and phone_id:
            try:
                status, payload = _doctor_json_request(
                    f"https://graph.facebook.com/v22.0/{phone_id}?fields=id",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=8.0,
                )
                if status == 200 and payload.get("id"):
                    return "✓", f"Graph API OK ({payload.get('id')})"
                return "✗", f"Graph status {status}"
            except urllib_error.HTTPError as exc:
                return "✗", f"Graph HTTP {exc.code}"
            except Exception as exc:
                return "✗", f"Graph handshake falhou: {exc}"

        if verify_token or token or phone_id:
            return "⚠", "sem servidor online ou token/phone_id suficientes para handshake real"
        return "·", "não configurado"

    if channel == "Slack":
        bot_token = env.get("SLACK_BOT_TOKEN", "").strip()
        if bot_token:
            try:
                status, payload = _doctor_json_request(
                    "https://slack.com/api/auth.test",
                    method="POST",
                    headers={"Authorization": f"Bearer {bot_token}"},
                    payload={},
                    timeout=8.0,
                )
                if status == 200 and payload.get("ok"):
                    team = payload.get("team") or payload.get("team_id", "workspace")
                    return "✓", f"auth.test OK ({team})"
                return "✗", payload.get("error", f"status {status}")
            except urllib_error.HTTPError as exc:
                return "✗", f"Slack HTTP {exc.code}"
            except Exception as exc:
                return "✗", f"Slack handshake falhou: {exc}"

        if server_online and env.get("SLACK_SIGNING_SECRET"):
            try:
                status, payload = _doctor_json_request(
                    f"{rlm_host}/slack/events",
                    method="POST",
                    payload={"type": "url_verification", "challenge": "rlm-doctor"},
                    timeout=5.0,
                )
                if status == 200 and payload.get("challenge") == "rlm-doctor":
                    return "✓", "url_verification local OK"
                return "✗", f"Slack verification status {status}"
            except Exception as exc:
                return "✗", f"Slack url_verification falhou: {exc}"

        if env.get("SLACK_SIGNING_SECRET") or env.get("SLACK_APP_ID") or bot_token:
            return "⚠", "sem SLACK_BOT_TOKEN válido ou servidor online para url_verification"
        return "·", "não configurado"

    return "·", "canal desconhecido"


def _doctor_launcher_state_status(context: CliContext, *, server_online: bool) -> tuple[str, str]:
    from rlm.cli.state.diagnosis import diagnose_launcher_state_alignment

    return diagnose_launcher_state_alignment(context, server_online=server_online)


def _doctor_launcher_state_json(context: CliContext, *, server_online: bool) -> dict[str, Any]:
    from rlm.cli.state.diagnosis import build_launcher_state_diagnosis

    return build_launcher_state_diagnosis(context, health_online=server_online)


def _doctor_operator_target(context: CliContext) -> str:
    return resolve_operator_api_base_url(context.env, context.api_base_url())


def cmd_doctor(args: argparse.Namespace, *, context: CliContext | None = None) -> int:  # noqa: ARG001
    """
    Valida toda a configuração do Arkhe e testa as conexões com serviços.

    Verifica:
    - Arquivo .env existente e variáveis obrigatórias
    - Conexão com a API do LLM (OpenAI/Anthropic/etc.)
    - Status dos processos Arkhe
    - Configurações de canais (Telegram, Discord, WhatsApp, Slack)
    """
    current_context = context if context is not None else CliContext.from_environment()
    json_only = getattr(args, "launcher_state_json", False)

    if json_only:
        current_context.load_env_file(override=False)
        rlm_host = _doctor_operator_target(current_context)
        server_online = False
        try:
            status, _body = _doctor_http_request(
                f"{rlm_host}/health",
                headers=_doctor_auth_headers(current_context.env, "RLM_ADMIN_TOKEN", "RLM_API_TOKEN", "RLM_WS_TOKEN"),
                timeout=3,
            )
            server_online = status == 200
        except Exception:
            server_online = False

        payload = _doctor_launcher_state_json(current_context, server_online=server_online)
        envelope = build_cli_json_envelope("doctor", payload, severity=str(payload.get("severity", "info")))
        print(json.dumps(envelope, indent=2, ensure_ascii=False))
        return 0

    console: Any | None = None
    try:
        from rich.console import Console

        console = Console()
        rich_enabled = True
    except ImportError:
        rich_enabled = False

    def row(label: str, status: str, detail: str = "") -> None:
        if rich_enabled and console is not None:
            color = "green" if status == "✓" else "red" if status == "✗" else "yellow"
            console.print(f"  [{color}]{status}[/] [bold]{label}[/]", end="")
            if detail:
                console.print(f"  [dim]{detail}[/]")
            else:
                console.print()
        else:
            print(f"  {status} {label}" + (f"  ({detail})" if detail else ""))

    if rich_enabled and console is not None:
        console.print("\n[bold cyan]Arkhe Doctor — Diagnóstico do sistema[/]\n")
    else:
        print("\n=== Arkhe Doctor ===\n")

    errors = 0

    runtime_ok, runtime_detail = _doctor_runtime_requirement()
    row("Python runtime", "✓" if runtime_ok else "✗", runtime_detail)
    if not runtime_ok:
        errors += 1

    if current_context.has_tool("uv"):
        row("uv", "✓", "disponível para setup/update")
    else:
        row("uv", "⚠", "não encontrado — setup/update automático fica limitado")

    env_path = current_context.load_env_file(override=False)
    if env_path is not None:
        row(".env encontrado", "✓", str(env_path))
    else:
        row(".env", "✗", "não encontrado — execute 'arkhe setup'")
        errors += 1

    llm_provider = None
    llm_key = None
    for provider_name, env_var in (
        ("OpenAI", "OPENAI_API_KEY"),
        ("Anthropic", "ANTHROPIC_API_KEY"),
        ("Gemini", "GEMINI_API_KEY"),
    ):
        if current_context.env.get(env_var):
            llm_provider = provider_name
            llm_key = current_context.env.get(env_var)
            break
    if not llm_key:
        row("LLM API Key", "✗", "OPENAI_API_KEY / ANTHROPIC_API_KEY não configurada")
        errors += 1
    else:
        if current_context.env.get("OPENAI_API_KEY"):
            try:
                req = urllib_request.Request(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {current_context.env['OPENAI_API_KEY']}"},
                )
                with urllib_request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        row("OpenAI API Key", "✓", "conexão OK")
                    else:
                        row("OpenAI API Key", "⚠", f"status {resp.status}")
            except urllib_error.HTTPError as exc:
                if exc.code == 401:
                    row("OpenAI API Key", "✗", "chave inválida (401)")
                    errors += 1
                else:
                    row("OpenAI API Key", "⚠", f"HTTP {exc.code}")
            except Exception as exc:
                row("OpenAI API Key", "⚠", f"sem conexão: {exc}")
        else:
            row(f"{llm_provider} API Key", "✓", "configurada (sem teste de conexão ativo no doctor)")

    ws_token = current_context.env.get("RLM_WS_TOKEN", "")
    internal_token = current_context.env.get("RLM_INTERNAL_TOKEN", "")
    admin_token = current_context.env.get("RLM_ADMIN_TOKEN", "")
    hook_token = current_context.env.get("RLM_HOOK_TOKEN", "")
    api_token = current_context.env.get("RLM_API_TOKEN", "")

    if ws_token and len(ws_token) >= 32:
        row("RLM_WS_TOKEN", "✓", f"{len(ws_token)} chars")
    else:
        row("RLM_WS_TOKEN", "✗", "não configurado — execute 'arkhe token rotate'")
        errors += 1

    if internal_token and len(internal_token) >= 32:
        row("RLM_INTERNAL_TOKEN", "✓", f"{len(internal_token)} chars")
    else:
        row("RLM_INTERNAL_TOKEN", "✗", "não configurado — o webhook central vai rejeitar chamadas internas")
        errors += 1

    if admin_token and len(admin_token) >= 32:
        row("RLM_ADMIN_TOKEN", "✓", f"{len(admin_token)} chars")
    else:
        row("RLM_ADMIN_TOKEN", "✗", "não configurado — health e rotas administrativas ficarão indisponíveis")
        errors += 1

    if hook_token and len(hook_token) >= 32:
        row("RLM_HOOK_TOKEN", "✓", f"{len(hook_token)} chars")
    else:
        row("RLM_HOOK_TOKEN", "⚠", "não configurado — receptor externo /api/hooks ficará desabilitado")

    if api_token and len(api_token) >= 32:
        row("RLM_API_TOKEN", "✓", f"{len(api_token)} chars")
    else:
        row("RLM_API_TOKEN", "⚠", "OpenAI-compat /v1 ficará desabilitada")

    rlm_host = _doctor_operator_target(current_context)
    server_online = False
    try:
        status, _body = _doctor_http_request(
            f"{rlm_host}/health",
            headers=_doctor_auth_headers(current_context.env, "RLM_ADMIN_TOKEN", "RLM_API_TOKEN", "RLM_WS_TOKEN"),
            timeout=3,
        )
        if status == 200:
            row("Servidor Arkhe", "✓", f"online em {rlm_host}")
            server_online = True
        else:
            row("Servidor Arkhe", "⚠", f"status {status} em {rlm_host}")
    except urllib_error.HTTPError as exc:
        row(
            "Servidor Arkhe",
            "✗",
            f"health rejeitado (HTTP {exc.code}) em {rlm_host} — verifique se o operador aponta para a API Arkhe correta",
        )
        errors += 1
    except Exception:
        row("Servidor Arkhe", "⚠", "offline (use 'arkhe start')")

    launcher_status, launcher_detail = _doctor_launcher_state_status(current_context, server_online=server_online)
    row("Launcher state", launcher_status, launcher_detail)

    channels = ["Telegram", "Discord", "WhatsApp", "Slack"]
    if rich_enabled and console is not None:
        console.print()
        console.print("[bold]Canais:[/]")
    else:
        print("\nCanais:")

    env_snapshot = dict(current_context.env)
    for channel_name in channels:
        status, detail = _doctor_channel_status(env_snapshot, channel_name)
        row(channel_name, status, detail)
        handshake_status, handshake_detail = _doctor_channel_handshake(
            env_snapshot,
            channel_name,
            server_online=server_online,
            rlm_host=rlm_host,
        )
        if handshake_status != "·":
            row(f"{channel_name} handshake", handshake_status, handshake_detail)
            if status in {"✓", "⚠"} and handshake_status == "✗":
                errors += 1

    if any(current_context.env.get(name) for name in (
        "DISCORD_APP_ID",
        "DISCORD_APP_PUBLIC_KEY",
        "WHATSAPP_TOKEN",
        "WHATSAPP_PHONE_ID",
        "WHATSAPP_VERIFY_TOKEN",
        "SLACK_SIGNING_SECRET",
    )):
        if not internal_token:
            row("Integração canais → webhook interno", "✗", "RLM_INTERNAL_TOKEN ausente pode quebrar despacho interno")
            errors += 1
        else:
            row("Integração canais → webhook interno", "✓", "autenticação interna presente")

    if rich_enabled and console is not None:
        console.print()
        if errors == 0:
            console.print("[bold green]✓ Sistema saudável[/]")
        else:
            console.print(f"[bold red]✗ {errors} problema(s) encontrado(s)[/]")
    else:
        print()
        print("✓ OK" if errors == 0 else f"✗ {errors} problema(s)")

    return 0 if errors == 0 else 1