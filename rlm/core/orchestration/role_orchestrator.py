from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from rlm.core.orchestration.handoff import HandoffRecord
from rlm.core.engine.sub_rlm import make_sub_rlm_fn

PENDING_HANDOFFS_KEY = "__rlm_pending_handoffs__"


@dataclass
class RoleExecutionOutcome:
    response: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    escalated: bool = False
    retried: bool = False


def pop_pending_handoffs(repl_locals: dict[str, Any] | None) -> list[HandoffRecord]:
    if repl_locals is None:
        return []
    raw_items = repl_locals.get(PENDING_HANDOFFS_KEY, [])
    repl_locals[PENDING_HANDOFFS_KEY] = []
    if not isinstance(raw_items, list):
        return []
    records: list[HandoffRecord] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            records.append(HandoffRecord(**item))
        except Exception:
            continue
    return records


def should_auto_evaluate(response: str, prompt_plan: Any) -> bool:
    text = (response or "").strip().lower()
    if not text:
        return True
    if any(marker in text for marker in ("não foi possível", "error", "falha", "timeout", "indispon")):
        return True
    if getattr(prompt_plan, "blocked_skills", None):
        return True
    return any(
        getattr(skill.runtime, "risk_level", "") == "high"
        for skill in (getattr(prompt_plan, "expanded_skills", []) or [])
    )


def orchestrate_roles(
    *,
    rlm: Any,
    prompt: str,
    response: str,
    prompt_plan: Any,
    repl_locals: dict[str, Any] | None,
    log_event: Callable[[str, str, dict[str, Any] | None], None],
    session_id: str,
    hooks: Any | None = None,
    max_retries: int = 1,
) -> RoleExecutionOutcome:
    outcome = RoleExecutionOutcome(response=response or "")
    pending = pop_pending_handoffs(repl_locals)
    auto_eval = should_auto_evaluate(outcome.response, prompt_plan)
    if not pending and not auto_eval:
        return outcome

    sub_rlm = make_sub_rlm_fn(rlm)
    skill_snapshot = _build_skill_snapshot(prompt_plan)
    current_response = outcome.response
    retries_left = max(0, int(max_retries))

    for handoff in pending:
        payload = handoff.to_payload()
        log_event(session_id, "agent_role_execution", {"phase": "handoff", **payload})
        if hooks is not None:
            hooks.trigger("agent.role_execution", session_id=session_id, context=payload)
        if handoff.target_role in {"worker", "micro"}:
            current_response = _run_worker(rlm, sub_rlm, handoff, prompt, current_response, skill_snapshot)
            outcome.steps.append({"role": handoff.target_role, "action": "executed"})
            continue
        if handoff.target_role == "evaluator":
            decision = _run_evaluator(sub_rlm, prompt, current_response, skill_snapshot)
            current_response, did_retry, escalated = _apply_evaluator_decision(
                rlm=rlm,
                handoff=handoff,
                sub_rlm=sub_rlm,
                decision=decision,
                prompt=prompt,
                response=current_response,
                skill_snapshot=skill_snapshot,
                retries_left=retries_left,
            )
            if did_retry:
                retries_left -= 1
                outcome.retried = True
            outcome.escalated = outcome.escalated or escalated
            _update_handoff_task(rlm, handoff, current_response)
            outcome.steps.append({"role": "evaluator", "action": decision.get("action", "accept")})
            continue
        if handoff.target_role == "human":
            outcome.escalated = True
            current_response = _build_human_escalation(handoff, current_response, skill_snapshot)
            _update_handoff_task(rlm, handoff, current_response)
            outcome.steps.append({"role": "human", "action": "escalated"})

    if auto_eval and not any(step.get("role") == "evaluator" for step in outcome.steps):
        decision = _run_evaluator(sub_rlm, prompt, current_response, skill_snapshot)
        current_response, did_retry, escalated = _apply_evaluator_decision(
            rlm=rlm,
            handoff=None,
            sub_rlm=sub_rlm,
            decision=decision,
            prompt=prompt,
            response=current_response,
            skill_snapshot=skill_snapshot,
            retries_left=retries_left,
        )
        if did_retry:
            outcome.retried = True
        outcome.escalated = outcome.escalated or escalated
        outcome.steps.append({"role": "evaluator", "action": decision.get("action", "accept")})

    outcome.response = current_response
    return outcome


def _run_worker(
    rlm: Any,
    sub_rlm: Callable[..., str],
    handoff: HandoffRecord,
    prompt: str,
    response: str,
    skill_snapshot: str,
) -> str:
    context = (
        "Atue como worker-agent operacional. Complete o objetivo restante com foco em execução segura.\n\n"
        f"Prompt original:\n{prompt.strip()}\n\n"
        f"Resposta atual:\n{response.strip()}\n\n"
        f"Skills relevantes:\n{skill_snapshot}\n"
    )
    task = (
        f"Motivo do handoff: {handoff.reason}\n"
        f"Objetivo restante: {handoff.remaining_goal}\n"
        f"Resumo: {handoff.summary or 'n/a'}\n"
        f"Skills tentadas: {', '.join(handoff.attempted_skills) or 'nenhuma'}\n"
        f"Falhas observadas: {', '.join(handoff.failures) or 'nenhuma'}\n"
        "Entregue a melhor resposta operacional final em texto claro."
    )
    result = sub_rlm(
        task,
        context=context,
        max_iterations=10,
        timeout_s=240,
        model_role="worker",
        _task_id=handoff.task_id,
    )
    _update_handoff_task(rlm, handoff, result)
    return result


def _update_handoff_task(rlm: Any, handoff: HandoffRecord, result: str) -> None:
    if handoff.task_id is None:
        return
    env = getattr(rlm, "_persistent_env", None)
    update_task = getattr(env, "update_runtime_task", None)
    if not callable(update_task):
        return
    status = "cancelled" if str(result).startswith("[CANCELLED]") else "completed"
    try:
        update_task(
            int(handoff.task_id),
            status=status,
            note=str(result)[:200],
            metadata={
                "target_role": handoff.target_role,
                "parent_task_id": handoff.parent_task_id,
            },
        )
    except Exception:
        return


def _run_evaluator(
    sub_rlm: Callable[..., str],
    prompt: str,
    response: str,
    skill_snapshot: str,
) -> dict[str, Any]:
    context = (
        "Atue como evaluator-agent. Valide a resposta operacional e decida entre accept, retry ou escalate.\n"
        "Considere quality, postconditions, risco e fallback policies.\n"
        "Responda SOMENTE JSON com action, rationale, retry_prompt, improved_response e escalation_target.\n\n"
        f"Prompt original:\n{prompt.strip()}\n\n"
        f"Resposta candidata:\n{response.strip()}\n\n"
        f"Skills relevantes:\n{skill_snapshot}\n"
    )
    raw = sub_rlm(
        "Valide a resposta candidata e devolva o JSON solicitado.",
        context=context,
        max_iterations=6,
        timeout_s=180,
        model_role="evaluator",
    )
    return _parse_evaluator_decision(raw)


def _apply_evaluator_decision(
    *,
    rlm: Any,
    handoff: HandoffRecord | None,
    sub_rlm: Callable[..., str],
    decision: dict[str, Any],
    prompt: str,
    response: str,
    skill_snapshot: str,
    retries_left: int,
) -> tuple[str, bool, bool]:
    action = str(decision.get("action", "accept")).strip().lower()
    improved = str(decision.get("improved_response", "")).strip()
    if action == "accept":
        return improved or response, False, False
    if action == "retry" and retries_left > 0:
        retry_prompt = str(decision.get("retry_prompt", "")).strip() or (
            "Refaça a resposta usando as fallback policies e cobrindo explicitamente as postconditions esperadas."
        )
        if handoff is not None and handoff.task_id is not None:
            env = getattr(rlm, "_persistent_env", None)
            update_task = getattr(env, "update_runtime_task", None)
            if callable(update_task):
                try:
                    update_task(
                        int(handoff.task_id),
                        status="in-progress",
                        note=retry_prompt[:200],
                        metadata={
                            "target_role": handoff.target_role,
                            "parent_task_id": handoff.parent_task_id,
                            "retry_requested": True,
                        },
                    )
                except Exception:
                    pass
        retried = sub_rlm(
            retry_prompt,
            context=(
                "Atue como worker-agent em modo de retry orientado por evaluator.\n\n"
                f"Prompt original:\n{prompt.strip()}\n\n"
                f"Resposta anterior:\n{response.strip()}\n\n"
                f"Skills relevantes:\n{skill_snapshot}\n"
            ),
            max_iterations=8,
            timeout_s=180,
            model_role="worker",
            _task_id=handoff.task_id if handoff is not None else None,
        )
        return retried, True, False
    if action == "escalate":
        target = str(decision.get("escalation_target", "human")).strip() or "human"
        return (
            improved
            or f"Escalonamento recomendado para {target}.\n\nResposta candidata:\n{response.strip()}\n\nJustificativa:\n{decision.get('rationale', '')}",
            False,
            True,
        )
    return response, False, False


def _parse_evaluator_decision(raw: str) -> dict[str, Any]:
    cleaned = (raw or "").strip()
    match = re.search(r"```json\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1)
    elif cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
    if cleaned.startswith("{") and cleaned.endswith("}"):
        try:
            payload = json.loads(cleaned)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    lowered = cleaned.lower()
    if "escalat" in lowered:
        return {"action": "escalate", "rationale": cleaned}
    if "retry" in lowered or "refa" in lowered:
        return {"action": "retry", "rationale": cleaned}
    return {"action": "accept", "rationale": cleaned, "improved_response": ""}


def _build_skill_snapshot(prompt_plan: Any) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    expanded = list(getattr(prompt_plan, "expanded_skills", []) or [])
    matched = list(getattr(prompt_plan, "matched_skills", []) or [])
    for skill in expanded + matched:
        if skill.name in seen:
            continue
        seen.add(skill.name)
        lines.append(
            f"- {skill.name}: risk={skill.runtime.risk_level}, cost={skill.runtime.estimated_cost}, "
            f"fallback={skill.runtime.fallback_policy or 'none'}, post={','.join(skill.runtime.postconditions) or 'none'}, "
            f"rel={skill.quality.historical_reliability if skill.quality.historical_reliability is not None else 'n/a'}, "
            f"utility={skill.quality.last_30d_utility if skill.quality.last_30d_utility is not None else 'n/a'}"
        )
    for availability in list(getattr(prompt_plan, "blocked_skills", []) or []):
        skill = availability.skill
        if skill.name in seen:
            continue
        seen.add(skill.name)
        lines.append(
            f"- {skill.name}: blocked ({'; '.join(availability.reasons)}), fallback={skill.runtime.fallback_policy or 'none'}"
        )
    return "\n".join(lines) or "- nenhuma skill operacional relevante"


def _build_human_escalation(handoff: HandoffRecord, response: str, skill_snapshot: str) -> str:
    return (
        "Escalonamento para humano recomendado.\n\n"
        f"Motivo: {handoff.reason}\n"
        f"Objetivo restante: {handoff.remaining_goal}\n"
        f"Resumo: {handoff.summary or 'n/a'}\n"
        f"Skills tentadas: {', '.join(handoff.attempted_skills) or 'nenhuma'}\n"
        f"Falhas: {', '.join(handoff.failures) or 'nenhuma'}\n\n"
        f"Resposta atual:\n{response.strip()}\n\n"
        f"Contexto de skills:\n{skill_snapshot}"
    )