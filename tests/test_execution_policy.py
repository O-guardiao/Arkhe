from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rlm.core.supervisor import ExecutionResult, SupervisorConfig
from rlm.server.runtime_pipeline import RuntimeDispatchServices, dispatch_runtime_prompt_sync


def _fake_prompt_plan(*, expanded_skills=None):
    return SimpleNamespace(
        effective_mode="auto",
        expanded_skills=list(expanded_skills or []),
        ranked_skills=[],
        blocked_skills=[],
    )


def test_infer_runtime_execution_policy_detects_simple_inspection(monkeypatch):
    from rlm.core.execution_policy import infer_runtime_execution_policy

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
    from rlm.core.execution_policy import infer_runtime_execution_policy

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
    from rlm.core.execution_policy import CostSlice, estimate_architecture_cost, parse_price_table

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


def test_sub_rlm_model_override_propagates_to_child_backend_kwargs():
    from rlm.core.sub_rlm import make_sub_rlm_fn

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
    from rlm.core.execution_policy import RuntimeExecutionPolicy
    from rlm.core.rlm import RLM

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