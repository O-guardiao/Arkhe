from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rlm.core.orchestration.supervisor import ExecutionResult, SupervisorConfig
from rlm.runtime import RuntimeThreatReport
from rlm.server.runtime_pipeline import RuntimeDispatchServices, dispatch_runtime_prompt_sync


def _fake_prompt_plan(*, expanded_skills=None):
    return SimpleNamespace(
        effective_mode="auto",
        expanded_skills=list(expanded_skills or []),
        ranked_skills=[],
        blocked_skills=[],
    )


def test_infer_runtime_execution_policy_detects_simple_inspection(monkeypatch):
    from rlm.core.security.execution_policy import infer_runtime_execution_policy

    monkeypatch.setenv("RLM_MODEL_FAST", "gpt-5.4-nano")
    plan = _fake_prompt_plan(expanded_skills=[SimpleNamespace(name="filesystem")])

    policy = infer_runtime_execution_policy(
        "quais os diretorios de memoria e quantas sessoes sao unificadas? faca verificacoes",
        client_id="tui:default",
        prompt_plan=plan,
        default_model="gpt-5.4",
    )

    assert policy.task_class == "simple_inspect"
    assert policy.allow_recursion is False
    assert policy.allow_role_orchestrator is False
    assert policy.max_iterations_override == 3
    assert policy.root_model_override == "gpt-5.4-nano"


def test_infer_runtime_execution_policy_keeps_complex_task_recursive():
    from rlm.core.security.execution_policy import infer_runtime_execution_policy

    policy = infer_runtime_execution_policy(
        "implemente uma policy de roteamento com subagentes e compare tres arquiteturas",
        client_id="tui:default",
        prompt_plan=_fake_prompt_plan(),
        default_model="gpt-5.4",
    )

    assert policy.task_class == "default"
    assert policy.allow_recursion is True
    assert policy.allow_role_orchestrator is True
    assert policy.root_model_override is None


def test_parse_price_table_and_estimate_cost():
    from rlm.core.security.execution_policy import CostSlice, estimate_architecture_cost, parse_price_table

    prices = parse_price_table(
        """
Model/ Input/ Cached input/ Output/
gpt-5.4/ $2.50/ $0.25/ $15.00/
gpt-5.4-mini/ $0.75/ $0.075/ $4.50/
"""
    )

    total = estimate_architecture_cost(
        prices,
        [
            CostSlice("planner", "gpt-5.4", input_tokens=1000, output_tokens=200),
            CostSlice("worker", "gpt-5.4-mini", input_tokens=2000, output_tokens=100, calls=2),
        ],
    )

    assert "gpt-5.4" in prices
    assert "gpt-5.4-mini" in prices
    assert total > 0


def test_build_policy_decision_input_exports_resolved_models(monkeypatch):
    from rlm.core.security.execution_policy import build_policy_decision_input

    for env_name in (
        "RLM_MODEL_PLANNER",
        "RLM_MODEL",
        "RLM_MODEL_WORKER",
        "RLM_SUBAGENT_MODEL",
        "RLM_MODEL_EVALUATOR",
        "RLM_FAST_MODEL",
        "RLM_MODEL_MINIREPL",
        "RLM_MINIREPL_MODEL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("RLM_MODEL_FAST", "gpt-5.4-nano")
    plan = _fake_prompt_plan(expanded_skills=[SimpleNamespace(name="filesystem")])

    payload = build_policy_decision_input(
        "quais os diretorios de memoria e quantas sessoes sao unificadas? faca verificacoes",
        client_id="tui:default",
        prompt_plan=plan,
        default_model="gpt-5.4",
    )

    assert payload["policy_version"] == 1
    assert payload["expanded_skills"] == ["filesystem"]
    assert payload["fast_model"] == "gpt-5.4-nano"
    assert payload["planner_model"] == "gpt-5.4"


def test_subprocess_runtime_policy_port_uses_native_response():
    from rlm.runtime.native_policy_adapter import SubprocessRuntimePolicyPort
    from rlm.runtime.python_runtime_guard import PythonRuntimePolicyPort

    port = SubprocessRuntimePolicyPort(
        binary_path=Path("C:/fake/arkhe-policy-core.exe"),
        timeout_s=0.2,
        fallback=PythonRuntimePolicyPort(),
    )

    with patch("rlm.runtime.native_policy_adapter.subprocess.run") as run:
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "policy_version": 1,
                    "task_class": "simple_inspect",
                    "allow_recursion": False,
                    "allow_role_orchestrator": False,
                    "max_iterations_override": 3,
                    "root_model_override": "gpt-5.4-nano",
                    "note": "native bridge",
                }
            ),
            stderr="",
        )

        policy = port.infer_runtime_execution_policy(
            "quais os diretorios de memoria e quantas sessoes sao unificadas? faca verificacoes",
            client_id="tui:default",
            prompt_plan=_fake_prompt_plan(expanded_skills=[SimpleNamespace(name="filesystem")]),
            default_model="gpt-5.4",
        )

    assert policy.task_class == "simple_inspect"
    assert policy.allow_recursion is False
    assert policy.root_model_override == "gpt-5.4-nano"
    run.assert_called_once()


def test_subprocess_runtime_policy_port_falls_back_to_python_policy_on_failure(monkeypatch):
    from rlm.runtime.native_policy_adapter import SubprocessRuntimePolicyPort
    from rlm.runtime.python_runtime_guard import PythonRuntimePolicyPort

    monkeypatch.setenv("RLM_MODEL_FAST", "gpt-5.4-nano")
    port = SubprocessRuntimePolicyPort(
        binary_path=Path("C:/fake/arkhe-policy-core.exe"),
        timeout_s=0.2,
        fallback=PythonRuntimePolicyPort(),
    )

    with patch(
        "rlm.runtime.native_policy_adapter.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="native-policy", timeout=0.2),
    ):
        policy = port.infer_runtime_execution_policy(
            "quais os diretorios de memoria e quantas sessoes sao unificadas? faca verificacoes",
            client_id="tui:default",
            prompt_plan=_fake_prompt_plan(expanded_skills=[SimpleNamespace(name="filesystem")]),
            default_model="gpt-5.4",
        )

    assert policy.task_class == "simple_inspect"
    assert policy.allow_recursion is False


def test_build_runtime_guard_from_env_can_enable_native_policy(monkeypatch):
    from rlm.runtime.native_policy_adapter import SubprocessRuntimePolicyPort
    from rlm.runtime.python_runtime_guard import build_runtime_guard_from_env

    monkeypatch.setenv("RLM_NATIVE_POLICY_MODE", "native")
    monkeypatch.setenv("RLM_NATIVE_POLICY_BIN", "C:/fake/arkhe-policy-core.exe")

    with patch("rlm.runtime.native_policy_adapter.Path.exists", return_value=True):
        runtime_guard = build_runtime_guard_from_env()

    assert isinstance(runtime_guard.policy, SubprocessRuntimePolicyPort)


def test_sub_rlm_model_override_propagates_to_child_backend_kwargs():
    from rlm.core.engine.sub_rlm import make_sub_rlm_fn

    parent = MagicMock()
    parent.depth = 0
    parent.max_depth = 3
    parent.backend = "openai"
    parent.backend_kwargs = {"model_name": "gpt-5.4"}
    parent.environment_type = "local"
    parent.environment_kwargs = {}
    parent.other_backend_kwargs = None

    mock_completion = MagicMock()
    mock_completion.response = "ok"
    mock_instance = MagicMock()
    mock_instance.completion.return_value = mock_completion
    mock_cls = MagicMock(return_value=mock_instance)

    sub_rlm = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
    sub_rlm("verifique algo", model="gpt-5.4-mini")

    assert mock_cls.call_args.kwargs["backend_kwargs"]["model_name"] == "gpt-5.4-mini"


def test_runtime_policy_disables_recursive_scaffolds_in_repl_globals():
    from rlm.core.security.execution_policy import RuntimeExecutionPolicy
    from rlm.core.engine.rlm import RLM

    rlm = RLM(backend="openai", backend_kwargs={"model_name": "gpt-5.4"}, environment="local")
    rlm._runtime_execution_policy = RuntimeExecutionPolicy(
        task_class="simple_inspect",
        allow_recursion=False,
        allow_role_orchestrator=False,
        note="simple local verification path",
    )
    env = SimpleNamespace(globals={})

    rlm._inject_repl_globals(MagicMock(), env)

    try:
        env.globals["sub_rlm"]("tarefa")
    except RuntimeError as exc:
        assert "disabled by execution policy" in str(exc)
    else:
        raise AssertionError("sub_rlm deveria estar bloqueado pela policy")


def test_dispatch_runtime_prompt_sync_applies_fast_policy_and_root_model_override(monkeypatch):
    monkeypatch.setenv("RLM_MODEL_FAST", "gpt-5.4-nano")

    skill_loader = MagicMock()
    skill_loader.plan_prompt_context.return_value = _fake_prompt_plan(
        expanded_skills=[SimpleNamespace(name="filesystem", runtime=SimpleNamespace(risk_level="low"))]
    )
    skill_loader.build_system_prompt_context.return_value = ""
    skill_loader.estimate_tokens.return_value = {"total": 1}
    skill_loader.set_request_context.return_value = object()
    skill_loader.clear_request_context.return_value = None

    session_manager = MagicMock()
    session_manager.log_operation.return_value = 1

    core = SimpleNamespace(
        backend_kwargs={"model_name": "gpt-5.4"},
        _persistent_lm_handler=None,
        _runtime_execution_policy=None,
    )
    rlm_session = SimpleNamespace(_rlm=core, _persistent_env=None, skills_context="")
    session = SimpleNamespace(
        session_id="sess-1",
        delivery_context={},
        rlm_instance=rlm_session,
    )

    def _execute_side_effect(session_obj, prompt, config=None, root_prompt=None):
        assert session_obj.rlm_instance._rlm.backend_kwargs["model_name"] == "gpt-5.4-nano"
        assert isinstance(config, SupervisorConfig)
        assert config.max_iterations_override == 3
        return ExecutionResult(
            session_id="sess-1",
            status="completed",
            response="ok",
            execution_time=0.12,
        )

    supervisor = MagicMock()
    supervisor.default_config = SupervisorConfig()
    supervisor.execute.side_effect = _execute_side_effect

    services = RuntimeDispatchServices(
        session_manager=session_manager,
        supervisor=supervisor,
        plugin_loader=MagicMock(),
        event_router=MagicMock(),
        hooks=MagicMock(),
        skill_loader=skill_loader,
        eligible_skills=[],
        skill_context="",
    )
    services.event_router.route.return_value = (
        "quais os diretorios de memoria e quantas sessoes sao unificadas? faca verificacoes",
        [],
    )

    with patch("rlm.server.runtime_pipeline.orchestrate_roles") as orchestrate_roles:
        result = dispatch_runtime_prompt_sync(
            services,
            "tui:default",
            {"text": "quais os diretorios de memoria e quantas sessoes sao unificadas? faca verificacoes"},
            session=session,
        )

    assert result["status"] == "completed"
    assert session.rlm_instance._rlm.backend_kwargs["model_name"] == "gpt-5.4"
    orchestrate_roles.assert_not_called()


def test_build_runtime_guard_from_env_reads_exec_approval_settings(monkeypatch):
    from rlm.runtime.python_runtime_guard import build_runtime_guard_from_env

    monkeypatch.setenv("RLM_EXEC_APPROVAL_REQUIRED", "true")
    monkeypatch.setenv("RLM_EXEC_APPROVAL_TIMEOUT", "75")

    runtime_guard = build_runtime_guard_from_env()

    assert runtime_guard.exec_approval_required is True
    assert runtime_guard.approvals.stats()["default_timeout_s"] == 75


def test_dispatch_runtime_prompt_sync_prefers_runtime_guard_ports(monkeypatch):
    policy = SimpleNamespace(
        infer_runtime_execution_policy=MagicMock(
            return_value=SimpleNamespace(
                task_class="simple_inspect",
                allow_recursion=False,
                allow_role_orchestrator=False,
                max_iterations_override=3,
                root_model_override="gpt-5.4-nano",
                note="runtime guard",
            )
        )
    )
    security = SimpleNamespace(
        audit_input=MagicMock(return_value=RuntimeThreatReport())
    )
    runtime_guard = SimpleNamespace(
        policy=policy,
        security=security,
        approvals=MagicMock(),
        vaults=SimpleNamespace(get_tools=MagicMock(return_value={})),
        exec_approval_required=False,
    )

    skill_loader = MagicMock()
    skill_loader.plan_prompt_context.return_value = _fake_prompt_plan(
        expanded_skills=[SimpleNamespace(name="filesystem", runtime=SimpleNamespace(risk_level="low"))]
    )
    skill_loader.build_system_prompt_context.return_value = ""
    skill_loader.estimate_tokens.return_value = {"total": 1}
    skill_loader.set_request_context.return_value = object()
    skill_loader.clear_request_context.return_value = None

    session_manager = MagicMock()
    session_manager.log_operation.return_value = 1

    core = SimpleNamespace(
        backend_kwargs={"model_name": "gpt-5.4"},
        _persistent_lm_handler=None,
        _runtime_execution_policy=None,
    )
    rlm_session = SimpleNamespace(_rlm=core, _persistent_env=None, skills_context="")
    session = SimpleNamespace(
        session_id="sess-1",
        delivery_context={},
        rlm_instance=rlm_session,
    )

    def _execute_side_effect(session_obj, prompt, config=None, root_prompt=None):
        assert session_obj.rlm_instance._rlm.backend_kwargs["model_name"] == "gpt-5.4-nano"
        assert isinstance(config, SupervisorConfig)
        assert config.max_iterations_override == 3
        return ExecutionResult(
            session_id="sess-1",
            status="completed",
            response="ok",
            execution_time=0.12,
        )

    supervisor = MagicMock()
    supervisor.default_config = SupervisorConfig()
    supervisor.execute.side_effect = _execute_side_effect

    services = RuntimeDispatchServices(
        session_manager=session_manager,
        supervisor=supervisor,
        plugin_loader=MagicMock(),
        event_router=MagicMock(),
        hooks=MagicMock(),
        skill_loader=skill_loader,
        runtime_guard=runtime_guard,
        eligible_skills=[],
        skill_context="",
    )
    services.event_router.route.return_value = (
        "quais os diretorios de memoria e quantas sessoes sao unificadas? faca verificacoes",
        [],
    )

    with patch("rlm.server.runtime_pipeline.orchestrate_roles") as orchestrate_roles:
        result = dispatch_runtime_prompt_sync(
            services,
            "tui:default",
            {"text": "quais os diretorios de memoria e quantas sessoes sao unificadas? faca verificacoes"},
            session=session,
        )

    assert result["status"] == "completed"
    assert session.rlm_instance._rlm.backend_kwargs["model_name"] == "gpt-5.4"
    policy.infer_runtime_execution_policy.assert_called_once()
    security.audit_input.assert_called_once()
    orchestrate_roles.assert_not_called()