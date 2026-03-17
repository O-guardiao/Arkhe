from __future__ import annotations

from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from rlm.core.handoff import HandoffRecord
from rlm.core.role_orchestrator import PENDING_HANDOFFS_KEY, orchestrate_roles, pop_pending_handoffs
from rlm.core.skill_loader import SkillDef, SkillPromptPlan


def _make_plan() -> SkillPromptPlan:
    shell = SkillDef(name="shell", description="terminal")
    shell.runtime.risk_level = "high"
    shell.runtime.fallback_policy = "ask_user_or_use_filesystem"
    shell.runtime.postconditions = ["logs_checked"]
    shell.quality.historical_reliability = 0.8
    shell.quality.last_30d_utility = 0.7
    return SkillPromptPlan(effective_mode="focused", expanded_skills=[shell], matched_skills=[shell])


def test_pop_pending_handoffs_limpa_fila():
    repl_locals = {
        PENDING_HANDOFFS_KEY: [
            {
                "target_role": "worker",
                "reason": "triagem concluída",
                "remaining_goal": "executar deploy",
                "summary": "n/a",
                "attempted_skills": [],
                "failures": [],
                "suggested_mode": "focused",
                "timestamp": "2026-03-11T00:00:00+00:00",
            }
        ]
    }
    handoffs = pop_pending_handoffs(repl_locals)
    assert len(handoffs) == 1
    assert repl_locals[PENDING_HANDOFFS_KEY] == []


def test_worker_handoff_executa_sub_rlm():
    plan = _make_plan()
    repl_locals = {
        PENDING_HANDOFFS_KEY: [
            HandoffRecord(
                target_role="worker",
                reason="triagem concluída",
                remaining_goal="executar deploy",
            ).to_payload()
        ]
    }
    fake_sub = MagicMock(return_value="deploy executado")
    with patch("rlm.core.role_orchestrator.make_sub_rlm_fn", return_value=fake_sub):
        outcome = orchestrate_roles(
            rlm=MagicMock(),
            prompt="faça deploy",
            response="",
            prompt_plan=plan,
            repl_locals=repl_locals,
            log_event=lambda *_args, **_kwargs: None,
            session_id="sess-1",
        )
    assert outcome.response == "deploy executado"
    assert outcome.steps[0]["role"] == "worker"


def test_evaluator_retry_refaz_resposta():
    plan = _make_plan()
    repl_locals = {
        PENDING_HANDOFFS_KEY: [
            HandoffRecord(
                target_role="evaluator",
                reason="validar saída",
                remaining_goal="garantir postconditions",
            ).to_payload()
        ]
    }
    fake_sub = MagicMock(side_effect=[
        '{"action": "retry", "retry_prompt": "refaça usando fallback"}',
        "resposta refeita",
    ])
    with patch("rlm.core.role_orchestrator.make_sub_rlm_fn", return_value=fake_sub):
        outcome = orchestrate_roles(
            rlm=MagicMock(),
            prompt="colete logs",
            response="timeout",
            prompt_plan=plan,
            repl_locals=repl_locals,
            log_event=lambda *_args, **_kwargs: None,
            session_id="sess-1",
        )
    assert outcome.retried is True
    assert outcome.response == "resposta refeita"


def test_evaluator_retry_reusa_task_id_do_handoff():
    plan = _make_plan()
    repl_locals = {
        PENDING_HANDOFFS_KEY: [
            HandoffRecord(
                target_role="evaluator",
                reason="validar saída",
                remaining_goal="garantir postconditions",
                task_id=77,
                parent_task_id=12,
            ).to_payload()
        ]
    }
    fake_sub = MagicMock(side_effect=[
        '{"action": "retry", "retry_prompt": "refaça usando fallback"}',
        "resposta refeita",
    ])
    update_runtime_task = MagicMock()
    fake_rlm = SimpleNamespace(
        _persistent_env=SimpleNamespace(update_runtime_task=update_runtime_task)
    )

    with patch("rlm.core.role_orchestrator.make_sub_rlm_fn", return_value=fake_sub):
        outcome = orchestrate_roles(
            rlm=fake_rlm,
            prompt="colete logs",
            response="timeout",
            prompt_plan=plan,
            repl_locals=repl_locals,
            log_event=lambda *_args, **_kwargs: None,
            session_id="sess-1",
        )

    assert outcome.retried is True
    assert fake_sub.call_args_list[1].kwargs["_task_id"] == 77
    assert update_runtime_task.call_args_list[0].args[0] == 77
    assert update_runtime_task.call_args_list[0].kwargs["status"] == "in-progress"
    assert update_runtime_task.call_args_list[-1].args[0] == 77
    assert update_runtime_task.call_args_list[-1].kwargs["status"] == "completed"


def test_auto_eval_escalate_sem_handoff():
    plan = _make_plan()
    fake_sub = MagicMock(return_value='{"action": "escalate", "rationale": "risco alto", "escalation_target": "human"}')
    with patch("rlm.core.role_orchestrator.make_sub_rlm_fn", return_value=fake_sub):
        outcome = orchestrate_roles(
            rlm=MagicMock(),
            prompt="executar ação sensível",
            response="não foi possível concluir",
            prompt_plan=plan,
            repl_locals={},
            log_event=lambda *_args, **_kwargs: None,
            session_id="sess-1",
        )
    assert outcome.escalated is True
    assert any(step["role"] == "evaluator" for step in outcome.steps)