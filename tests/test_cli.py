"""Tests para o RLM CLI (rlm/cli/).

Cobertura:
    - main.py: parser, dispatch, token rotate, doctor, cmd_version
  - wizard.py: _write_env, _load_existing_env, _resolve_env_path
  - service.py: _read_pid/_write_pid/_pid_alive, start_services (mock),
                stop_services (mock), show_status (mock)

Todos os testes rodam sem interação humana e sem dependências externas
(systemd, launchd, wg, OpenAI). Processos reais são mockados.
"""

from __future__ import annotations

import json
import os
import sys
import secrets
import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest


# --------------------------------------------------------------------------- #
# Helpers fixtures                                                              #
# --------------------------------------------------------------------------- #

@pytest.fixture()
def tmp_env(tmp_path: Path) -> Path:
    """Retorna caminho de .env temporário."""
    return tmp_path / ".env"


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Simula raiz de projeto com pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "rlm"\n')
    return tmp_path


# =========================================================================== #
# 1. main.py                                                                   #
# =========================================================================== #

class TestCLIParser:
    """Parser e dispatch básico."""

    def test_no_args_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rlm.cli.main import main
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 0

    def test_version_subcommand_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rlm.cli.main import main
        with pytest.raises(SystemExit) as exc:
            main(["version"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "arkhe" in out

    def test_version_flag_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rlm.cli.main import main
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "arkhe" in out

    def test_unknown_command_exits_nonzero(self) -> None:
        from rlm.cli.main import main
        with pytest.raises(SystemExit) as exc:
            main(["comando-inexistente"])
        # argparse produz exit 2 para argumentos inválidos
        assert exc.value.code in (1, 2)

    def test_start_foreground_flag_parsed(self) -> None:
        from rlm.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["start", "--foreground"])
        assert args.foreground is True

    def test_start_api_only_flag_parsed(self) -> None:
        from rlm.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["start", "--api-only"])
        assert args.api_only is True

    def test_start_ws_only_flag_parsed(self) -> None:
        from rlm.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["start", "--ws-only"])
        assert args.ws_only is True

    def test_tui_flags_parsed(self) -> None:
        from rlm.cli.parser import build_parser

        parser = build_parser()
        args = parser.parse_args(["tui", "--client-id", "tui:demo", "--refresh-interval", "1.5", "--once"])

        assert args.client_id == "tui:demo"
        assert args.refresh_interval == 1.5
        assert args.once is True

    def test_peer_add_args_parsed(self) -> None:
        from rlm.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["peer", "add", "--name", "laptop",
                                   "--pubkey", "ABC123", "--ip", "10.0.0.2"])
        assert args.peer_command == "add"
        assert args.name == "laptop"
        assert args.pubkey == "ABC123"
        assert args.ip == "10.0.0.2"

    def test_update_flags_parsed(self) -> None:
        from rlm.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["update", "--check", "--no-restart"])
        assert args.check is True
        assert args.no_restart is True

    def test_status_json_flag_parsed(self) -> None:
        from rlm.cli.parser import build_parser

        parser = build_parser()
        args = parser.parse_args(["status", "--json"])

        assert args.json is True

    def test_status_alias_parsed(self) -> None:
        from rlm.cli.main import main
        with patch.dict("rlm.cli.main.DISPATCH", {"ps": MagicMock(return_value=0)}):
            with pytest.raises(SystemExit) as exc:
                main(["ps"])
        assert exc.value.code == 0

    def test_doctor_alias_parsed(self) -> None:
        from rlm.cli.main import main
        with patch.dict("rlm.cli.main.DISPATCH", {"diag": MagicMock(return_value=0)}):
            with pytest.raises(SystemExit) as exc:
                main(["diag"])
        assert exc.value.code == 0

    def test_channel_list_alias_parsed(self) -> None:
        from rlm.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["channel", "ls"])
        assert args.command == "channel"
        assert args.channel_command == "ls"

    def test_skill_list_alias_parsed(self) -> None:
        from rlm.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["skill", "ls"])
        assert args.command == "skill"
        assert args.skill_command == "ls"

    def test_start_help_has_recovery_guidance(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rlm.cli.parser import build_parser

        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["start", "--help"])

        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "Recuperação:" in out
        assert "arkhe start --foreground" in out
        assert "arkhe doctor" in out

    def test_update_help_has_recovery_guidance(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rlm.cli.parser import build_parser

        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["update", "--help"])

        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "Recuperação:" in out
        assert "arkhe update --check" in out
        assert "uv sync" in out

    def test_doctor_help_has_recovery_guidance(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rlm.cli.parser import build_parser

        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["doctor", "--help"])

        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "Recuperação:" in out
        assert "arkhe token rotate" in out
        assert "arkhe start" in out

    def test_doctor_launcher_state_json_flag_parsed(self) -> None:
        from rlm.cli.parser import build_parser

        parser = build_parser()
        args = parser.parse_args(["doctor", "--launcher-state-json"])

        assert args.launcher_state_json is True

    def test_root_help_mentions_operational_aliases(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rlm.cli.main import main

        with pytest.raises(SystemExit) as exc:
            main([])

        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "arkhe diag" in out
        assert "arkhe ps" in out
        assert "arkhe channel ls" in out
        assert "arkhe skill ls" in out

    def test_channel_list_alias_executes(self) -> None:
        from rlm.cli.main import main

        with patch.dict("rlm.cli.main.NESTED_DISPATCH", {"channel": {"ls": MagicMock(return_value=0)}}):
            with pytest.raises(SystemExit) as exc:
                main(["channel", "ls"])

        assert exc.value.code == 0

    def test_skill_list_alias_executes(self) -> None:
        from rlm.cli.main import main

        with patch.dict("rlm.cli.main.NESTED_DISPATCH", {"skill": {"ls": MagicMock(return_value=0)}}):
            with pytest.raises(SystemExit) as exc:
                main(["skill", "ls"])

        assert exc.value.code == 0


class TestUpdateCommand:
    def test_update_dispatches_to_service(self) -> None:
        from rlm.cli.main import main
        with patch.dict("rlm.cli.main.DISPATCH", {"update": MagicMock(return_value=0)}):
            with pytest.raises(SystemExit) as exc:
                main(["update"])
        assert exc.value.code == 0

    def test_tui_dispatches_to_handler(self) -> None:
        from rlm.cli.main import main

        with patch.dict("rlm.cli.main.DISPATCH", {"tui": MagicMock(return_value=0)}):
            with pytest.raises(SystemExit) as exc:
                main(["tui", "--once"])

        assert exc.value.code == 0


class TestDoctorHelpers:
    def test_doctor_runtime_requirement_fails_on_old_python(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from rlm.cli.commands.doctor import _doctor_runtime_requirement

        monkeypatch.setattr(sys, "version_info", (3, 10, 11, "final", 0))
        ok, detail = _doctor_runtime_requirement()

        assert ok is False
        assert ">= 3.11" in detail

    def test_doctor_channel_status_flags_incomplete_discord(self) -> None:
        from rlm.cli.commands.doctor import _doctor_channel_status

        status, detail = _doctor_channel_status({"DISCORD_APP_ID": "abc"}, "Discord")

        assert status == "⚠"
        assert "DISCORD_APP_PUBLIC_KEY" in detail

    def test_doctor_channel_status_marks_whatsapp_complete(self) -> None:
        from rlm.cli.commands.doctor import _doctor_channel_status

        status, detail = _doctor_channel_status(
            {
                "WHATSAPP_VERIFY_TOKEN": "verify",
                "WHATSAPP_TOKEN": "token",
                "WHATSAPP_PHONE_ID": "phone",
            },
            "WhatsApp",
        )

        assert status == "✓"
        assert detail == "configurado"

    def test_cmd_doctor_reports_old_python(self, tmp_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        from rlm.cli.commands.doctor import cmd_doctor

        tmp_env.write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
        monkeypatch.chdir(tmp_env.parent)
        monkeypatch.setattr(sys, "version_info", (3, 10, 11, "final", 0))

        rc = cmd_doctor(argparse.Namespace())

        assert rc == 1
        out = capsys.readouterr().out
        assert "Python runtime" in out
        assert ">= 3.11" in out

    def test_doctor_channel_handshake_telegram_success(self) -> None:
        from rlm.cli.commands.doctor import _doctor_channel_handshake

        response = MagicMock()
        response.status = 200
        response.read.return_value = b'{"ok": true, "result": {"username": "rlm_bot"}}'
        response.__enter__.return_value = response
        response.__exit__.return_value = None

        with patch("rlm.cli.commands.doctor.urllib_request.urlopen", return_value=response):
            status, detail = _doctor_channel_handshake(
                {"TELEGRAM_BOT_TOKEN": "123:abc"},
                "Telegram",
                server_online=False,
                rlm_host="http://127.0.0.1:5000",
            )

        assert status == "✓"
        assert "getMe OK" in detail

    def test_doctor_channel_handshake_whatsapp_local_success(self) -> None:
        from rlm.cli.commands.doctor import _doctor_channel_handshake

        response = MagicMock()
        response.status = 200
        response.read.return_value = b"rlm-doctor"
        response.__enter__.return_value = response
        response.__exit__.return_value = None

        with patch("rlm.cli.commands.doctor.urllib_request.urlopen", return_value=response):
            status, detail = _doctor_channel_handshake(
                {"WHATSAPP_VERIFY_TOKEN": "verify"},
                "WhatsApp",
                server_online=True,
                rlm_host="http://127.0.0.1:5000",
            )

        assert status == "✓"
        assert "challenge local OK" in detail

    def test_doctor_channel_handshake_discord_reports_http_error(self) -> None:
        from rlm.cli.commands.doctor import _doctor_channel_handshake

        with patch(
            "rlm.cli.commands.doctor.urllib_request.urlopen",
            side_effect=HTTPError("https://discord.com", 401, "Unauthorized", {}, None),
        ):
            status, detail = _doctor_channel_handshake(
                {"DISCORD_BOT_TOKEN": "Bot token"},
                "Discord",
                server_online=False,
                rlm_host="http://127.0.0.1:5000",
            )

        assert status == "✗"
        assert "Discord HTTP 401" in detail

    def test_doctor_launcher_state_warns_when_online_without_state(self, tmp_path: Path) -> None:
        from rlm.cli.commands.doctor import _doctor_launcher_state_status
        from rlm.cli.context import CliContext

        context = CliContext(env={}, cwd=tmp_path, home=tmp_path)

        with patch("rlm.cli.launcher_state.port_accepting_connections", return_value=False):
            status, detail = _doctor_launcher_state_status(context, server_online=True)

        assert status == "⚠"
        assert "processo externo ao launcher" in detail

    def test_doctor_launcher_state_warns_when_persisted_running_but_offline(self, tmp_path: Path) -> None:
        from rlm.cli.commands.doctor import _doctor_launcher_state_status
        from rlm.cli.context import CliContext
        from rlm.cli.launcher_state import mark_bootstrap_success

        context = CliContext(env={}, cwd=tmp_path, home=tmp_path)
        mark_bootstrap_success(
            context,
            source="start",
            mode="background-combined",
            api_enabled=True,
            ws_enabled=True,
        )

        with patch("rlm.cli.launcher_state.port_accepting_connections", return_value=False):
            status, detail = _doctor_launcher_state_status(context, server_online=False)

        assert status == "⚠"
        assert "estado stale após crash" in detail

    def test_doctor_launcher_state_json_emits_machine_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from rlm.cli.commands.doctor import cmd_doctor
        from rlm.cli.context import CliContext

        context = CliContext(env={}, cwd=tmp_path, home=tmp_path)

        with (
            patch("rlm.cli.commands.doctor._doctor_http_request", side_effect=Exception("offline")),
            patch("rlm.cli.launcher_state.port_accepting_connections", return_value=False),
        ):
            rc = cmd_doctor(argparse.Namespace(launcher_state_json=True), context=context)

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "doctor"
        assert payload["schema_version"] == 1
        assert payload["payload"]["classification"] == "no-state"
        assert payload["classification"] == "no-state"
        assert payload["severity"] == "info"
        assert payload["signals"]["state_exists"] is False

    def test_cmd_setup_blocks_old_python(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from rlm.cli.commands.setup import cmd_setup

        monkeypatch.setattr(sys, "version_info", (3, 10, 11, "final", 0))
        with patch("rlm.cli.wizard.run_wizard", return_value=0) as mock_run:
            rc = cmd_setup(argparse.Namespace())

        assert rc == 1
        mock_run.assert_not_called()

    def test_cmd_start_blocks_old_python(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from rlm.cli.commands.service import cmd_start

        monkeypatch.setattr(sys, "version_info", (3, 10, 11, "final", 0))
        args = argparse.Namespace(foreground=False, api_only=False, ws_only=False)
        with patch("rlm.cli.service.start_services", return_value=0) as mock_start:
            rc = cmd_start(args)

        assert rc == 1
        mock_start.assert_not_called()


class TestTokenRotate:
    """cmd_token_rotate: escreve novos tokens no .env."""

    def test_creates_tokens_if_absent(
        self, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tmp_env.write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
        from rlm.cli.commands.token import cmd_token_rotate
        args = argparse.Namespace()

        monkeypatch.chdir(tmp_env.parent)
        rc = cmd_token_rotate(args)

        assert rc == 0
        content = tmp_env.read_text(encoding="utf-8")
        assert "RLM_WS_TOKEN=" in content
        assert "RLM_INTERNAL_TOKEN=" in content
        assert "RLM_ADMIN_TOKEN=" in content
        assert "RLM_HOOK_TOKEN=" in content
        assert "RLM_API_TOKEN=" in content
        # Garante que a chave existente foi preservada
        assert "OPENAI_API_KEY=sk-test" in content

    def test_rotates_existing_tokens(
        self, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old_ws = "a" * 64
        old_internal = "b" * 64
        old_admin = "c" * 64
        old_hook = "d" * 64
        old_api = "e" * 64
        tmp_env.write_text(
            f"RLM_WS_TOKEN={old_ws}\n"
            f"RLM_INTERNAL_TOKEN={old_internal}\n"
            f"RLM_ADMIN_TOKEN={old_admin}\n"
            f"RLM_HOOK_TOKEN={old_hook}\n"
            f"RLM_API_TOKEN={old_api}\n",
            encoding="utf-8",
        )
        from rlm.cli.commands.token import cmd_token_rotate
        args = argparse.Namespace()

        monkeypatch.chdir(tmp_env.parent)
        rc = cmd_token_rotate(args)

        assert rc == 0
        content = tmp_env.read_text(encoding="utf-8")
        assert old_ws not in content
        assert old_internal not in content
        assert old_admin not in content
        assert old_hook not in content
        assert old_api not in content
        assert "RLM_WS_TOKEN=" in content
        assert "RLM_INTERNAL_TOKEN=" in content
        assert "RLM_ADMIN_TOKEN=" in content
        assert "RLM_HOOK_TOKEN=" in content
        assert "RLM_API_TOKEN=" in content

    def test_returns_1_if_no_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rlm.cli.commands.token import cmd_token_rotate
        args = argparse.Namespace()

        # Muda para dir que definitivamente não tem .env nem ~/.rlm/.env
        empty_dir = tmp_path / "empty_subdir"
        empty_dir.mkdir()
        monkeypatch.chdir(empty_dir)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "fakehome")
        rc = cmd_token_rotate(args)

        assert rc == 1


# =========================================================================== #
# 2. wizard.py                                                                 #
# =========================================================================== #

class TestWizardEnvIO:
    """_write_env e _load_existing_env."""

    def test_write_and_load_roundtrip(self, tmp_env: Path) -> None:
        from rlm.cli.wizard import _load_existing_env, _write_env
        original = {
            "OPENAI_API_KEY": "sk-abc",
            "RLM_MODEL": "gpt-4o",
            "RLM_WS_TOKEN": secrets.token_hex(32),
            "RLM_INTERNAL_TOKEN": secrets.token_hex(32),
            "RLM_ADMIN_TOKEN": secrets.token_hex(32),
            "RLM_HOOK_TOKEN": secrets.token_hex(32),
            "RLM_API_TOKEN": secrets.token_hex(32),
            "RLM_API_HOST": "127.0.0.1",
            "RLM_API_PORT": "5000",
        }
        _write_env(tmp_env, original)
        loaded = _load_existing_env(tmp_env)
        for k, v in original.items():
            assert loaded[k] == v

    def test_write_preserves_extra_keys(self, tmp_env: Path) -> None:
        from rlm.cli.wizard import _load_existing_env, _write_env
        tmp_env.write_text("TELEGRAM_TOKEN=abc123\n")
        _write_env(tmp_env, {"RLM_MODEL": "gpt-4o"})
        loaded = _load_existing_env(tmp_env)
        assert loaded["TELEGRAM_TOKEN"] == "abc123"
        assert loaded["RLM_MODEL"] == "gpt-4o"

    def test_write_overwrites_managed_keys(self, tmp_env: Path) -> None:
        from rlm.cli.wizard import _load_existing_env, _write_env
        tmp_env.write_text("RLM_MODEL=gpt-3.5-turbo\n")
        _write_env(tmp_env, {"RLM_MODEL": "gpt-4o"})
        loaded = _load_existing_env(tmp_env)
        assert loaded["RLM_MODEL"] == "gpt-4o"

    def test_load_ignores_comments(self, tmp_env: Path) -> None:
        from rlm.cli.wizard import _load_existing_env
        tmp_env.write_text("# comment line\nRLM_MODEL=gpt-4o\n# outro\n", encoding="utf-8")
        loaded = _load_existing_env(tmp_env)
        assert list(loaded.keys()) == ["RLM_MODEL"]

    def test_load_empty_file_returns_empty(self, tmp_env: Path) -> None:
        from rlm.cli.wizard import _load_existing_env
        tmp_env.write_text("")
        assert _load_existing_env(tmp_env) == {}

    def test_load_nonexistent_returns_empty(self, tmp_path: Path) -> None:
        from rlm.cli.wizard import _load_existing_env
        assert _load_existing_env(tmp_path / "nope.env") == {}

    def test_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        from rlm.cli.wizard import _write_env
        deep = tmp_path / "a" / "b" / "c" / ".env"
        _write_env(deep, {"RLM_MODEL": "gpt-4o"})
        assert deep.exists()

    def test_resolve_env_path_prefers_local(self, tmp_project: Path) -> None:
        from rlm.cli.wizard import _resolve_env_path
        # Cria .env local
        local_env = tmp_project / ".env"
        local_env.write_text("")
        result = _resolve_env_path(tmp_project)
        assert result == local_env

    def test_resolve_env_path_returns_local_if_not_exists(self, tmp_project: Path) -> None:
        from rlm.cli.wizard import _resolve_env_path
        result = _resolve_env_path(tmp_project)
        assert result == tmp_project / ".env"


class TestTokenGeneration:
    """Tokens gerados pelo wizard são criptograficamente suficientes."""

    def test_generated_token_length(self) -> None:
        token = secrets.token_hex(32)
        assert len(token) == 64  # 32 bytes = 64 hex chars

    def test_generated_tokens_are_unique(self) -> None:
        tokens = {secrets.token_hex(32) for _ in range(100)}
        assert len(tokens) == 100  # sem colisões


class TestSetupFlowFlag:
    """Parser aceita --flow no subcomando setup."""

    def test_setup_flow_quickstart(self) -> None:
        from rlm.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["setup", "--flow", "quickstart"])
        assert args.flow == "quickstart"

    def test_setup_flow_advanced(self) -> None:
        from rlm.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["setup", "--flow", "advanced"])
        assert args.flow == "advanced"

    def test_setup_flow_default_is_none(self) -> None:
        from rlm.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["setup"])
        assert args.flow is None

    def test_setup_flow_invalid_rejected(self) -> None:
        from rlm.cli.parser import build_parser
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["setup", "--flow", "invalid"])


class TestWizardPrompterInterface:
    """WizardPrompter ABC e RichPrompter podem ser instanciados."""

    def test_wizard_cancelled_error(self) -> None:
        from rlm.cli.wizard import WizardCancelledError
        err = WizardCancelledError("test")
        assert str(err) == "test"
        err2 = WizardCancelledError()
        assert "cancelado" in str(err2)

    def test_rich_prompter_instantiates(self) -> None:
        from rlm.cli.wizard import RichPrompter
        prompter = RichPrompter()
        assert prompter is not None

    def test_summarize_existing_config(self) -> None:
        from rlm.cli.wizard import _summarize_existing_config
        config = {
            "OPENAI_API_KEY": "sk-1234567890abcdef",
            "RLM_MODEL": "gpt-4o",
            "RLM_API_HOST": "127.0.0.1",
            "RLM_API_PORT": "5000",
            "RLM_WS_HOST": "127.0.0.1",
            "RLM_WS_PORT": "8765",
            "RLM_WS_TOKEN": "abc123",
        }
        summary = _summarize_existing_config(config)
        assert "OpenAI" in summary
        assert "gpt-4o" in summary
        assert "127.0.0.1" in summary
        assert "1 configurados" in summary

    def test_summarize_empty_config(self) -> None:
        from rlm.cli.wizard import _summarize_existing_config
        assert "(vazio)" in _summarize_existing_config({})

    def test_probe_server_unreachable(self) -> None:
        from rlm.cli.wizard import _probe_server
        # Porta que certamente não está em uso
        assert _probe_server("127.0.0.1", "19999") is False

    def test_run_wizard_accepts_flow_param(self) -> None:
        """run_wizard aceita parâmetro flow sem erro de assinatura."""
        import inspect
        from rlm.cli.wizard import run_wizard
        sig = inspect.signature(run_wizard)
        assert "flow" in sig.parameters


# =========================================================================== #
# 3. service.py                                                                #
# =========================================================================== #

class TestPidHelpers:
    """_read_pid, _write_pid, _pid_alive."""

    def test_write_and_read(self, tmp_path: Path) -> None:
        from rlm.cli.service import _read_pid, _write_pid
        pid_file = tmp_path / "test.pid"
        _write_pid(pid_file, 12345)
        assert _read_pid(pid_file) == 12345

    def test_read_nonexistent_returns_none(self, tmp_path: Path) -> None:
        from rlm.cli.service import _read_pid
        assert _read_pid(tmp_path / "ghost.pid") is None

    def test_read_invalid_content_returns_none(self, tmp_path: Path) -> None:
        from rlm.cli.service import _read_pid
        f = tmp_path / "bad.pid"
        f.write_text("not-a-number")
        assert _read_pid(f) is None

    def test_pid_alive_current_process(self) -> None:
        """Verifica processo existente usando mock multiplataforma."""
        from rlm.cli.service import _pid_alive
        import sys
        if sys.platform == "win32":
            # no Windows _pid_alive usa tasklist — mocka subprocess.run
            mock_result = MagicMock()
            mock_result.stdout = f'"python.exe","{os.getpid()}"\n'
            with patch("subprocess.run", return_value=mock_result):
                assert _pid_alive(os.getpid()) is True
        else:
            # POSIX: os.kill(pid, 0) no próprio processo nunca lança
            assert _pid_alive(os.getpid()) is True

    def test_pid_alive_nonexistent(self) -> None:
        from rlm.cli.service import _pid_alive
        import sys
        if sys.platform == "win32":
            mock_result = MagicMock()
            mock_result.stdout = "INFO: No tasks are running which match the specified criteria.\n"
            with patch("subprocess.run", return_value=mock_result):
                assert _pid_alive(999_999_999) is False
        else:
            assert _pid_alive(999_999_999) is False

    def test_write_creates_parent_dir(self, tmp_path: Path) -> None:
        from rlm.cli.service import _write_pid
        deep = tmp_path / "deep" / "run" / "test.pid"
        _write_pid(deep, 42)
        assert deep.read_text() == "42"


class TestStartServices:
    """start_services não lança exceções com mocks adequados."""

    def test_start_background_api_and_ws(self, tmp_path: Path) -> None:
        from rlm.cli import service as svc

        fake_proc = MagicMock()
        fake_proc.pid = 9999
        fake_proc.poll.return_value = None
        pid_api = tmp_path / "run" / "api.pid"
        pid_ws = tmp_path / "run" / "ws.pid"

        with (
            patch("rlm.cli.service._PID_DIR", tmp_path / "run"),
            patch("rlm.cli.service._PID_API", pid_api),
            patch("rlm.cli.service._PID_WS", pid_ws),
            patch("rlm.cli.service._LOG_DIR", tmp_path / "logs"),
            patch("subprocess.Popen", return_value=fake_proc) as mock_popen,
            patch("builtins.open", MagicMock()),
            patch("time.sleep"),
        ):
            rc = svc.start_services(foreground=False, api_only=False, ws_only=False)

        assert rc == 0
        assert mock_popen.call_count == 1
        assert pid_api.read_text() == "9999"
        assert pid_ws.read_text() == "9999"

    def test_start_api_only(self, tmp_path: Path) -> None:
        from rlm.cli import service as svc

        fake_proc = MagicMock()
        fake_proc.pid = 1234
        fake_proc.poll.return_value = None
        pid_api = tmp_path / "run" / "api.pid"
        pid_ws = tmp_path / "run" / "ws.pid"

        with (
            patch("rlm.cli.service._PID_DIR", tmp_path / "run"),
            patch("rlm.cli.service._PID_API", pid_api),
            patch("rlm.cli.service._PID_WS", pid_ws),
            patch("rlm.cli.service._LOG_DIR", tmp_path / "logs"),
            patch("subprocess.Popen", return_value=fake_proc) as mock_popen,
            patch("builtins.open", MagicMock()),
            patch("time.sleep"),
        ):
            rc = svc.start_services(foreground=False, api_only=True, ws_only=False)

        assert rc == 0
        assert mock_popen.call_count == 1
        assert pid_api.read_text() == "1234"
        assert not pid_ws.exists()

    def test_start_ws_only(self, tmp_path: Path) -> None:
        from rlm.cli import service as svc

        fake_proc = MagicMock()
        fake_proc.pid = 5678
        fake_proc.poll.return_value = None
        pid_api = tmp_path / "run" / "api.pid"
        pid_ws = tmp_path / "run" / "ws.pid"

        with (
            patch("rlm.cli.service._PID_DIR", tmp_path / "run"),
            patch("rlm.cli.service._PID_API", pid_api),
            patch("rlm.cli.service._PID_WS", pid_ws),
            patch("rlm.cli.service._LOG_DIR", tmp_path / "logs"),
            patch("subprocess.Popen", return_value=fake_proc) as mock_popen,
            patch("builtins.open", MagicMock()),
            patch("time.sleep"),
        ):
            rc = svc.start_services(foreground=False, api_only=False, ws_only=True)

        assert rc == 0
        assert mock_popen.call_count == 1
        assert not pid_api.exists()
        assert pid_ws.read_text() == "5678"

    def test_start_background_returns_error_if_api_dies_early(self, tmp_path: Path) -> None:
        from rlm.cli import service as svc

        fake_proc = MagicMock()
        fake_proc.pid = 4321
        fake_proc.poll.return_value = 1
        fake_proc.returncode = 1
        pid_api = tmp_path / "run" / "api.pid"
        pid_ws = tmp_path / "run" / "ws.pid"

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "api.log").write_text("boom\n")

        with (
            patch("rlm.cli.service._PID_DIR", tmp_path / "run"),
            patch("rlm.cli.service._PID_API", pid_api),
            patch("rlm.cli.service._PID_WS", pid_ws),
            patch("rlm.cli.service._LOG_DIR", log_dir),
            patch("subprocess.Popen", return_value=fake_proc),
            patch("builtins.open", MagicMock()),
            patch("time.sleep"),
        ):
            rc = svc.start_services(foreground=False, api_only=False, ws_only=False)

        assert rc == 1
        assert not pid_api.exists()
        assert not pid_ws.exists()


class TestStopServices:
    """stop_services lê PIDs e envia SIGTERM."""

    def test_stop_running_process(self, tmp_path: Path) -> None:
        from rlm.cli import service as svc

        pid_api = tmp_path / "api.pid"
        pid_ws  = tmp_path / "ws.pid"
        pid_api.write_text(str(os.getpid()))  # PID real (alive)
        pid_ws.write_text(str(os.getpid()))

        with (
            patch("rlm.cli.service._PID_API", pid_api),
            patch("rlm.cli.service._PID_WS",  pid_ws),
            patch("os.kill") as mock_kill,
        ):
            rc = svc.stop_services()

        assert rc == 0
        # SIGTERM enviado para cada PID vivo
        import signal
        assert mock_kill.call_count >= 1
        for call_args in mock_kill.call_args_list:
            assert call_args[0][1] == signal.SIGTERM

    def test_stop_no_pids_returns_0(self, tmp_path: Path) -> None:
        from rlm.cli import service as svc

        ghost_api = tmp_path / "api.pid"
        ghost_ws  = tmp_path / "ws.pid"
        # arquivos inexistentes → _read_pid retorna None

        with (
            patch("rlm.cli.service._PID_API", ghost_api),
            patch("rlm.cli.service._PID_WS",  ghost_ws),
        ):
            rc = svc.stop_services()

        assert rc == 0


class TestShowStatus:
    """show_status não lança exceções."""

    def test_show_status_no_process(self, tmp_path: Path) -> None:
        from rlm.cli import service as svc

        ghost = tmp_path / "x.pid"  # não existe

        with (
            patch("rlm.cli.service._PID_API", ghost),
            patch("rlm.cli.service._PID_WS",  ghost),
            patch("pathlib.Path.cwd", return_value=tmp_path),
        ):
            rc = svc.show_status()

        assert rc == 0

    def test_show_status_json_emits_structured_snapshot(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from rlm.cli.context import CliContext
        from rlm.cli import service as svc

        context = CliContext(env={}, cwd=tmp_path, home=tmp_path)
        with patch("rlm.cli.service.port_accepting_connections", return_value=False):
            rc = svc.show_status(context=context, json_output=True)

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "status"
        assert payload["schema_version"] == 1
        assert payload["payload"]["launcher_state"]["classification"] == "no-state"
        assert payload["runtime"]["api"]["running"] is False
        assert payload["runtime"]["ws"]["running"] is False
        assert payload["launcher_state"]["classification"] == "no-state"
        assert payload["launcher_state"]["severity"] == "info"


class TestUpdateInstallation:
    def test_fails_without_git_checkout(self, tmp_path: Path) -> None:
        from rlm.cli import service as svc

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            rc = svc.update_installation()

        assert rc == 1

    def test_check_only_reports_pending_commits(self, tmp_project: Path) -> None:
        from rlm.cli import service as svc

        (tmp_project / ".git").mkdir()

        def fake_run(cmd, cwd=None, capture_output=False, text=False, timeout=None):
            joined = " ".join(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if "status --porcelain" in joined:
                result.stdout = ""
            elif "rev-parse --abbrev-ref HEAD" in joined:
                result.stdout = "main\n"
            elif "fetch origin main --quiet" in joined:
                result.stdout = ""
            elif "rev-list --left-right --count HEAD...origin/main" in joined:
                result.stdout = "0 3\n"
            else:
                raise AssertionError(f"Comando inesperado: {joined}")
            return result

        with (
            patch("pathlib.Path.cwd", return_value=tmp_project),
            patch("shutil.which", side_effect=lambda name: f"C:/fake/{name}.exe"),
            patch("subprocess.run", side_effect=fake_run) as mock_run,
        ):
            rc = svc.update_installation(check_only=True)

        assert rc == 0
        assert mock_run.call_count == 4

    def test_update_runs_pull_sync_and_restart(self, tmp_project: Path) -> None:
        from rlm.cli import service as svc

        (tmp_project / ".git").mkdir()

        def fake_run(cmd, cwd=None, capture_output=False, text=False, timeout=None):
            joined = " ".join(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if "status --porcelain" in joined:
                result.stdout = ""
            elif "rev-parse --abbrev-ref HEAD" in joined:
                result.stdout = "main\n"
            elif "fetch origin main --quiet" in joined:
                result.stdout = ""
            elif "rev-list --left-right --count HEAD...origin/main" in joined:
                result.stdout = "0 2\n"
            elif "pull --ff-only origin main" in joined:
                result.stdout = "Updating abc..def"
            elif joined == "uv sync":
                result.stdout = "Synced"
            else:
                raise AssertionError(f"Comando inesperado: {joined}")
            return result

        with (
            patch("pathlib.Path.cwd", return_value=tmp_project),
            patch("shutil.which", side_effect=lambda name: f"C:/fake/{name}.exe"),
            patch("subprocess.run", side_effect=fake_run),
            patch("rlm.cli.service._services_are_running", return_value=True),
            patch("rlm.cli.service.stop_services", return_value=0) as mock_stop,
            patch("rlm.cli.service.start_services", return_value=0) as mock_start,
        ):
            rc = svc.update_installation()

        assert rc == 0
        mock_stop.assert_called_once()
        mock_start.assert_called_once()
        assert mock_start.call_args.kwargs["foreground"] is False
        assert "context" in mock_start.call_args.kwargs

    def test_show_status_with_env_file(self, tmp_path: Path) -> None:
        from rlm.cli import service as svc

        env_file = tmp_path / ".env"
        env_file.write_text(
            "RLM_API_HOST=127.0.0.1\nRLM_API_PORT=5000\n"
            "RLM_WS_HOST=127.0.0.1\nRLM_WS_PORT=8765\n"
        )
        ghost = tmp_path / "x.pid"

        with (
            patch("rlm.cli.service._PID_API", ghost),
            patch("rlm.cli.service._PID_WS",  ghost),
            patch("pathlib.Path.cwd", return_value=tmp_path),
        ):
            rc = svc.show_status()

        assert rc == 0


class TestLauncherState:
    def test_start_persists_launcher_state(self, tmp_path: Path) -> None:
        from rlm.cli.context import CliContext
        from rlm.cli import service as svc

        fake_proc = MagicMock()
        fake_proc.pid = 2468
        fake_proc.poll.return_value = None
        context = CliContext(env={}, cwd=tmp_path, home=tmp_path)

        with (
            patch("rlm.cli.service._PID_DIR", tmp_path / "run"),
            patch("rlm.cli.service._PID_API", tmp_path / "run" / "api.pid"),
            patch("rlm.cli.service._PID_WS", tmp_path / "run" / "ws.pid"),
            patch("rlm.cli.service._LOG_DIR", tmp_path / "logs"),
            patch("subprocess.Popen", return_value=fake_proc),
            patch("builtins.open", MagicMock()),
            patch("time.sleep"),
        ):
            rc = svc.start_services(foreground=False, api_only=False, ws_only=False, context=context)

        assert rc == 0
        state_path = context.paths.launcher_state_path
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        assert payload["last_known_status"] == "running"
        assert payload["last_launch_mode"] == "background-combined"
        assert payload["last_valid_bootstrap"]["api_enabled"] is True
        assert payload["runtime_artifacts"]["api_pid_file"].endswith("api.pid")

    def test_stop_updates_launcher_state(self, tmp_path: Path) -> None:
        from rlm.cli.context import CliContext
        from rlm.cli import service as svc

        context = CliContext(env={}, cwd=tmp_path, home=tmp_path)
        context.paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        svc._write_pid(context.paths.runtime_dir / "api.pid", 1111)
        svc._write_pid(context.paths.runtime_dir / "ws.pid", 1111)

        with (
            patch("rlm.cli.service._PID_API", context.paths.runtime_dir / "api.pid"),
            patch("rlm.cli.service._PID_WS", context.paths.runtime_dir / "ws.pid"),
            patch("rlm.cli.service._pid_alive", return_value=True),
            patch("os.kill"),
        ):
            rc = svc.stop_services(context=context)

        assert rc == 0
        payload = json.loads(context.paths.launcher_state_path.read_text(encoding="utf-8"))
        assert payload["last_known_status"] == "stopped"
        assert payload["last_operation"] == "stop"

    def test_install_systemd_persists_daemon_artifact(self, tmp_path: Path) -> None:
        from rlm.cli.context import CliContext
        from rlm.cli import service as svc

        project_root = tmp_path / "project"
        project_root.mkdir()
        env_path = project_root / ".env"
        env_path.write_text("RLM_API_PORT=5000\n")
        context = CliContext(env={}, cwd=project_root, home=tmp_path)

        def fake_run(cmd, capture_output=False):
            result = MagicMock()
            result.returncode = 0
            result.stderr = b""
            return result

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("rlm.cli.service.CliContext.from_environment", return_value=context),
            patch("subprocess.run", side_effect=fake_run),
        ):
            rc = svc.install_systemd_service(project_root=project_root, env_path=env_path)

        assert rc == 0
        payload = json.loads(context.paths.launcher_state_path.read_text(encoding="utf-8"))
        assert payload["runtime_artifacts"]["daemon_manager"] == "systemd"
        assert payload["runtime_artifacts"]["daemon_definition"].endswith("rlm.service")

    def test_show_status_syncs_launcher_state(self, tmp_path: Path) -> None:
        from rlm.cli.context import CliContext
        from rlm.cli import service as svc

        context = CliContext(env={}, cwd=tmp_path, home=tmp_path)
        api_pid = tmp_path / "api.pid"
        ws_pid = tmp_path / "ws.pid"
        api_pid.write_text("3333")
        ws_pid.write_text("3333")

        with (
            patch("rlm.cli.service._PID_API", api_pid),
            patch("rlm.cli.service._PID_WS", ws_pid),
            patch("rlm.cli.service._pid_alive", return_value=True),
        ):
            rc = svc.show_status(context=context)

        assert rc == 0
        payload = json.loads(context.paths.launcher_state_path.read_text(encoding="utf-8"))
        assert payload["last_known_status"] == "running"


class TestWireGuard:
    def test_add_wireguard_peer_impl_returns_1_when_conf_missing(self, tmp_path: Path) -> None:
        from rlm.cli.service_wireguard import add_wireguard_peer_impl

        seen: list[str] = []
        rc = add_wireguard_peer_impl(
            "laptop",
            "pubkey1234567890",
            "10.0.0.2",
            wg_conf=tmp_path / "missing.conf",
            os_geteuid=lambda: 0,
            ok=seen.append,
            warn=seen.append,
            err=seen.append,
            info=seen.append,
        )

        assert rc == 1
        assert any("wg0.conf não encontrado" in message for message in seen)

    def test_add_wireguard_peer_impl_skips_duplicate_key(self, tmp_path: Path) -> None:
        from rlm.cli.service_wireguard import add_wireguard_peer_impl

        wg_conf = tmp_path / "wg0.conf"
        wg_conf.write_text("[Peer]\nPublicKey = duplicated-key\n")
        seen: list[str] = []

        rc = add_wireguard_peer_impl(
            "laptop",
            "duplicated-key",
            "10.0.0.2",
            wg_conf=wg_conf,
            os_geteuid=lambda: 0,
            ok=seen.append,
            warn=seen.append,
            err=seen.append,
            info=seen.append,
        )

        assert rc == 0
        assert any("já existe" in message for message in seen)


# =========================================================================== #
# 4. Smoke test: importação dos 3 módulos                                      #
# =========================================================================== #

class TestImports:
    """Garante que os módulos importam sem erro."""

    def test_import_main(self) -> None:
        import rlm.cli.main  # noqa: F401

    def test_import_wizard(self) -> None:
        import rlm.cli.wizard  # noqa: F401

    def test_import_service(self) -> None:
        import rlm.cli.service  # noqa: F401
