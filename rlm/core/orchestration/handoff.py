"""RLM Agent handoff contract.

Camada mínima para registrar handoffs explícitos entre papéis sem impor
uma arquitetura multiagente completa ao runtime atual.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from rlm.core.skillkit.skill_telemetry import SkillTelemetryStore, get_skill_telemetry

VALID_HANDOFF_ROLES = ("micro", "worker", "evaluator", "human")


def _normalize_role(role: str) -> str:
    normalized = role.strip().lower().replace("-agent", "").replace("agent", "")
    normalized = normalized.strip(" _-")
    if normalized not in VALID_HANDOFF_ROLES:
        raise ValueError(
            f"target_role inválido: {role!r}. Use um de {VALID_HANDOFF_ROLES}."
        )
    return normalized


def _clean_items(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if not values:
        return []
    return [str(item).strip() for item in values if str(item).strip()]


@dataclass
class HandoffRecord:
    target_role: str
    reason: str
    remaining_goal: str
    summary: str = ""
    attempted_skills: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    suggested_mode: str = ""
    timestamp: str = ""
    task_id: int | None = None
    parent_task_id: int | None = None

    def __post_init__(self) -> None:
        self.target_role = _normalize_role(self.target_role)
        self.reason = str(self.reason).strip()
        self.remaining_goal = str(self.remaining_goal).strip()
        self.summary = str(self.summary).strip()
        self.suggested_mode = str(self.suggested_mode).strip()
        self.attempted_skills = _clean_items(self.attempted_skills)
        self.failures = _clean_items(self.failures)
        if not self.reason:
            raise ValueError("reason é obrigatório para registrar handoff")
        if not self.remaining_goal:
            raise ValueError("remaining_goal é obrigatório para registrar handoff")
        if self.suggested_mode and self.suggested_mode not in {"micro", "focused", "auto", "sif", "full"}:
            raise ValueError("suggested_mode inválido")
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def make_handoff_fn(
    *,
    session_id: str,
    log_event: Callable[[str, str, dict[str, Any] | None], None],
    hooks: Any | None = None,
    telemetry: SkillTelemetryStore | None = None,
    client_id: str = "",
    state_sink: Callable[[dict[str, Any]], None] | None = None,
    task_sink: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> Callable[..., dict[str, Any]]:
    telemetry_store = telemetry or get_skill_telemetry()

    def request_handoff(
        target_role: str,
        reason: str,
        remaining_goal: str,
        summary: str = "",
        attempted_skills: list[str] | tuple[str, ...] | None = None,
        failures: list[str] | tuple[str, ...] | None = None,
        suggested_mode: str = "",
    ) -> dict[str, Any]:
        record = HandoffRecord(
            target_role=target_role,
            reason=reason,
            remaining_goal=remaining_goal,
            summary=summary,
            attempted_skills=list(attempted_skills or []),
            failures=list(failures or []),
            suggested_mode=suggested_mode,
        )
        payload = record.to_payload()
        if task_sink is not None:
            task_payload = task_sink(dict(payload))
            if isinstance(task_payload, dict):
                if task_payload.get("task_id") is not None:
                    payload["task_id"] = int(task_payload["task_id"])
                if task_payload.get("parent_task_id") is not None:
                    payload["parent_task_id"] = int(task_payload["parent_task_id"])
        log_event(session_id, "agent_handoff", payload)
        if hooks is not None:
            hooks.trigger("agent.handoff", session_id=session_id, context=payload)
        telemetry_store.record_handoff(
            payload=payload,
            session_id=session_id,
            client_id=client_id,
        )
        if state_sink is not None:
            state_sink(payload)
        return {
            "ok": True,
            "event_type": "agent_handoff",
            "session_id": session_id,
            "handoff": payload,
        }

    request_handoff.__name__ = "request_handoff"
    return request_handoff