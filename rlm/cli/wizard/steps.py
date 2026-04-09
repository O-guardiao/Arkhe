"""Steps do wizard — coleta de configurações por seção."""

from __future__ import annotations

import secrets
from rlm.cli.wizard.channels import (
    CHANNEL_SPECS,
    is_sensitive_channel_var,
)
from rlm.cli.wizard.env_utils import (
    _LLM_SECTION_KEYS,
    _MODEL_ROLE_SPECS,
    _build_role_model_defaults,
    _format_role_model_summary,
    _get_model_options,
    _prompt_model_name,
    _test_openai_key,
)
from rlm.cli.wizard.prompter import WizardPrompter


# ═══════════════════════════════════════════════════════════════════════════ #
# _step_llm_credentials                                                      #
# ═══════════════════════════════════════════════════════════════════════════ #


def _step_llm_credentials(
    p: WizardPrompter,
    existing: dict[str, str],
    flow: str,
) -> dict[str, str]:
    """Coleta credenciais LLM — provider + modelo."""
    config: dict[str, str] = {}

    # Provider selection
    provider = p.select(
        "Provedor LLM",
        options=[
            {"value": "openai", "label": "OpenAI", "hint": "GPT-5.4, mini, nano"},
            {"value": "anthropic", "label": "Anthropic", "hint": "Claude 3.5, Claude 4"},
            {"value": "google", "label": "Google AI", "hint": "Gemini Pro, Gemini Flash"},
            {"value": "custom", "label": "Outro / OpenAI-compatível", "hint": "LM Studio, Ollama, etc."},
            {"value": "skip", "label": "Pular", "hint": "configurar depois"},
        ],
        initial_value="openai",
    )

    if provider == "skip":
        # Mantém existentes se houver
        for k in _LLM_SECTION_KEYS:
            if existing.get(k):
                config[k] = existing[k]
        return config

    # Modelo
    models = _get_model_options(provider)
    existing_model = existing.get("RLM_MODEL", "")

    if flow == "quickstart" and provider != "custom":
        # QuickStart: usa existente ou primeiro modelo recomendado automaticamente
        selected_model = existing_model or models[0]["value"]
        p.note(f"Modelo selecionado automaticamente: [bold]{selected_model}[/]", title="QuickStart")
    else:
        selected_model = _prompt_model_name(
            p,
            provider,
            "Modelo padrão",
            existing_model,
            models,
        )

    config["RLM_MODEL"] = selected_model

    has_role_models = any(existing.get(env_name) for env_name, _label, _description in _MODEL_ROLE_SPECS)
    route_mode_default = "single" if flow == "quickstart" else "recommended"
    if has_role_models:
        route_mode_default = "manual"

    route_mode = p.select(
        "Estratégia de modelos",
        options=[
            {"value": "single", "label": "Um único modelo", "hint": "usa apenas RLM_MODEL e limpa overrides antigos"},
            {"value": "recommended", "label": "Split recomendado", "hint": "preenche planner, worker, fast e minirepl automaticamente"},
            {"value": "manual", "label": "Escolher por papel", "hint": "configurar planner, worker, evaluator, fast e minirepl"},
        ],
        initial_value=route_mode_default,
    )

    role_defaults = _build_role_model_defaults(existing, provider, selected_model)
    if route_mode == "recommended":
        config.update(role_defaults)
        p.note(_format_role_model_summary(role_defaults), title="Modelos por papel")
    elif route_mode == "manual":
        for env_name, label, description in _MODEL_ROLE_SPECS:
            config[env_name] = _prompt_model_name(
                p,
                provider,
                f"Modelo para {label} ({description})",
                role_defaults[env_name],
                models,
            )
        p.note(_format_role_model_summary(config), title="Modelos por papel")
    else:
        p.note(
            f"  • Todos os papéis usarão [bold]{selected_model}[/]\n"
            "  • Overrides RLM_MODEL_* antigos serão removidos ao salvar",
            title="Modelos",
        )

    # API Key
    key_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "custom": "OPENAI_API_KEY",
    }
    key_name = key_map[provider]
    existing_key = existing.get(key_name, "")
    masked = f"…{existing_key[-6:]}" if len(existing_key) > 10 else ""

    if masked and flow == "quickstart":
        # QuickStart com key existente: mantém
        config[key_name] = existing_key
        p.note(f"Usando API key existente ({masked})", title="QuickStart")
    else:
        prompt_msg = f"API Key ({key_name})"
        if masked:
            prompt_msg += f"  [dim]atual: {masked}[/]"

        api_key = p.text(prompt_msg, default=existing_key, password=not bool(masked))
        if api_key:
            config[key_name] = api_key

        # Validar OpenAI key
        if provider == "openai" and api_key and api_key.startswith("sk-"):
            if p.confirm("Testar chave OpenAI agora?", default=False):
                spinner = p.progress("Verificando…")
                ok = _test_openai_key(api_key)
                if ok:
                    spinner.stop("[bold green]✓[/] Chave válida — conexão OK")
                else:
                    spinner.stop("[bold yellow]⚠[/]  Não validada — verifique depois")

    return config


# ═══════════════════════════════════════════════════════════════════════════ #
# _step_server_config                                                        #
# ═══════════════════════════════════════════════════════════════════════════ #


def _step_server_config(
    p: WizardPrompter,
    existing: dict[str, str],
    flow: str,
) -> dict[str, str]:
    """Coleta endereço/porta do servidor."""
    config: dict[str, str] = {}

    defaults = {
        "RLM_API_HOST": existing.get("RLM_API_HOST", "127.0.0.1"),
        "RLM_API_PORT": existing.get("RLM_API_PORT", "5000"),
        "RLM_WS_HOST": existing.get("RLM_WS_HOST", "127.0.0.1"),
        "RLM_WS_PORT": existing.get("RLM_WS_PORT", "8765"),
    }

    if flow == "quickstart":
        # QuickStart: usa defaults direto
        config.update(defaults)
        p.note(
            f"  • API REST:  {defaults['RLM_API_HOST']}:{defaults['RLM_API_PORT']}\n"
            f"  • WebSocket: {defaults['RLM_WS_HOST']}:{defaults['RLM_WS_PORT']}",
            title="Servidor (defaults)",
        )
        return config

    # Advanced: perguntar tudo
    def _validate_port(v: str) -> str | None:
        try:
            n = int(v)
            if not (1 <= n <= 65535):
                return "Porta deve estar entre 1 e 65535"
        except ValueError:
            return "Deve ser um número inteiro"
        return None

    def _validate_non_empty(v: str) -> str | None:
        return None if v else "IP não pode ser vazio"

    bind_choice = p.select(
        "Bind do servidor (quem pode acessar?)",
        options=[
            {"value": "loopback", "label": "Loopback (127.0.0.1)", "hint": "apenas esta máquina"},
            {"value": "lan", "label": "LAN (0.0.0.0)", "hint": "acessível na rede local"},
            {"value": "custom", "label": "IP customizado", "hint": "definir manualmente"},
        ],
        initial_value="loopback",
    )

    if bind_choice == "loopback":
        host = "127.0.0.1"
    elif bind_choice == "lan":
        host = "0.0.0.0"
    else:
        host = p.text(
            "Endereço IP para bind",
            default=defaults["RLM_API_HOST"],
            validate=_validate_non_empty,
        )

    config["RLM_API_HOST"] = host
    config["RLM_WS_HOST"] = host

    config["RLM_API_PORT"] = p.text(
        "Porta da API REST",
        default=defaults["RLM_API_PORT"],
        validate=_validate_port,
    )
    config["RLM_WS_PORT"] = p.text(
        "Porta do WebSocket",
        default=defaults["RLM_WS_PORT"],
        validate=_validate_port,
    )

    return config


# ═══════════════════════════════════════════════════════════════════════════ #
# _step_channels                                                             #
# ═══════════════════════════════════════════════════════════════════════════ #


def _step_channels(
    p: WizardPrompter,
    existing: dict[str, str],
    flow: str,
) -> dict[str, str]:
    """Coleta tokens de bots e canais de comunicação."""
    config: dict[str, str] = {}

    # Detecta quais canais já têm alguma variável configurada
    pre_configured: list[str] = []
    for spec in CHANNEL_SPECS:
        has_any = any(existing.get(var_name) for var_name, _label, _req in spec["vars"])
        if has_any:
            pre_configured.append(spec["name"])

    if flow == "quickstart":
        # QuickStart: mantém tokens existentes, pergunta se quer configurar novos
        if pre_configured:
            p.note(
                f"Canais já configurados: {', '.join(pre_configured)}",
                title="Canais (QuickStart)",
            )
            for spec in CHANNEL_SPECS:
                for var_name, _label, _req in spec["vars"]:
                    if existing.get(var_name):
                        config[var_name] = existing[var_name]

        add_channels = p.confirm(
            "Configurar canais de comunicação (Telegram, Discord, etc.)?",
            default=not bool(pre_configured),
        )
        if not add_channels:
            return config
    else:
        p.note(
            "Canais permitem que o Arkhe receba e envie mensagens\n"
            "por Telegram, Discord, WhatsApp e Slack.\n"
            "WebChat está sempre ativo e não requer configuração.",
            title="Canais de comunicação",
        )

    for spec in CHANNEL_SPECS:
        channel_name = spec["name"]
        has_existing = any(existing.get(v) for v, _l, _r in spec["vars"])

        if has_existing:
            label = f"({channel_name} já tem tokens)"
        else:
            label = ""

        enable = p.confirm(
            f"Configurar {channel_name}? {label}".strip(),
            default=has_existing,
        )

        if not enable:
            # Mantém existentes mesmo se não reconfigura
            for var_name, _label, _req in spec["vars"]:
                if existing.get(var_name):
                    config[var_name] = existing[var_name]
            continue

        p.note(spec["hint"], title=f"{channel_name} — Setup")

        test_token_value = ""
        for var_name, label, required in spec["vars"]:
            existing_val = existing.get(var_name, "")
            sensitive = is_sensitive_channel_var(var_name)
            masked = ""
            if existing_val and sensitive:
                masked = f"…{existing_val[-6:]}" if len(existing_val) > 8 else "configurado"

            prompt_msg = f"{var_name}"
            if masked:
                prompt_msg += f"  [dim]atual: {masked}[/]"
            if not required:
                prompt_msg += "  [dim](opcional)[/]"

            val = p.text(
                prompt_msg,
                default="" if sensitive else existing_val,
                password=sensitive,
            )

            if val:
                config[var_name] = val
            elif existing_val:
                config[var_name] = existing_val

        # Teste de conectividade (se há fn de teste e token)
        test_fn = spec.get("test_fn")
        test_env_var = spec.get("test_env_var")
        test_token_value = config.get(test_env_var, "").strip() if test_env_var else ""

        if callable(test_fn) and test_token_value:
            if p.confirm(f"Testar token do {channel_name} agora?", default=True):
                spinner = p.progress(f"Conectando ao {channel_name}…")
                ok, msg = test_fn(test_token_value)
                if ok:
                    spinner.stop(f"[bold green]✓[/] {channel_name} OK — {msg}")
                else:
                    spinner.stop(f"[bold yellow]⚠[/]  {channel_name} falhou: {msg}")

    # WebChat é passivo — informar
    p.note("WebChat está sempre ativo (sem tokens necessários).", title="WebChat")

    return config


# ═══════════════════════════════════════════════════════════════════════════ #
# _step_security_tokens                                                      #
# ═══════════════════════════════════════════════════════════════════════════ #


def _step_security_tokens(
    p: WizardPrompter,
    existing: dict[str, str],
    flow: str,
) -> dict[str, str]:
    """Gera ou mantém tokens de segurança."""
    config: dict[str, str] = {}

    token_specs = [
        ("RLM_WS_TOKEN", "WebSocket / observabilidade"),
        ("RLM_INTERNAL_TOKEN", "API interna /webhook/{client_id}"),
        ("RLM_ADMIN_TOKEN", "rotas administrativas e health"),
        ("RLM_HOOK_TOKEN", "webhooks externos /api/hooks"),
        ("RLM_API_TOKEN", "API OpenAI-compatible /v1"),
    ]

    if flow == "quickstart":
        # QuickStart: gera tudo automaticamente, mantém existentes
        generated = 0
        kept = 0
        for env_name, _label in token_specs:
            if existing.get(env_name):
                config[env_name] = existing[env_name]
                kept += 1
            else:
                config[env_name] = secrets.token_hex(32)
                generated += 1

        parts: list[str] = []
        if generated:
            parts.append(f"{generated} gerados")
        if kept:
            parts.append(f"{kept} mantidos")
        p.note(
            f"Tokens de segurança: {', '.join(parts)}",
            title="Segurança (auto)",
        )
        return config

    # Advanced: perguntar por cada token existente
    for env_name, label in token_specs:
        existing_value = existing.get(env_name, "")
        if existing_value:
            regenerate = p.confirm(
                f"{env_name} ({label}) já existe (…{existing_value[-6:]}). Regenerar?",
                default=False,
            )
            config[env_name] = secrets.token_hex(32) if regenerate else existing_value
        else:
            config[env_name] = secrets.token_hex(32)

    p.note(f"{len(token_specs)} tokens de segurança configurados.", title="✓ Segurança")
    return config
