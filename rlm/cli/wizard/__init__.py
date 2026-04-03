"""wizard/ — subpacote de onboarding interativo Arkhe.

Re-exporta toda a API pública para que ``from rlm.cli.wizard import X`` funcione.
"""

from rlm.cli.wizard.channels import (
    _CHANNEL_SPECS as _CHANNEL_SPECS,
    _test_discord_token as _test_discord_token,
    _test_telegram_token as _test_telegram_token,
)
from rlm.cli.wizard.env_utils import (
    _Env as _Env,
    _build_role_model_defaults as _build_role_model_defaults,
    _format_role_model_summary as _format_role_model_summary,
    _get_model_options as _get_model_options,
    _LLM_SECTION_KEYS as _LLM_SECTION_KEYS,
    _load_existing_env as _load_existing_env,
    _MANAGED_ENV_KEYS as _MANAGED_ENV_KEYS,
    _MANAGED_ENV_SECTIONS as _MANAGED_ENV_SECTIONS,
    _MODEL_ROLE_SPECS as _MODEL_ROLE_SPECS,
    _probe_server as _probe_server,
    _prompt_model_name as _prompt_model_name,
    _PROVIDER_MODEL_OPTIONS as _PROVIDER_MODEL_OPTIONS,
    _PROVIDER_ROLE_DEFAULTS as _PROVIDER_ROLE_DEFAULTS,
    _resolve_env_path as _resolve_env_path,
    _SECURITY_SECTION_KEYS as _SECURITY_SECTION_KEYS,
    _SERVER_SECTION_KEYS as _SERVER_SECTION_KEYS,
    _summarize_existing_config as _summarize_existing_config,
    _test_openai_key as _test_openai_key,
    _write_env as _write_env,
)
from rlm.cli.wizard.onboarding import (
    run_wizard as run_wizard,
    _collect_config as _collect_config,
    _run_onboarding as _run_onboarding,
    _setup_daemon as _setup_daemon,
    _show_summary as _show_summary,
)
from rlm.cli.wizard.prompter import (
    WizardCancelledError as WizardCancelledError,
    WizardPrompter as WizardPrompter,
    _ProgressHandle as _ProgressHandle,
)
from rlm.cli.wizard.rich_prompter import (
    HAS_RICH as HAS_RICH,
    RichPrompter as RichPrompter,
    _ask as _ask,
    _clean_markup as _clean_markup,
    _confirm as _confirm,
    _console as _console,
    _default_prompter as _default_prompter,
    _panel as _panel,
    _PlainProgressHandle as _PlainProgressHandle,
    _print as _print,
    _RichProgressHandle as _RichProgressHandle,
    _rule as _rule,
)
from rlm.cli.wizard.steps import (
    _step_channels as _step_channels,
    _step_llm_credentials as _step_llm_credentials,
    _step_security_tokens as _step_security_tokens,
    _step_server_config as _step_server_config,
)
