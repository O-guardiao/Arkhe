from __future__ import annotations

from collections.abc import Callable, Iterable
from collections import Counter
import threading
import time
from pathlib import Path
from typing import Any

from rlm.plugins.channel_registry import sanitize_text_payload
from rlm.runtime.contracts import (
    RuntimeDaemonChannelRuntime,
    RuntimeDaemonMemoryAccess,
    RuntimeDaemonMemoryScope,
    RuntimeDaemonProjection,
    RuntimeProjection,
    RuntimeRecursionBranchView,
    RuntimeRecursionControls,
    RuntimeRecursionEvent,
    RuntimeRecursionProjection,
    RuntimeRecursionSummary,
)


def _tail(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return list(items)
    return list(items[-limit:])


def get_runtime_environment(session: Any) -> Any | None:
    rlm_instance = getattr(session, "rlm_instance", None)
    return getattr(rlm_instance, "_persistent_env", None)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        return list(value)
    return []


def _require_command_entry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("Runtime retornou entrada de comando invalida")
    return dict(value)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except Exception:
            return None
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except Exception:
            return None
    return None


def _normalize_branch_final_status(status: str, metadata: dict[str, Any], error_message: str | None) -> str | None:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "done", "success"}:
        return "success"
    if normalized in {"cancelled", "canceled"}:
        return "cancelled"
    if normalized in {"error", "failed"}:
        return "error"
    if normalized == "blocked":
        if metadata.get("timed_out") is True:
            return "timeout"
        if error_message:
            return "error"
        return "blocked"
    return None


def _build_recursion_controls(controls: dict[str, Any]) -> RuntimeRecursionControls:
    branch_priorities = _as_dict(controls.get("branch_priorities"))
    normalized_priorities = {
        str(key): int(value)
        for key, value in branch_priorities.items()
        if _as_int(value) is not None
        for value in [_as_int(value)]
    }
    return RuntimeRecursionControls(
        paused=bool(controls.get("paused", False)),
        pause_reason=str(controls.get("pause_reason") or ""),
        focused_branch_id=_as_int(controls.get("focused_branch_id")),
        fixed_winner_branch_id=_as_int(controls.get("fixed_winner_branch_id")),
        branch_priorities=normalized_priorities,
        last_checkpoint_path=str(controls.get("last_checkpoint_path") or "-"),
        last_operator_note=str(controls.get("last_operator_note") or ""),
    )


def _build_recursion_branch_view(
    branch: dict[str, Any], controls: RuntimeRecursionControls
) -> RuntimeRecursionBranchView:
    raw = dict(branch)
    metadata = _as_dict(raw.get("metadata"))
    branch_id = _as_int(raw.get("branch_id"))
    focused_branch_id = controls.focused_branch_id
    fixed_winner_branch_id = controls.fixed_winner_branch_id
    branch_priorities = dict(controls.branch_priorities)
    parent_branch_id = _as_int(raw.get("parent_branch_id"))
    if parent_branch_id is None:
        parent_branch_id = _as_int(metadata.get("parent_branch_id"))
    depth = _as_int(raw.get("depth"))
    if depth is None:
        depth = _as_int(metadata.get("child_depth"))
    if depth is None:
        depth = _as_int(metadata.get("depth"))
    duration_ms = _as_float(raw.get("duration_ms"))
    if duration_ms is None:
        elapsed_s = _as_float(metadata.get("elapsed_s"))
        if elapsed_s is not None:
            duration_ms = round(elapsed_s * 1000, 3)
    error_message = str(raw.get("error_message") or metadata.get("error") or "").strip() or None
    operator_priority = _as_int(raw.get("operator_priority"))
    if operator_priority is None:
        operator_priority = _as_int(metadata.get("operator_priority"))
    if operator_priority is None and branch_id is not None:
        operator_priority = _as_int(branch_priorities.get(str(branch_id)))
    status_text = str(raw.get("status") or "")

    return RuntimeRecursionBranchView(
        branch_id=branch_id,
        task_id=_as_int(raw.get("task_id")),
        parent_task_id=_as_int(raw.get("parent_task_id")),
        parent_branch_id=parent_branch_id,
        depth=depth,
        role=raw.get("role") or metadata.get("role"),
        mode=str(raw.get("mode") or ""),
        title=str(raw.get("title") or ""),
        status=status_text,
        final_status=_normalize_branch_final_status(status_text, metadata, error_message),
        duration_ms=duration_ms,
        error_type=raw.get("error_type") or metadata.get("error_type"),
        error_message=error_message,
        operator_focused=bool(metadata.get("operator_focus"))
        or (branch_id is not None and branch_id == focused_branch_id),
        operator_fixed_winner=bool(metadata.get("operator_fixed_winner"))
        or (branch_id is not None and branch_id == fixed_winner_branch_id),
        operator_priority=operator_priority,
        created_at=raw.get("created_at"),
        updated_at=raw.get("updated_at"),
        metadata=metadata,
    )


def _build_recursion_events(items: list[dict[str, Any]]) -> list[RuntimeRecursionEvent]:
    normalized: list[RuntimeRecursionEvent] = []
    for item in items:
        raw = dict(item)
        normalized.append(
            RuntimeRecursionEvent(
                operation=str(raw.get("operation") or "unknown"),
                topic=str(raw.get("topic") or ""),
                sender_id=_as_int(raw.get("sender_id")),
                receiver_id=_as_int(raw.get("receiver_id")),
                payload_preview=str(raw.get("payload_preview") or ""),
                metadata=_as_dict(raw.get("metadata")),
                timestamp=str(raw.get("timestamp") or ""),
            )
        )
    return normalized


def _build_recursion_summary(
    summary: dict[str, Any],
    controls: RuntimeRecursionControls,
    branches: list[RuntimeRecursionBranchView],
) -> RuntimeRecursionSummary:
    status_counts = Counter(str(branch.status or "unknown") for branch in branches)
    winner_branch_id = _as_int(summary.get("winner_branch_id"))
    fixed_winner_branch_id = controls.fixed_winner_branch_id
    if fixed_winner_branch_id is not None:
        winner_branch_id = fixed_winner_branch_id
    return RuntimeRecursionSummary(
        winner_branch_id=winner_branch_id,
        cancelled_count=int(_as_int(summary.get("cancelled_count")) or 0),
        failed_count=int(_as_int(summary.get("failed_count")) or 0),
        total_tasks=int(_as_int(summary.get("total_tasks")) or len(branches)),
        branch_count=len(branches),
        branch_status_counts=dict(status_counts),
        strategy=_as_dict(summary.get("strategy")),
        stop_evaluation=_as_dict(summary.get("stop_evaluation")),
        focused_branch_id=controls.focused_branch_id,
        fixed_winner_branch_id=fixed_winner_branch_id,
    )


def _build_recursion_projection(runtime: dict[str, Any]) -> RuntimeRecursionProjection:
    coordination = _as_dict(runtime.get("coordination"))
    controls = _build_recursion_controls(_as_dict(runtime.get("controls")))
    branch_tasks = [
        _build_recursion_branch_view(item, controls)
        for item in _as_list(coordination.get("branch_tasks"))
        if isinstance(item, dict)
    ]
    return RuntimeRecursionProjection(
        attached=bool(coordination.get("attached", False)),
        active_branch_id=_as_int(coordination.get("branch_id")),
        controls=controls,
        summary=_build_recursion_summary(
            _as_dict(coordination.get("latest_parallel_summary")),
            controls,
            branch_tasks,
        ),
        branches=branch_tasks,
        events=_build_recursion_events(_as_list(coordination.get("events"))),
        latest_stats=_as_dict(coordination.get("latest_stats")),
    )


def _build_daemon_projection(
    session: Any, session_manager: Any | None = None
) -> RuntimeDaemonProjection | None:
    daemon = getattr(session_manager, "_recursion_daemon", None)
    if daemon is None:
        rlm_core = getattr(getattr(session, "rlm_instance", None), "_rlm", None)
        daemon = getattr(rlm_core, "_recursion_daemon", None)
    snapshot = getattr(daemon, "snapshot", None)
    if not callable(snapshot):
        return None

    raw_snapshot = snapshot()
    if not isinstance(raw_snapshot, dict):
        return None

    attached_channels = {
        str(key): int(value)
        for key, value in _as_dict(raw_snapshot.get("attached_channels")).items()
        if _as_int(value) is not None
        for value in [_as_int(value)]
    }
    stats = {
        str(key): int(value)
        for key, value in _as_dict(raw_snapshot.get("stats")).items()
        if _as_int(value) is not None
        for value in [_as_int(value)]
    }
    warm_runtime = {
        str(key): int(value)
        for key, value in _as_dict(raw_snapshot.get("warm_runtime")).items()
        if _as_int(value) is not None
        for value in [_as_int(value)]
    }
    raw_outbox = _as_dict(raw_snapshot.get("outbox"))
    outbox = {
        str(key): int(value)
        for key, value in raw_outbox.items()
        if key != "worker_alive"
        if _as_int(value) is not None
        for value in [_as_int(value)]
    }
    outbox["worker_alive"] = bool(raw_outbox.get("worker_alive", False))
    raw_channel_runtime = _as_dict(raw_snapshot.get("channel_runtime"))
    channel_runtime = {
        "total": int(_as_int(raw_channel_runtime.get("total")) or 0),
        "running": int(_as_int(raw_channel_runtime.get("running")) or 0),
        "healthy": int(_as_int(raw_channel_runtime.get("healthy")) or 0),
        "registered_channels": [
            str(item)
            for item in _as_list(raw_channel_runtime.get("registered_channels"))
            if str(item).strip()
        ],
    }
    raw_memory_access = _as_dict(raw_snapshot.get("memory_access"))
    memory_access = {
        str(key): int(value)
        for key, value in raw_memory_access.items()
        if key != "last_scope"
        if _as_int(value) is not None
        for value in [_as_int(value)]
    }
    raw_last_scope = _as_dict(raw_memory_access.get("last_scope"))
    memory_scope = RuntimeDaemonMemoryScope(
        session_id=str(raw_last_scope.get("session_id") or ""),
        channel=str(raw_last_scope.get("channel") or ""),
        actor=str(raw_last_scope.get("actor") or ""),
        active_channels=[
            str(item)
            for item in _as_list(raw_last_scope.get("active_channels"))
            if str(item).strip()
        ],
        workspace_scope=str(raw_last_scope.get("workspace_scope") or ""),
        agent_depth=_as_int(raw_last_scope.get("agent_depth")),
        branch_id=_as_int(raw_last_scope.get("branch_id")),
        agent_role=str(raw_last_scope.get("agent_role") or ""),
        parent_session_id=str(raw_last_scope.get("parent_session_id") or ""),
    )
    return RuntimeDaemonProjection(
        name=str(raw_snapshot.get("name") or "main"),
        running=bool(raw_snapshot.get("running", False)),
        ready=bool(raw_snapshot.get("ready", False)),
        draining=bool(raw_snapshot.get("draining", False)),
        inflight_dispatches=int(_as_int(raw_snapshot.get("inflight_dispatches")) or 0),
        active_sessions=int(_as_int(raw_snapshot.get("active_sessions")) or 0),
        attached_channels=attached_channels,
        stats=stats,
        warm_runtime=warm_runtime,
        outbox=outbox,
        channel_runtime=RuntimeDaemonChannelRuntime(**channel_runtime),
        memory_access=RuntimeDaemonMemoryAccess(counts=memory_access, last_scope=memory_scope),
    )


def build_runtime_snapshot(session: Any, session_manager: Any | None = None) -> dict[str, Any] | None:
    daemon_projection = _build_daemon_projection(session, session_manager)
    env = get_runtime_environment(session)
    snapshot = getattr(env, "get_runtime_state_snapshot", None)
    if not callable(snapshot):
        if daemon_projection:
            return {"daemon": daemon_projection.to_dict()}
        return None

    runtime_raw = snapshot()
    if not isinstance(runtime_raw, dict):
        if daemon_projection:
            return {"daemon": daemon_projection.to_dict()}
        return None

    runtime = dict(runtime_raw)
    recursive = _as_dict(runtime.get("recursive_session"))
    tasks = _as_dict(runtime.get("tasks"))
    attachments = _as_dict(runtime.get("attachments"))
    timeline = _as_dict(runtime.get("timeline"))
    coordination = _as_dict(runtime.get("coordination"))
    controls = _as_dict(runtime.get("controls"))
    strategy = _as_dict(runtime.get("strategy"))

    recursive["messages"] = _tail(list(recursive.get("messages") or []), 40)
    recursive["commands"] = _tail(list(recursive.get("commands") or []), 20)
    recursive["events"] = _tail(list(recursive.get("events") or []), 40)
    tasks["items"] = _tail(list(tasks.get("items") or []), 20)
    attachments["items"] = _tail(list(attachments.get("items") or []), 10)
    timeline["entries"] = _tail(list(timeline.get("entries") or []), 20)
    coordination["events"] = _tail(list(coordination.get("events") or []), 20)

    runtime["recursive_session"] = recursive
    runtime["tasks"] = tasks
    runtime["attachments"] = attachments
    runtime["timeline"] = timeline
    runtime["coordination"] = coordination
    runtime["controls"] = controls

    projection = RuntimeProjection(
        tasks=tasks,
        attachments=attachments,
        timeline=timeline,
        recursive_session=recursive,
        coordination=coordination,
        controls=controls,
        strategy=strategy,
        recursion=_build_recursion_projection(runtime),
        daemon=daemon_projection,
    )
    return projection.to_dict()


def build_activity_payload(session_manager: Any, session: Any, *, event_limit: int = 40) -> dict[str, Any]:
    get_operation_log = getattr(session_manager, "get_operation_log", None)
    operation_log = []
    if callable(get_operation_log):
        operation_items = get_operation_log(session.session_id, limit=event_limit)
        operation_log = list(reversed(_as_list(operation_items)))
    return {
        "session": session_manager.session_to_dict(session),
        "event_log": list(reversed(session_manager.get_events(session.session_id, limit=event_limit))),
        "operation_log": operation_log,
        "runtime": build_runtime_snapshot(session, session_manager=session_manager),
    }


def _update_command_status(
    env: Any,
    command_id: int,
    *,
    status: str,
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    update_command = getattr(env, "update_recursive_command", None)
    if not callable(update_command):
        return None
    updated = update_command(command_id, status=status, outcome=outcome)
    return _as_dict(updated) or None


def _persist_session_state(session: Any, checkpoint_dir: Path) -> str:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    save_state = getattr(getattr(session, "rlm_instance", None), "save_state", None)
    if callable(save_state):
        return str(save_state(str(checkpoint_dir)))

    env = get_runtime_environment(session)
    save_checkpoint = getattr(env, "save_checkpoint", None)
    if not callable(save_checkpoint):
        raise RuntimeError("Sessao sem mecanismo de checkpoint persistente")
    checkpoint_path = checkpoint_dir / "repl_checkpoint.json"
    return str(save_checkpoint(str(checkpoint_path)))


def _transition_session_status(
    session_manager: Any,
    session: Any,
    status: str,
    *,
    source: str,
    reason: str,
) -> None:
    transition_status = getattr(session_manager, "transition_status", None)
    if callable(transition_status):
        transition_status(session, status, source=source, reason=reason)
        return
    session.status = status
    session_manager.update_session(session)


def _apply_queued_command(
    session_manager: Any,
    session: Any,
    *,
    supervisor: Any | None,
    env: Any,
    command_type: str,
    payload: dict[str, Any],
    branch_id: int | None,
    entry: dict[str, Any],
    origin: str,
) -> dict[str, Any]:
    reason = str(payload.get("reason") or payload.get("note") or "").strip()
    outcome: dict[str, Any] = {}
    session.metadata = dict(getattr(session, "metadata", {}) or {})

    if command_type == "pause_runtime":
        set_paused = getattr(env, "set_runtime_paused", None)
        if not callable(set_paused):
            raise RuntimeError("Runtime sem suporte a pausa operacional")
        control_state = set_paused(True, reason=reason or "Paused by operator", origin=origin)
        abort_result = None
        abort = getattr(supervisor, "abort", None)
        if callable(abort):
            abort_result = bool(abort(session.session_id, reason=reason or "Paused by operator"))
        session.metadata["operator_paused"] = True
        session.metadata["operator_pause_reason"] = reason
        session_manager.update_session(session)
        outcome = {"applied": True, "controls": control_state, "abort_requested": abort_result}
    elif command_type == "resume_runtime":
        set_paused = getattr(env, "set_runtime_paused", None)
        if not callable(set_paused):
            raise RuntimeError("Runtime sem suporte a retomada operacional")
        control_state = set_paused(False, reason=reason or "Resumed by operator", origin=origin)
        session.metadata["operator_paused"] = False
        session.metadata["operator_pause_reason"] = ""
        if getattr(session, "status", "") == "aborted":
            _transition_session_status(
                session_manager,
                session,
                "idle",
                source="operator_surface.resume_runtime",
                reason=reason or "runtime resumed by operator",
            )
        else:
            session_manager.update_session(session)
        outcome = {"applied": True, "controls": control_state}
    elif command_type == "create_checkpoint":
        state_dir = Path(getattr(session, "state_dir", "") or ".")
        checkpoint_name = str(payload.get("checkpoint_name") or f"operator-{int(time.time())}").strip() or f"operator-{int(time.time())}"
        checkpoint_dir = state_dir / "operator_checkpoints" / checkpoint_name
        save_result = _persist_session_state(session, checkpoint_dir)
        mark_checkpoint = getattr(env, "mark_runtime_checkpoint", None)
        control_state = mark_checkpoint(str(checkpoint_dir), origin=origin) if callable(mark_checkpoint) else None
        outcome = {
            "applied": True,
            "checkpoint_path": str(checkpoint_dir),
            "save_result": save_result,
            "controls": control_state,
        }
    elif command_type == "reprioritize_branch":
        if branch_id is None:
            raise ValueError("branch_id e obrigatorio para reprioritize_branch")
        reprioritize = getattr(env, "reprioritize_branch", None)
        if not callable(reprioritize):
            raise RuntimeError("Runtime sem suporte a repriorizacao operacional")
        priority = int(payload.get("priority", 0))
        control_state = reprioritize(branch_id, priority, reason=reason, origin=origin)
        outcome = {"applied": True, "branch_id": branch_id, "priority": priority, "controls": control_state}
    elif command_type in {"focus_branch", "fix_winner_branch"}:
        if branch_id is None:
            raise ValueError(f"branch_id e obrigatorio para {command_type}")
        set_focus = getattr(env, "set_runtime_focus", None)
        if not callable(set_focus):
            raise RuntimeError("Runtime sem suporte a foco operacional de branch")
        control_state = set_focus(branch_id, fixed=command_type == "fix_winner_branch", reason=reason, origin=origin)
        outcome = {
            "applied": True,
            "branch_id": branch_id,
            "fixed": command_type == "fix_winner_branch",
            "controls": control_state,
        }
    elif command_type == "operator_note":
        record_note = getattr(env, "record_operator_note", None)
        if not callable(record_note):
            raise RuntimeError("Runtime sem suporte a nota operacional")
        control_state = record_note(str(payload.get("note") or reason), branch_id=branch_id, origin=origin)
        outcome = {"applied": True, "branch_id": branch_id, "controls": control_state}
    elif command_type in {"request_runtime_summary", "review_parallel_state"}:
        outcome = {"applied": True, "requested": command_type, "branch_id": branch_id}
    else:
        raise ValueError(f"command_type sem executor dedicado: {command_type}")

    updated = _update_command_status(env, int(entry["command_id"]), status="completed", outcome=outcome)
    session_manager.log_event(
        session.session_id,
        f"{origin}_command_applied",
        {
            "command_id": entry.get("command_id"),
            "command_type": command_type,
            "branch_id": branch_id,
            "outcome": outcome,
        },
    )
    return updated or {**entry, "status": "completed", "outcome": outcome}


def apply_operator_command(
    session_manager: Any,
    session: Any,
    *,
    supervisor: Any | None,
    command_type: str,
    payload: dict[str, Any] | None = None,
    branch_id: int | None = None,
    origin: str = "operator",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    env = get_runtime_environment(session)
    queue_command = getattr(env, "queue_recursive_command", None)
    if not callable(queue_command):
        raise RuntimeError("Sessao sem canal de comando recursivo")

    body = dict(payload or {})
    body.setdefault("source", origin)
    body.setdefault("issued_at", int(time.time()))

    entry = _require_command_entry(queue_command(command_type, payload=body, branch_id=branch_id))
    session_manager.log_event(
        session.session_id,
        f"{origin}_command_enqueued",
        {
            "command_id": entry.get("command_id"),
            "command_type": entry.get("command_type"),
            "branch_id": entry.get("branch_id"),
            "payload": body,
        },
    )
    operation_id = None
    log_operation = getattr(session_manager, "log_operation", None)
    if callable(log_operation):
        operation_id = log_operation(
            session.session_id,
            "operator.command",
            phase="queued",
            status="queued",
            source=origin,
            payload={
                "command_id": entry.get("command_id"),
                "command_type": entry.get("command_type"),
                "branch_id": entry.get("branch_id"),
            },
        )

    try:
        applied = _apply_queued_command(
            session_manager,
            session,
            supervisor=supervisor,
            env=env,
            command_type=command_type,
            payload=body,
            branch_id=branch_id,
            entry=entry,
            origin=origin,
        )
        if callable(log_operation):
            log_operation(
                session.session_id,
                "operator.command",
                phase="applied",
                status="completed",
                source=origin,
                operation_id=operation_id,
                payload={
                    "command_id": entry.get("command_id"),
                    "command_type": command_type,
                    "branch_id": branch_id,
                },
            )
    except Exception as exc:
        _update_command_status(env, int(entry["command_id"]), status="failed", outcome={"error": str(exc)})
        session_manager.log_event(
            session.session_id,
            f"{origin}_command_failed",
            {
                "command_id": entry.get("command_id"),
                "command_type": command_type,
                "branch_id": branch_id,
                "error": str(exc),
            },
        )
        if callable(log_operation):
            log_operation(
                session.session_id,
                "operator.command",
                phase="applied",
                status="failed",
                source=origin,
                operation_id=operation_id,
                payload={
                    "command_id": entry.get("command_id"),
                    "command_type": command_type,
                    "branch_id": branch_id,
                    "error": str(exc),
                },
            )
        raise

    return applied, build_runtime_snapshot(session)


def dispatch_operator_prompt(
    session_manager: Any,
    supervisor: Any,
    session: Any,
    *,
    text: str,
    origin: str = "operator",
    runtime_services: Any | None = None,
    client_id: str | None = None,
    dispatch_fn: Callable[..., Any] | None = None,
) -> str:
    prompt = text.strip()
    if not prompt:
        raise ValueError("Mensagem vazia")
    if supervisor is None:
        raise RuntimeError("Supervisor indisponivel")
    if getattr(supervisor, "is_running", lambda _session_id: False)(session.session_id):
        raise RuntimeError("Sessao ja esta executando um turno")

    session.metadata = dict(getattr(session, "metadata", {}) or {})
    session.metadata["last_operator_origin"] = origin
    session.metadata["last_operator_prompt"] = prompt[:500]
    record_message = getattr(getattr(session, "rlm_instance", None), "_record_recursive_message", None)

    session_manager.log_event(
        session.session_id,
        f"{origin}_message_enqueued",
        {
            "text_preview": prompt[:200],
        },
    )
    session_manager.update_session(session)

    def _on_complete(result: Any, finished_session: Any | None = None) -> None:
        target_session = finished_session or session
        target_session.metadata = dict(getattr(target_session, "metadata", {}) or {})
        session.metadata = dict(getattr(session, "metadata", {}) or {})
        response_text = sanitize_text_payload(
            getattr(result, "response", None) if not isinstance(result, dict) else result.get("response", "") or ""
        )
        error_text = sanitize_text_payload(
            (getattr(result, "error_detail", None) if not isinstance(result, dict) else result.get("error_detail"))
            or (getattr(result, "abort_reason", None) if not isinstance(result, dict) else result.get("abort_reason"))
            or ""
        )
        status = getattr(result, "status", None) if not isinstance(result, dict) else result.get("status")
        target_session.metadata["last_operator_status"] = status or "unknown"
        target_session.metadata["last_operator_response"] = (response_text or error_text)[:4000]
        if runtime_services is None and callable(record_message):
            if response_text:
                record_message("assistant", response_text, metadata={"source": origin, "status": status or "completed"})
            elif error_text:
                record_message("assistant", f"[{status or 'error'}] {error_text}", metadata={"source": origin, "status": status or "error"})

        event_type = f"{origin}_response_ready" if status == "completed" else f"{origin}_response_error"
        session_manager.log_event(
            target_session.session_id,
            event_type,
            {
                "status": status or "unknown",
                "response_preview": response_text[:200],
                "error": error_text[:200],
                "execution_time": getattr(result, "execution_time", None) if not isinstance(result, dict) else result.get("execution_time", 0.0),
            },
        )
        if status in {"error", "error_loop", "timeout", "aborted"}:
            target_session.last_error = error_text[:500]
        session_manager.update_session(target_session)

    if runtime_services is not None:
        if dispatch_fn is None:
            raise TypeError("dispatch_fn é obrigatório quando runtime_services é fornecido")

        def _worker() -> None:
            completion_notified = False

            def _notify(result: Any, finished_session: Any | None = None) -> None:
                nonlocal completion_notified
                completion_notified = True
                _on_complete(result, finished_session)

            try:
                dispatch_fn(
                    runtime_services,
                    client_id or getattr(session, "client_id", ""),
                    {
                        "from_user": origin,
                        "session_id": session.session_id,
                        "text": prompt,
                        "channel": origin,
                    },
                    session=session,
                    record_conversation=True,
                    source_name=origin,
                    on_complete=_notify,
                )
            except Exception as exc:
                if completion_notified:
                    return
                _notify(
                    {
                        "status": "error",
                        "response": None,
                        "execution_time": 0.0,
                        "error_detail": str(exc),
                        "abort_reason": None,
                    },
                    session,
                )

        # Transiciona para "running" ANTES de iniciar a thread para evitar race
        # condition: o TUI poll via HTTP pode ver "idle" se a thread ainda nao
        # alcancou supervisor.execute(), fazendo watch_until_idle() retornar
        # imediatamente e o usuario achar que a mensagem foi descartada.
        if hasattr(session_manager, "transition_status"):
            session_manager.transition_status(
                session,
                "running",
                source=f"{origin}.dispatch_operator_prompt",
                reason="dispatch thread queued",
            )
        else:
            session.status = "running"

        thread = threading.Thread(target=_worker, daemon=True, name=f"{origin}-dispatch-{session.session_id[:8]}")
        thread.start()
        return session.session_id

    if callable(record_message):
        record_message("user", prompt, metadata={"source": origin})

    # Mesmo fix do path com dispatch_fn: setar "running" antes de iniciar a
    # thread para evitar race com watch_until_idle() no TUI.
    if hasattr(session_manager, "transition_status"):
        session_manager.transition_status(
            session,
            "running",
            source=f"{origin}.dispatch_operator_prompt",
            reason="execute_async queued",
        )
    else:
        session.status = "running"

    supervisor.execute_async(session, prompt, on_complete=lambda result: _on_complete(result, session))
    return session.session_id