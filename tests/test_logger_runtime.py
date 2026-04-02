import importlib.util
import importlib
import json
import sys
from pathlib import Path

from rlm import logging as rlm_logging
from rlm.core.types import RLMIteration, RLMMetadata
from rlm.core.structured_log import RuntimeLogger, get_logger, get_runtime_logger
from rlm.logger.rlm_logger import RLMLogger


def test_rlm_logger_writes_utf8_jsonl(tmp_path):
    logger = RLMLogger(str(tmp_path), file_name="teste")
    metadata = RLMMetadata(
        root_model="modelo",
        max_depth=1,
        max_iterations=3,
        backend="openai",
        backend_kwargs={"emoji": "ação"},
        environment_type="local",
        environment_kwargs={},
        other_backends=None,
    )
    iteration = RLMIteration(
        prompt="olá",
        response="resposta com çã",
        code_blocks=[],
        final_answer="fim",
        iteration_time=0.1,
    )

    logger.log_metadata(metadata)
    logger.log(iteration)

    lines = Path(logger.log_file_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "metadata"
    assert json.loads(lines[1])["response"] == "resposta com çã"


def test_verbose_module_imports_without_rich_dependency(tmp_path, monkeypatch):
    module_path = Path("c:/Users/demet/Desktop/agente proativo/RLM_OpenClaw_Engine/rlm-main/rlm/logger/verbose.py")
    spec = importlib.util.spec_from_file_location("verbose_no_rich_test", module_path)
    assert spec is not None
    assert spec.loader is not None

    original_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("rich"):
            raise ImportError("rich not installed")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    printer = module.VerbosePrinter(enabled=True)
    printer.print_final_answer("ok")

    assert module._RICH_AVAILABLE is False


def test_runtime_logger_alias_matches_legacy_structured_logger_api(capsys):
    legacy = get_logger("scheduler")
    explicit = get_runtime_logger("scheduler")

    assert isinstance(legacy, RuntimeLogger)
    assert isinstance(explicit, RuntimeLogger)

    explicit.info("tick", cycle=1)
    captured = capsys.readouterr()
    assert "tick" in captured.err
    assert "scheduler" in captured.err


def test_logging_facade_exposes_distinct_log_responsibilities():
    assert rlm_logging.RuntimeLogger is not None
    assert rlm_logging.TrajectoryLogger is RLMLogger
    assert rlm_logging.VerbosePrinter is not None
    runtime = rlm_logging.get_runtime_logger("scheduler")
    assert isinstance(runtime, RuntimeLogger)


def test_operational_modules_use_runtime_logger():
    modules_and_attrs = [
        ("rlm.core.engine.lm_handler", "logger"),
        ("rlm.core.lifecycle.shutdown", "log"),
        ("rlm.core.lifecycle.disposable", "log"),
        ("rlm.server.slack_gateway", "log"),
        ("rlm.server.discord_gateway", "log"),
        ("rlm.server.whatsapp_gateway", "log"),
        ("rlm.server.webchat", "log"),
        ("rlm.server.telegram_gateway", "logger"),
        ("rlm.server.event_router", "log"),
    ]

    for module_name, attr_name in modules_and_attrs:
        module = importlib.import_module(module_name)
        assert isinstance(getattr(module, attr_name), RuntimeLogger)