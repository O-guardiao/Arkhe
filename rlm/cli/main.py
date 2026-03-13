"""Arkhe CLI — ponto de entrada principal.

Uso:
    arkhe setup        Wizard interativo de instalação
    arkhe start        Inicia o servidor Arkhe
    arkhe stop         Para o daemon/servidor Arkhe
    arkhe status       Mostra status dos processos e configuração
    arkhe token rotate Regenera todos os tokens de segurança
    arkhe peer add     Adiciona peer WireGuard
    arkhe version      Exibe versão do Arkhe

Compatibilidade:
    rlm ...            Alias legado ainda suportado
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request


def _doctor_preferred_token(env: dict[str, str], *names: str) -> str:
    for name in names:
        token = env.get(name, "").strip()
        if token:
            return token
    return ""


def _doctor_auth_headers(env: dict[str, str], *names: str) -> dict[str, str]:
    token = _doctor_preferred_token(env, *names)
    return {"X-RLM-Token": token} if token else {}


def _doctor_runtime_requirement() -> tuple[bool, str]:
    required = (3, 11)
    current = sys.version_info[:3]
    if current < required:
        return False, f"Python {current[0]}.{current[1]}.{current[2]} em uso; requer >= 3.11"
    return True, f"Python {current[0]}.{current[1]}.{current[2]}"


def _require_supported_runtime(command_name: str) -> bool:
    ok, detail = _doctor_runtime_requirement()
    if ok:
        return True
    _print_error(f"{command_name} bloqueado: {detail}")
    return False


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

# --------------------------------------------------------------------------- #
# Utilitário de console (rich, já dependência do projeto)                      #
# --------------------------------------------------------------------------- #

def _print_error(msg: str) -> None:
    try:
        from rich.console import Console
        Console(stderr=True).print(f"[bold red]✗ Erro:[/] {msg}")
    except ImportError:
        print(f"✗ Erro: {msg}", file=sys.stderr)


def _print_success(msg: str) -> None:
    try:
        from rich.console import Console
        Console().print(f"[bold green]✓[/] {msg}")
    except ImportError:
        print(f"✓ {msg}")


# --------------------------------------------------------------------------- #
# Subcomandos                                                                  #
# --------------------------------------------------------------------------- #

def cmd_setup(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Executa o wizard interativo de configuração."""
    if not _require_supported_runtime("arkhe setup"):
        return 1
    from rlm.cli.wizard import run_wizard
    return run_wizard()


def cmd_start(args: argparse.Namespace) -> int:
    """Inicia o servidor Arkhe (API + WebSocket)."""
    if not _require_supported_runtime("arkhe start"):
        return 1
    from rlm.cli.service import start_services
    return start_services(
        foreground=args.foreground,
        api_only=args.api_only,
        ws_only=args.ws_only,
    )


def cmd_stop(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Para o daemon Arkhe."""
    from rlm.cli.service import stop_services
    return stop_services()


def cmd_status(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Exibe status atual dos processos e configuração."""
    from rlm.cli.service import show_status
    return show_status()


def cmd_update(args: argparse.Namespace) -> int:
    """Atualiza o checkout git do Arkhe e sincroniza dependências."""
    from rlm.cli.service import update_installation
    return update_installation(check_only=args.check, restart=not args.no_restart)


def cmd_token_rotate(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Regenera todos os tokens de segurança no .env."""
    import secrets
    from pathlib import Path

    env_path = Path(".env")
    if not env_path.exists():
        env_path = Path.home() / ".rlm" / ".env"
    if not env_path.exists():
        _print_error(f"Arquivo .env não encontrado em {env_path}")
        return 1

    text = env_path.read_text(encoding="utf-8")
    lines: list[str] = []
    rotated: list[str] = []
    managed_tokens = (
        "RLM_WS_TOKEN",
        "RLM_INTERNAL_TOKEN",
        "RLM_ADMIN_TOKEN",
        "RLM_HOOK_TOKEN",
        "RLM_API_TOKEN",
    )

    for line in text.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in managed_tokens:
            new_token = secrets.token_hex(32)
            lines.append(f"{key}={new_token}")
            rotated.append(key)
        else:
            lines.append(line)

    existing_rotated = set(rotated)
    for name in managed_tokens:
        if name not in existing_rotated:
            lines.append(f"{name}={secrets.token_hex(32)}")
            rotated.append(name)

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for name in rotated:
        _print_success(f"{name} → rotacionado em {env_path}")

    try:
        from rich.console import Console
        Console().print(
            "\n[yellow]⚠[/]  Reinicie o servidor para aplicar os novos tokens."
        )
    except ImportError:
        print("\n⚠  Reinicie o servidor para aplicar os novos tokens.")

    return 0


def cmd_peer_add(args: argparse.Namespace) -> int:
    """Adiciona peer WireGuard ao wg0.conf."""
    from rlm.cli.service import add_wireguard_peer
    return add_wireguard_peer(name=args.name, pubkey=args.pubkey, ip=args.ip)


def cmd_doctor(args: argparse.Namespace) -> int:  # noqa: ARG001
    """
    Valida toda a configuração do Arkhe e testa as conexões com serviços.

    Verifica:
    - Arquivo .env existente e variáveis obrigatórias
    - Conexão com a API do LLM (OpenAI/Anthropic/etc.)
    - Status dos processos Arkhe
    - Configurações de canais (Telegram, Discord, WhatsApp, Slack)
    """
    from pathlib import Path
    import os
    from typing import Any
    import shutil

    console: Any | None = None
    try:
        from rich.console import Console
        console = Console()
        _rich = True
    except ImportError:
        _rich = False

    def row(label: str, status: str, detail: str = "") -> None:
        if _rich and console is not None:
            color = "green" if status == "✓" else "red" if status == "✗" else "yellow"
            console.print(f"  [{color}]{status}[/] [bold]{label}[/]", end="")
            if detail:
                console.print(f"  [dim]{detail}[/]")
            else:
                console.print()
        else:
            print(f"  {status} {label}" + (f"  ({detail})" if detail else ""))

    if _rich and console is not None:
        console.print("\n[bold cyan]Arkhe Doctor — Diagnóstico do sistema[/]\n")
    else:
        print("\n=== Arkhe Doctor ===\n")

    errors = 0

    # ── 0. Runtime / distribuição ───────────────────────────────────────────
    runtime_ok, runtime_detail = _doctor_runtime_requirement()
    row("Python runtime", "✓" if runtime_ok else "✗", runtime_detail)
    if not runtime_ok:
        errors += 1

    if shutil.which("uv"):
        row("uv", "✓", "disponível para setup/update")
    else:
        row("uv", "⚠", "não encontrado — setup/update automático fica limitado")

    # ── 1. Arquivo .env ──────────────────────────────────────────────────────
    env_path = Path(".env")
    if not env_path.exists():
        env_path = Path.home() / ".rlm" / ".env"

    if env_path.exists():
        row(".env encontrado", "✓", str(env_path))
        # Carrega variáveis
        try:
            from dotenv import load_dotenv
            load_dotenv(str(env_path), override=False)
        except ImportError:
            # Parse manual simples
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    else:
        row(".env", "✗", "não encontrado — execute 'arkhe setup'")
        errors += 1

    # ── 2. LLM API Key ───────────────────────────────────────────────────────
    llm_provider = None
    llm_key = None
    for provider_name, env_var in (
        ("OpenAI", "OPENAI_API_KEY"),
        ("Anthropic", "ANTHROPIC_API_KEY"),
        ("Gemini", "GEMINI_API_KEY"),
    ):
        if os.environ.get(env_var):
            llm_provider = provider_name
            llm_key = os.environ.get(env_var)
            break
    if not llm_key:
        row("LLM API Key", "✗", "OPENAI_API_KEY / ANTHROPIC_API_KEY não configurada")
        errors += 1
    else:
        # Testa conexão real com a OpenAI (lista de modelos, barato)
        if os.environ.get("OPENAI_API_KEY"):
            import urllib.request as ur
            import urllib.error as ue
            try:
                req = ur.Request(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                )
                with ur.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        row("OpenAI API Key", "✓", "conexão OK")
                    else:
                        row("OpenAI API Key", "⚠", f"status {resp.status}")
            except ue.HTTPError as e:
                if e.code == 401:
                    row("OpenAI API Key", "✗", "chave inválida (401)")
                    errors += 1
                else:
                    row("OpenAI API Key", "⚠", f"HTTP {e.code}")
            except Exception as exc:
                row("OpenAI API Key", "⚠", f"sem conexão: {exc}")
        else:
            row(f"{llm_provider} API Key", "✓", "configurada (sem teste de conexão ativo no doctor)")

    # ── 3. Tokens do runtime ────────────────────────────────────────────────
    ws_token = os.environ.get("RLM_WS_TOKEN", "")
    internal_token = os.environ.get("RLM_INTERNAL_TOKEN", "")
    admin_token = os.environ.get("RLM_ADMIN_TOKEN", "")
    hook_token = os.environ.get("RLM_HOOK_TOKEN", "")
    api_token = os.environ.get("RLM_API_TOKEN", "")

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

    # ── 4. Servidor em execução ───────────────────────────────────────────────
    rlm_host = os.environ.get("RLM_INTERNAL_HOST", "http://127.0.0.1:5000")
    server_online = False
    try:
        status, _body = _doctor_http_request(
            f"{rlm_host}/health",
            headers=_doctor_auth_headers(os.environ, "RLM_ADMIN_TOKEN", "RLM_API_TOKEN", "RLM_WS_TOKEN"),
            timeout=3,
        )
        if status == 200:
            row("Servidor Arkhe", "✓", f"online em {rlm_host}")
            server_online = True
        else:
            row("Servidor Arkhe", "⚠", f"status {status} em {rlm_host}")
    except urllib_error.HTTPError as exc:
        row("Servidor Arkhe", "✗", f"health rejeitado (HTTP {exc.code}) — verifique RLM_ADMIN_TOKEN")
        errors += 1
    except Exception:
        row("Servidor Arkhe", "⚠", f"offline (use 'arkhe start')")

    # ── 5. Canais configurados ─────────────────────────────────────────────
    _CHANNELS = ["Telegram", "Discord", "WhatsApp", "Slack"]
    if _rich and console is not None:
        console.print()
        console.print("[bold]Canais:[/]")
    else:
        print("\nCanais:")

    env_snapshot = dict(os.environ)
    for ch_name in _CHANNELS:
        status, detail = _doctor_channel_status(env_snapshot, ch_name)
        row(ch_name, status, detail)
        handshake_status, handshake_detail = _doctor_channel_handshake(
            env_snapshot,
            ch_name,
            server_online=server_online,
            rlm_host=rlm_host,
        )
        if handshake_status != "·":
            row(f"{ch_name} handshake", handshake_status, handshake_detail)
            if status in {"✓", "⚠"} and handshake_status == "✗":
                errors += 1

    if any(os.environ.get(name) for name in (
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

    if _rich and console is not None:
        console.print()
        if errors == 0:
            console.print("[bold green]✓ Sistema saudável[/]")
        else:
            console.print(f"[bold red]✗ {errors} problema(s) encontrado(s)[/]")
    else:
        print()
        print("✓ OK" if errors == 0 else f"✗ {errors} problema(s)")

    return 0 if errors == 0 else 1


def cmd_skill_list(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Lista todas as skills instaladas e seu status."""
    from pathlib import Path
    import os

    skills_dir = Path(os.environ.get("RLM_SKILLS_DIR", "")) or (
        Path(__file__).parent.parent / "skills"
    )

    if not skills_dir.exists():
        _print_error(f"Diretório de skills não encontrado: {skills_dir}")
        return 1

    skill_dirs = sorted([d for d in skills_dir.iterdir() if d.is_dir()])
    if not skill_dirs:
        print("Nenhuma skill instalada.")
        return 0

    console = None
    table = None
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
        table.add_column("Skill", style="bold")
        table.add_column("Versão")
        table.add_column("Descrição")
        table.add_column("Status")
        _rich = True
    except ImportError:
        _rich = False
        print(f"{'Skill':<20} {'Versão':<8} Descrição")
        print("-" * 70)

    for skill_path in skill_dirs:
        skill_md = skill_path / "SKILL.md"
        name = skill_path.name
        version = "-"
        description = ""
        has_skill_md = skill_md.exists()

        if has_skill_md:
            # Extrai frontmatter TOML entre +++...+++
            content = skill_md.read_text(encoding="utf-8", errors="replace")
            import re
            m = re.search(r"^\+\+\+(.*?)\+\+\+", content, re.DOTALL | re.MULTILINE)
            if m:
                frontmatter = m.group(1)
                v_match = re.search(r'version\s*=\s*["\']?([^"\'\\n]+)["\']?', frontmatter)
                d_match = re.search(r'description\s*=\s*["\']([^"\']+)["\']', frontmatter)
                if v_match:
                    version = v_match.group(1).strip()
                if d_match:
                    description = d_match.group(1).strip()[:60]

        status = "✓" if has_skill_md else "⚠ sem SKILL.md"

        if _rich and table is not None:
            table.add_row(name, version, description or "-", status)
        else:
            print(f"{name:<20} {version:<8} {description or '-'}")

    if _rich and console is not None and table is not None:
        console.print(table)
        console.print(f"\n[dim]{len(skill_dirs)} skill(s) em {skills_dir}[/]")
    else:
        print(f"\n{len(skill_dirs)} skill(s)")

    return 0


def cmd_skill_install(args: argparse.Namespace) -> int:
    """
    Instala uma skill remotamente.

    Formatos aceitos:
        arkhe skill install github:usuario/repositorio
        arkhe skill install github:usuario/repositorio@branch
        arkhe skill install https://raw.githubusercontent.com/.../SKILL.md
    """
    import os
    import re
    from pathlib import Path
    import urllib.request as ur
    import urllib.error as ue

    source = args.source.strip()
    skills_dir = Path(os.environ.get("RLM_SKILLS_DIR", "")) or (
        Path(__file__).parent.parent / "skills"
    )

    raw_url: str = ""
    skill_name: str = ""

    # github:usuario/repositorio[@branch]
    m = re.match(r"^github:([^/]+)/([^@]+)(?:@(.+))?$", source)
    if m:
        gh_user, gh_repo, branch = m.group(1), m.group(2), m.group(3) or "main"
        raw_url = (
            f"https://raw.githubusercontent.com/{gh_user}/{gh_repo}/{branch}/SKILL.md"
        )
        skill_name = gh_repo.lower().replace("-", "_").replace(".", "_")

    # URL direta
    elif source.startswith("https://") or source.startswith("http://"):
        raw_url = source
        # Infere o nome do path
        skill_name = source.rstrip("/").split("/")[-2] if "/SKILL.md" in source else (
            source.rstrip("/").split("/")[-1]
        )
        skill_name = re.sub(r"[^a-z0-9_]", "_", skill_name.lower())

    else:
        _print_error(
            f"Formato inválido: '{source}'\n"
            "  Use: github:usuario/repo  ou  github:usuario/repo@branch"
        )
        return 1

    # Baixa o SKILL.md
    print(f"Baixando skill de {raw_url} ...")
    try:
        req = ur.Request(raw_url, headers={"User-Agent": "Arkhe-CLI/1.0"})
        with ur.urlopen(req, timeout=15) as resp:
            skill_content = resp.read().decode("utf-8")
    except ue.HTTPError as e:
        if e.code == 404:
            _print_error(f"SKILL.md não encontrado em {raw_url} (404)")
        else:
            _print_error(f"Erro HTTP {e.code} ao baixar {raw_url}")
        return 1
    except Exception as exc:
        _print_error(f"Falha ao baixar skill: {exc}")
        return 1

    # Valida que é um SKILL.md real (tem frontmatter +++ ou ---)
    if "+++" not in skill_content and "---" not in skill_content:
        _print_error(
            "O arquivo baixado não parece ser um SKILL.md válido "
            "(sem frontmatter +++ ou ---)."
        )
        return 1

    # Extrai nome do frontmatter se disponível
    m_name = re.search(r'name\s*=\s*["\']?([A-Za-z0-9_\-]+)["\']?', skill_content)
    if m_name:
        skill_name = re.sub(r"[^a-z0-9_]", "_", m_name.group(1).lower())

    # Cria diretório e escreve o arquivo
    dest_dir = skills_dir / skill_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / "SKILL.md"

    if dest_file.exists() and not getattr(args, "force", False):
        _print_error(
            f"Skill '{skill_name}' já existe em {dest_file}. "
            "Use --force para sobrescrever."
        )
        return 1

    dest_file.write_text(skill_content, encoding="utf-8")
    _print_success(f"Skill '{skill_name}' instalada em {dest_file}")
    print("  Reinicie o servidor para ativar: arkhe stop && arkhe start")
    return 0


def cmd_channel_list(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Mostra todos os canais disponíveis e seu estado de configuração."""
    import os
    from pathlib import Path

    # Carrega .env se existir
    env_path = Path(".env")
    if not env_path.exists():
        env_path = Path.home() / ".rlm" / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(str(env_path), override=False)
        except ImportError:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    CHANNELS = [
        {
            "name": "Telegram",
            "prefix": "telegram",
            "direction": "in + out",
            "vars": {"required": ["TELEGRAM_BOT_TOKEN"], "optional": []},
            "docs": "Polling de mensagens. Sem webhook necessário.",
        },
        {
            "name": "Discord",
            "prefix": "discord",
            "direction": "in + out",
            "vars": {
                "required": ["DISCORD_APP_PUBLIC_KEY", "DISCORD_APP_ID"],
                "optional": ["DISCORD_WEBHOOK_URL", "DISCORD_BOT_TOKEN"],
            },
            "docs": "Interactions Endpoint (slash commands). Out via webhook URL.",
        },
        {
            "name": "WhatsApp",
            "prefix": "whatsapp",
            "direction": "in + out",
            "vars": {
                "required": ["WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "WHATSAPP_VERIFY_TOKEN"],
                "optional": [],
            },
            "docs": "Meta Cloud API. Requer conta WhatsApp Business.",
        },
        {
            "name": "Slack",
            "prefix": "slack",
            "direction": "in + out",
            "vars": {
                "required": ["SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET"],
                "optional": ["SLACK_WEBHOOK_URL"],
            },
            "docs": "Events API. Requer app instalado no workspace.",
        },
        {
            "name": "WebChat",
            "prefix": "webchat",
            "direction": "in + out",
            "vars": {"required": [], "optional": []},
            "docs": "Sempre ativo. Acesse http://localhost:5000/webchat",
        },
    ]

    console = None
    table = None
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
        table.add_column("Canal", style="bold")
        table.add_column("Prefixo")
        table.add_column("Status")
        table.add_column("Nota")
        _rich = True
    except ImportError:
        _rich = False
        print(f"{'Canal':<12} {'Prefixo':<12} {'Status':<15} Nota")
        print("-" * 70)

    for ch in CHANNELS:
        required = ch["vars"]["required"]
        if not required:
            status = "✓ sempre ativo"
            status_color = "green"
        else:
            missing = [v for v in required if not os.environ.get(v)]
            if not missing:
                status = "✓ configurado"
                status_color = "green"
            else:
                status = f"· faltam: {', '.join(missing)}"
                status_color = "dim"

        if _rich and table is not None:
            table.add_row(
                ch["name"],
                ch["prefix"],
                f"[{status_color}]{status}[/]",
                ch["docs"],
            )
        else:
            print(f"{ch['name']:<12} {ch['prefix']:<12} {status:<30} {ch['docs']}")

    if _rich and console is not None and table is not None:
        console.print(table)
        console.print()
        console.print("[dim]Para adicionar um canal: edite .env com as variáveis listadas e reinicie.[/]")
    else:
        print("\nEdite .env com as variáveis necessárias e reinicie o servidor.")

    return 0


def cmd_version(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Exibe versão do Arkhe."""
    try:
        from importlib.metadata import version
        try:
            ver = version("arkhe")
        except Exception:
            ver = version("rlm")
    except Exception:
        ver = "0.1.0-dev"
    print(f"arkhe {ver}")
    return 0


# --------------------------------------------------------------------------- #
# Construção do parser                                                         #
# --------------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    prog_name = "arkhe"
    parser = argparse.ArgumentParser(
    prog=prog_name,
    description="Arkhe — CLI de gerenciamento",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
    arkhe setup              # Wizard de primeira instalação
    arkhe start              # Inicia API + WebSocket em background
    arkhe start --foreground # Inicia no terminal (logs ao vivo)
    arkhe stop               # Para o daemon
    arkhe status             # Mostra processos ativos e config
    arkhe token rotate       # Rotaciona tokens de segurança
    arkhe peer add --name laptop --pubkey <KEY> --ip 10.0.0.2

Compatibilidade:
    rlm ...                  # Alias legado ainda suportado
""",
    )

    sub = parser.add_subparsers(dest="command", metavar="<comando>")

    # --- setup ---
    sub.add_parser("setup", help="Wizard interativo de instalação")

    # --- start ---
    p_start = sub.add_parser("start", help="Inicia o servidor Arkhe")
    p_start.add_argument(
        "--foreground", "-f",
        action="store_true",
        help="Não lança em background (bloqueia o terminal)",
    )
    p_start.add_argument(
        "--api-only",
        action="store_true",
        help="Inicia somente o servidor FastAPI (sem WebSocket)",
    )
    p_start.add_argument(
        "--ws-only",
        action="store_true",
        help="Inicia somente o servidor WebSocket",
    )

    # --- stop ---
    sub.add_parser("stop", help="Para o daemon Arkhe")

    # --- status ---
    sub.add_parser("status", help="Mostra status dos processos e configuração")

    # --- token ---
    p_token = sub.add_parser("token", help="Gerencia tokens de segurança")
    token_sub = p_token.add_subparsers(dest="token_command", metavar="<ação>")
    token_sub.add_parser("rotate", help="Regenera todos os tokens de segurança do runtime")

    # --- peer ---
    p_peer = sub.add_parser("peer", help="Gerencia peers WireGuard")
    peer_sub = p_peer.add_subparsers(dest="peer_command", metavar="<ação>")
    p_peer_add = peer_sub.add_parser("add", help="Adiciona peer WireGuard")
    p_peer_add.add_argument("--name", required=True, help="Nome do peer (ex: laptop)")
    p_peer_add.add_argument("--pubkey", required=True, help="Chave pública WireGuard do peer")
    p_peer_add.add_argument(
        "--ip",
        required=True,
        help="IP do peer na VPN (ex: 10.0.0.2)",
    )

    # --- version ---
    sub.add_parser("version", help="Exibe versão do Arkhe")

    # --- update ---
    p_update = sub.add_parser("update", help="Atualiza checkout git e dependências")
    p_update.add_argument(
        "--check",
        action="store_true",
        help="Apenas verifica se há commits remotos pendentes",
    )
    p_update.add_argument(
        "--no-restart",
        action="store_true",
        help="Não reinicia os serviços após atualizar",
    )

    # --- doctor ---
    sub.add_parser("doctor", help="Valida configuração e testa conexões")

    # --- skill ---
    p_skill = sub.add_parser("skill", help="Gerencia skills do Arkhe")
    skill_sub = p_skill.add_subparsers(dest="skill_command", metavar="<ação>")
    skill_sub.add_parser("list", help="Lista skills instaladas")
    p_skill_install = skill_sub.add_parser("install", help="Instala skill remota")
    p_skill_install.add_argument(
        "source",
        help="Fonte da skill: github:usuario/repo  ou  github:usuario/repo@branch",
    )
    p_skill_install.add_argument(
        "--force", action="store_true", help="Sobrescreve se já existir"
    )

    # --- channel ---
    p_channel = sub.add_parser("channel", help="Gerencia canais de mensagem")
    channel_sub = p_channel.add_subparsers(dest="channel_command", metavar="<ação>")
    channel_sub.add_parser("list", help="Lista canais disponíveis e seu estado")

    return parser


# --------------------------------------------------------------------------- #
# main()                                                                       #
# --------------------------------------------------------------------------- #

DISPATCH = {
    "setup": cmd_setup,
    "start": cmd_start,
    "stop": cmd_stop,
    "status": cmd_status,
    "version": cmd_version,
    "update": cmd_update,
    "doctor": cmd_doctor,
}


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "token":
        if getattr(args, "token_command", None) == "rotate":
            sys.exit(cmd_token_rotate(args))
        else:
            parser.parse_args(["token", "--help"])
        return

    if args.command == "peer":
        if getattr(args, "peer_command", None) == "add":
            sys.exit(cmd_peer_add(args))
        else:
            parser.parse_args(["peer", "--help"])
        return

    if args.command == "skill":
        cmd = getattr(args, "skill_command", None)
        if cmd == "list":
            sys.exit(cmd_skill_list(args))
        elif cmd == "install":
            sys.exit(cmd_skill_install(args))
        else:
            parser.parse_args(["skill", "--help"])
        return

    if args.command == "channel":
        cmd = getattr(args, "channel_command", None)
        if cmd == "list":
            sys.exit(cmd_channel_list(args))
        else:
            parser.parse_args(["channel", "--help"])
        return

    fn = DISPATCH.get(args.command)
    if fn is None:
        _print_error(f"Comando desconhecido: '{args.command}'")
        sys.exit(1)

    sys.exit(fn(args))


if __name__ == "__main__":
    main()
