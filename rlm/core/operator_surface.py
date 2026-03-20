from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any


def _tail(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return list(items)
    return list(items[-limit:])


def get_runtime_environment(session: Any) -> Any | None:
    rlm_instance = getattr(session, "rlm_instance", None)
    return getattr(rlm_instance, "_persistent_env", None)


def build_runtime_snapshot(session: Any) -> dict[str, Any] | None:
    env = get_runtime_environment(session)
    snapshot = getattr(env, "get_runtime_state_snapshot", None)
    if not callable(snapshot):
        return None

    runtime = snapshot()
    recursive = dict(runtime.get("recursive_session") or {})
    tasks = dict(runtime.get("tasks") or {})
    attachments = dict(runtime.get("attachments") or {})
    timeline = dict(runtime.get("timeline") or {})
    coordination = dict(runtime.get("coordination") or {})

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
    return runtime


def build_activity_payload(session_manager: Any, session: Any, *, event_limit: int = 40) -> dict[str, Any]:
    return {
        "session": session_manager.session_to_dict(session),
        "event_log": list(reversed(session_manager.get_events(session.session_id, limit=event_limit))),
        "runtime": build_runtime_snapshot(session),
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
    return update_command(command_id, status=status, outcome=outcome)


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
            session.status = "idle"
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
        outcome = {"applied": False, "reason": "command_type sem executor dedicado"}

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

    entry = queue_command(command_type, payload=body, branch_id=branch_id)
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
        response_text = str(getattr(result, "response", None) if not isinstance(result, dict) else result.get("response", "") or "")
        error_text = str(
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
        def _worker() -> None:
            from rlm.server.runtime_pipeline import dispatch_runtime_prompt_sync

            try:
                dispatch_runtime_prompt_sync(
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
                    on_complete=_on_complete,
                )
            except Exception:
                return

        thread = threading.Thread(target=_worker, daemon=True, name=f"{origin}-dispatch-{session.session_id[:8]}")
        thread.start()
        return session.session_id

    if callable(record_message):
        record_message("user", prompt, metadata={"source": origin})

    supervisor.execute_async(session, prompt, on_complete=lambda result: _on_complete(result, session))
    return session.session_id