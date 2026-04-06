"""wizard/ — subpacote de onboarding interativo Arkhe.

Re-exporta a API pública para que ``from rlm.cli.wizard import X`` funcione.
Módulos internos são carregados sob demanda via __getattr__ para evitar
carga eager de rich_prompter, steps, channels etc. quando não necessário.
"""

from rlm.cli.wizard.onboarding import run_wizard as run_wizard  # noqa: F401 — public API
from rlm.cli.wizard.prompter import (  # noqa: F401 — public API
    WizardCancelledError as WizardCancelledError,
    WizardPrompter as WizardPrompter,
)

_LAZY: dict[str, str] = {
    # env_utils
    "_Env": "rlm.cli.wizard.env_utils",
    "_build_role_model_defaults": "rlm.cli.wizard.env_utils",
    "_format_role_model_summary": "rlm.cli.wizard.env_utils",
    "_get_model_options": "rlm.cli.wizard.env_utils",
    "_LLM_SECTION_KEYS": "rlm.cli.wizard.env_utils",
    "_load_existing_env": "rlm.cli.wizard.env_utils",
    "_MANAGED_ENV_KEYS": "rlm.cli.wizard.env_utils",
    "_MANAGED_ENV_SECTIONS": "rlm.cli.wizard.env_utils",
    "_MODEL_ROLE_SPECS": "rlm.cli.wizard.env_utils",
    "_probe_server": "rlm.cli.wizard.env_utils",
    "_prompt_model_name": "rlm.cli.wizard.env_utils",
    "_PROVIDER_MODEL_OPTIONS": "rlm.cli.wizard.env_utils",
    "_PROVIDER_ROLE_DEFAULTS": "rlm.cli.wizard.env_utils",
    "_resolve_env_path": "rlm.cli.wizard.env_utils",
    "_SECURITY_SECTION_KEYS": "rlm.cli.wizard.env_utils",
    "_SERVER_SECTION_KEYS": "rlm.cli.wizard.env_utils",
    "_summarize_existing_config": "rlm.cli.wizard.env_utils",
    "_test_openai_key": "rlm.cli.wizard.env_utils",
    "_write_env": "rlm.cli.wizard.env_utils",
    # onboarding (non-public helpers)
    "_collect_config": "rlm.cli.wizard.onboarding",
    "_run_onboarding": "rlm.cli.wizard.onboarding",
    "_setup_daemon": "rlm.cli.wizard.onboarding",
    "_show_summary": "rlm.cli.wizard.onboarding",
    # prompter (non-public)
    "_ProgressHandle": "rlm.cli.wizard.prompter",
    # rich_prompter
    "HAS_RICH": "rlm.cli.wizard.rich_prompter",
    "RichPrompter": "rlm.cli.wizard.rich_prompter",
    "_ask": "rlm.cli.wizard.rich_prompter",
    "_clean_markup": "rlm.cli.wizard.rich_prompter",
    "_confirm": "rlm.cli.wizard.rich_prompter",
    "_console": "rlm.cli.wizard.rich_prompter",
    "_default_prompter": "rlm.cli.wizard.rich_prompter",
    "_panel": "rlm.cli.wizard.rich_prompter",
    "_PlainProgressHandle": "rlm.cli.wizard.rich_prompter",
    "_print": "rlm.cli.wizard.rich_prompter",
    "_RichProgressHandle": "rlm.cli.wizard.rich_prompter",
    "_rule": "rlm.cli.wizard.rich_prompter",
    # channels
    "_CHANNEL_SPECS": "rlm.cli.wizard.channels",
    "_test_discord_token": "rlm.cli.wizard.channels",
    "_test_telegram_token": "rlm.cli.wizard.channels",
    # steps
    "_step_channels": "rlm.cli.wizard.steps",
    "_step_llm_credentials": "rlm.cli.wizard.steps",
    "_step_security_tokens": "rlm.cli.wizard.steps",
    "_step_server_config": "rlm.cli.wizard.steps",
}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib
        mod = importlib.import_module(_LAZY[name])
        value = getattr(mod, name)
        globals()[name] = value  # cache
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
