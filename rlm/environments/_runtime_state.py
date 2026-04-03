"""Runtime state management mixin for LocalREPL.

Provides methods for:
- Event timeline recording
- Recursive session management (messages, events, commands)
- Task ledger management
- Sub-agent task registration
- Operator control (pause, focus, priority, notes, checkpoints)
- Sibling bus coordination
- Active recursive strategy management

Extracted from local_repl.py during responsibility separation refactoring.
"""

from __future__ import annotations

import time
from typing import Any


class RuntimeStateMixin:
    """Manages runtime state: events, tasks, recursive sessions, and operator controls.

    Assumes the concrete class sets the following attributes in ``__init__``:

    - ``_execution_timeline`` (:class:`ExecutionTimeline`)
    - ``_recursive_session`` (:class:`RecursiveSessionLedger`)
    - ``_task_ledger`` (:class:`TaskLedger`)
    - ``_coordination_digest`` (:class:`CoordinationDigest`)
    - ``_context_attachments`` (:class:`ContextAttachmentStore`)
    - ``_active_recursive_strategy`` (``dict | None``)
    - ``_runtime_control_state`` (``dict``)
    - ``_event_bus`` (optional event bus)
    - ``_sibling_bus`` (optional sibling bus)
    - ``_sibling_branch_id`` (``int | None``)
    """

    # -----------------------------------------------------------------
    # Timeline
    # -----------------------------------------------------------------

    def record_runtime_event(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        origin: str = "runtime",
    ) -> dict[str, Any]:
        return self._execution_timeline.record(event_type, data, origin=origin)

    # -----------------------------------------------------------------
    # Recursive Session — Messages
    # -----------------------------------------------------------------

    def record_recursive_message(
        self,
        role: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        branch_id: int | None = None,
    ) -> dict[str, Any]:
        entry = self._recursive_session.add_message(
            role,
            content,
            metadata=metadata,
            branch_id=branch_id,
        )
        self.record_runtime_event(
            "recursive.message.recorded",
            {
                "message_id": entry["message_id"],
                "role": entry["role"],
                "branch_id": entry["branch_id"],
            },
            origin="recursive-session",
        )
        self.emit_recursive_event(
            "user_message_received" if entry["role"] == "user" else "assistant_message_emitted",
            payload={
                "message_id": entry["message_id"],
                "role": entry["role"],
                "metadata": dict(entry.get("metadata") or {}),
            },
            branch_id=entry["branch_id"],
            source=str((entry.get("metadata") or {}).get("source") or "runtime"),
            visibility="chat",
            correlation_id=f"msg:{entry['message_id']}",
        )
        return entry

    def recent_recursive_messages(
        self,
        *,
        limit: int = 20,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._recursive_session.recent_messages(limit=limit, role=role)

    # -----------------------------------------------------------------
    # Recursive Session — Events
    # -----------------------------------------------------------------

    def emit_recursive_event(
        self,
        event_type: str,
        *,
        payload: dict[str, Any] | None = None,
        branch_id: int | None = None,
        source: str = "runtime",
        visibility: str = "internal",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        entry = self._recursive_session.emit_event(
            event_type,
            payload=payload,
            branch_id=branch_id,
            source=source,
            visibility=visibility,
            correlation_id=correlation_id,
        )
        self.record_runtime_event(
            "recursive.event.emitted",
            {
                "event_id": entry["event_id"],
                "event_type": entry["event_type"],
                "branch_id": entry["branch_id"],
                "source": entry["source"],
            },
            origin="recursive-session",
        )
        return entry

    def recent_recursive_events(
        self,
        *,
        limit: int = 20,
        event_type: str | None = None,
        branch_id: int | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._recursive_session.recent_events(
            limit=limit,
            event_type=event_type,
            branch_id=branch_id,
            source=source,
        )

    # -----------------------------------------------------------------
    # Recursive Session — Commands
    # -----------------------------------------------------------------

    def queue_recursive_command(
        self,
        command_type: str,
        *,
        payload: dict[str, Any] | None = None,
        status: str = "queued",
        branch_id: int | None = None,
    ) -> dict[str, Any]:
        entry = self._recursive_session.queue_command(
            command_type,
            payload=payload,
            status=status,
            branch_id=branch_id,
        )
        self.record_runtime_event(
            "recursive.command.queued",
            {
                "command_id": entry["command_id"],
                "command_type": entry["command_type"],
                "status": entry["status"],
                "branch_id": entry["branch_id"],
            },
            origin="recursive-session",
        )
        self.emit_recursive_event(
            "command_queued",
            payload={
                "command_id": entry["command_id"],
                "command_type": entry["command_type"],
                "status": entry["status"],
                "payload": dict(entry.get("payload") or {}),
            },
            branch_id=entry["branch_id"],
            source="control",
            visibility="control",
            correlation_id=f"cmd:{entry['command_id']}",
        )
        return entry

    def update_recursive_command(
        self,
        command_id: int,
        *,
        status: str,
        outcome: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = self._recursive_session.update_command(
            command_id,
            status=status,
            outcome=outcome,
        )
        self.record_runtime_event(
            "recursive.command.updated",
            {
                "command_id": entry["command_id"],
                "command_type": entry["command_type"],
                "status": entry["status"],
            },
            origin="recursive-session",
        )
        self.emit_recursive_event(
            "command_updated",
            payload={
                "command_id": entry["command_id"],
                "command_type": entry["command_type"],
                "status": entry["status"],
                "outcome": dict(entry.get("outcome") or {}),
            },
            branch_id=entry["branch_id"],
            source="control",
            visibility="control",
            correlation_id=f"cmd:{entry['command_id']}",
        )
        return entry

    def recent_recursive_commands(
        self,
        *,
        limit: int = 20,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._recursive_session.recent_commands(limit=limit, status=status)

    def get_recursive_session_state(self) -> dict[str, Any]:
        return self._recursive_session.state()

    # -----------------------------------------------------------------
    # Task Ledger
    # -----------------------------------------------------------------

    def current_runtime_task(self) -> dict[str, Any] | None:
        return self._task_ledger.current()

    def current_runtime_task_id(self) -> int | None:
        current = self.current_runtime_task()
        return current.get("task_id") if current is not None else None

    def create_runtime_task(
        self,
        title: str,
        *,
        parent_task_id: int | None = None,
        status: str = "not-started",
        note: str = "",
        metadata: dict[str, Any] | None = None,
        current: bool = False,
    ) -> dict[str, Any]:
        return self._task_ledger.create(
            title,
            parent_id=parent_task_id,
            status=status,
            note=note,
            metadata=metadata,
            current=current,
        )

    def update_runtime_task(
        self,
        task_id: int,
        *,
        title: str | None = None,
        status: str | None = None,
        note: str | None = None,
        metadata: dict[str, Any] | None = None,
        current: bool | None = None,
    ) -> dict[str, Any]:
        return self._task_ledger.update(
            task_id,
            title=title,
            status=status,
            note=note,
            metadata=metadata,
            current=current,
        )

    # -----------------------------------------------------------------
    # Sub-agent Task Registration
    # -----------------------------------------------------------------

    def register_subagent_task(
        self,
        *,
        mode: str,
        title: str,
        branch_id: int | None = None,
        parent_task_id: int | None = None,
        metadata: dict[str, Any] | None = None,
        current: bool = False,
    ) -> dict[str, Any]:
        resolved_parent = parent_task_id
        if resolved_parent is None:
            resolved_parent = self.current_runtime_task_id()
        task = self.create_runtime_task(
            title,
            parent_task_id=resolved_parent,
            status="in-progress",
            metadata=metadata,
            current=current,
        )
        if branch_id is not None:
            self._coordination_digest.bind_branch_task(
                int(branch_id),
                int(task["task_id"]),
                mode=mode,
                title=task["title"],
                parent_task_id=resolved_parent,
                status=task["status"],
                metadata=metadata,
            )
        return task

    def update_subagent_task(
        self,
        *,
        task_id: int,
        branch_id: int | None = None,
        status: str | None = None,
        note: str | None = None,
        metadata: dict[str, Any] | None = None,
        current: bool | None = None,
    ) -> dict[str, Any]:
        task = self.update_runtime_task(
            task_id,
            status=status,
            note=note,
            metadata=metadata,
            current=current,
        )
        if branch_id is not None:
            self._coordination_digest.update_branch_task(
                int(branch_id),
                status=task["status"],
                metadata=metadata,
            )
        return task

    # -----------------------------------------------------------------
    # Active Recursive Strategy
    # -----------------------------------------------------------------

    def set_parallel_summary(
        self,
        *,
        winner_branch_id: int | None,
        cancelled_count: int,
        failed_count: int,
        total_tasks: int,
        strategy: dict[str, Any] | None = None,
        stop_evaluation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._coordination_digest.set_parallel_summary(
            winner_branch_id=winner_branch_id,
            cancelled_count=cancelled_count,
            failed_count=failed_count,
            total_tasks=total_tasks,
            strategy=strategy,
            stop_evaluation=stop_evaluation,
        )

    def set_active_recursive_strategy(
        self,
        strategy: dict[str, Any] | None,
        *,
        origin: str = "runtime",
    ) -> dict[str, Any] | None:
        if strategy is None:
            self._active_recursive_strategy = None
            self.record_runtime_event(
                "strategy.cleared",
                {"origin": origin},
                origin="strategy",
            )
            return None
        self._active_recursive_strategy = dict(strategy)
        self.record_runtime_event(
            "strategy.activated",
            {
                "origin": origin,
                "strategy_name": self._active_recursive_strategy.get("strategy_name")
                or self._active_recursive_strategy.get("name"),
                "coordination_policy": self._active_recursive_strategy.get("coordination_policy"),
                "stop_condition": self._active_recursive_strategy.get("stop_condition"),
                "repl_search_mode": self._active_recursive_strategy.get("repl_search_mode"),
            },
            origin="strategy",
        )
        return dict(self._active_recursive_strategy)

    def get_active_recursive_strategy(self) -> dict[str, Any] | None:
        if self._active_recursive_strategy is None:
            return None
        return dict(self._active_recursive_strategy)

    def clear_active_recursive_strategy(self, *, origin: str = "runtime") -> None:
        self.set_active_recursive_strategy(None, origin=origin)

    # -----------------------------------------------------------------
    # Operator Control
    # -----------------------------------------------------------------

    def get_runtime_control_state(self) -> dict[str, Any]:
        state = dict(self._runtime_control_state)
        state["branch_priorities"] = dict(state.get("branch_priorities") or {})
        return state

    def _publish_operator_control(
        self,
        signal_type: str,
        payload: dict[str, Any],
        *,
        sender_id: int | None = None,
    ) -> bool:
        publish_control = getattr(self._sibling_bus, "publish_control", None)
        if not callable(publish_control):
            return False
        topic = f"control/{signal_type}"
        publish_control(topic, dict(payload), sender_id=sender_id, signal_type=signal_type)
        return True

    def set_runtime_paused(
        self,
        paused: bool,
        *,
        reason: str = "",
        origin: str = "operator",
    ) -> dict[str, Any]:
        self._runtime_control_state["paused"] = bool(paused)
        self._runtime_control_state["pause_reason"] = str(reason or "")
        event_type = "runtime_paused" if paused else "runtime_resumed"
        self.record_runtime_event(
            f"control.{event_type}",
            {"reason": reason, "origin": origin},
            origin="control",
        )
        self.emit_recursive_event(
            event_type,
            payload={"reason": reason, "origin": origin},
            source="control",
            visibility="control",
        )
        if paused:
            self._publish_operator_control(
                "stop",
                {"reason": reason or "Paused by operator", "origin": origin, "action": "pause_runtime"},
            )
        return self.get_runtime_control_state()

    def set_runtime_focus(
        self,
        branch_id: int,
        *,
        fixed: bool = False,
        reason: str = "",
        origin: str = "operator",
    ) -> dict[str, Any]:
        normalized_branch = int(branch_id)
        self._runtime_control_state["focused_branch_id"] = normalized_branch
        if fixed:
            self._runtime_control_state["fixed_winner_branch_id"] = normalized_branch
        branch_tasks = self._coordination_digest.list_branch_tasks()
        for item in branch_tasks:
            self._coordination_digest.update_branch_task(
                int(item["branch_id"]),
                metadata={
                    "operator_focus": int(item["branch_id"]) == normalized_branch,
                    "operator_fixed_winner": fixed and int(item["branch_id"]) == normalized_branch,
                },
            )
        summary = self._coordination_digest.filtered_snapshot(limit=0).get("latest_parallel_summary") or {}
        self.set_parallel_summary(
            winner_branch_id=normalized_branch if fixed else summary.get("winner_branch_id"),
            cancelled_count=int(summary.get("cancelled_count", 0)),
            failed_count=int(summary.get("failed_count", 0)),
            total_tasks=int(summary.get("total_tasks", len(branch_tasks))),
            strategy=summary.get("strategy"),
            stop_evaluation=summary.get("stop_evaluation"),
        )
        action = "fix_winner_branch" if fixed else "focus_branch"
        self.record_runtime_event(
            f"control.{action}",
            {"branch_id": normalized_branch, "reason": reason, "origin": origin},
            origin="control",
        )
        self.emit_recursive_event(
            action,
            payload={"branch_id": normalized_branch, "reason": reason, "origin": origin},
            branch_id=normalized_branch,
            source="control",
            visibility="control",
        )
        self._publish_operator_control(
            "switch_strategy",
            {
                "action": action,
                "prioritized_branch_id": normalized_branch,
                "reason": reason,
                "origin": origin,
            },
            sender_id=normalized_branch if fixed else None,
        )
        if fixed:
            self._publish_operator_control(
                "stop",
                {"reason": reason or f"Winner branch fixed by operator: {normalized_branch}", "origin": origin, "action": action},
                sender_id=normalized_branch,
            )
        return self.get_runtime_control_state()

    def reprioritize_branch(
        self,
        branch_id: int,
        priority: int,
        *,
        reason: str = "",
        origin: str = "operator",
    ) -> dict[str, Any]:
        normalized_branch = int(branch_id)
        normalized_priority = int(priority)
        priorities = dict(self._runtime_control_state.get("branch_priorities") or {})
        priorities[str(normalized_branch)] = normalized_priority
        self._runtime_control_state["branch_priorities"] = priorities
        self._coordination_digest.update_branch_task(
            normalized_branch,
            metadata={"operator_priority": normalized_priority, "operator_priority_reason": reason},
        )
        strategy = self.get_active_recursive_strategy() or {}
        strategy["operator_branch_priorities"] = dict(priorities)
        self.set_active_recursive_strategy(strategy or None, origin=origin)
        self.record_runtime_event(
            "control.reprioritize_branch",
            {"branch_id": normalized_branch, "priority": normalized_priority, "reason": reason, "origin": origin},
            origin="control",
        )
        self.emit_recursive_event(
            "reprioritize_branch",
            payload={"branch_id": normalized_branch, "priority": normalized_priority, "reason": reason, "origin": origin},
            branch_id=normalized_branch,
            source="control",
            visibility="control",
        )
        self._publish_operator_control(
            "switch_strategy",
            {
                "action": "reprioritize_branch",
                "prioritized_branch_id": normalized_branch,
                "priority": normalized_priority,
                "reason": reason,
                "origin": origin,
                "branch_priorities": priorities,
            },
        )
        return self.get_runtime_control_state()

    def record_operator_note(self, note: str, *, branch_id: int | None = None, origin: str = "operator") -> dict[str, Any]:
        clean = str(note or "").strip()
        self._runtime_control_state["last_operator_note"] = clean
        self.record_runtime_event(
            "control.operator_note",
            {"note": clean, "branch_id": branch_id, "origin": origin},
            origin="control",
        )
        self.emit_recursive_event(
            "operator_note",
            payload={"note": clean, "origin": origin},
            branch_id=branch_id,
            source="control",
            visibility="control",
        )
        return self.get_runtime_control_state()

    def mark_runtime_checkpoint(self, checkpoint_path: str, *, origin: str = "operator") -> dict[str, Any]:
        self._runtime_control_state["last_checkpoint_path"] = str(checkpoint_path)
        self._runtime_control_state["last_checkpoint_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.record_runtime_event(
            "control.checkpoint_created",
            {"checkpoint_path": checkpoint_path, "origin": origin},
            origin="control",
        )
        self.emit_recursive_event(
            "checkpoint_created",
            payload={"checkpoint_path": checkpoint_path, "origin": origin},
            source="control",
            visibility="control",
        )
        return self.get_runtime_control_state()

    # -----------------------------------------------------------------
    # Full State Snapshot
    # -----------------------------------------------------------------

    def get_runtime_state_snapshot(
        self,
        *,
        coordination_limit: int = 0,
        coordination_operation: str | None = None,
        coordination_topic: str | None = None,
        coordination_branch_id: int | None = None,
    ) -> dict[str, Any]:
        return {
            "tasks": {
                "current": self._task_ledger.current(),
                "items": self._task_ledger.list(),
            },
            "attachments": {
                "items": self._context_attachments.list(include_content=False),
            },
            "timeline": {
                "entries": self._execution_timeline.recent(limit=0),
            },
            "recursive_session": {
                "state": self.get_recursive_session_state(),
                "messages": self.recent_recursive_messages(limit=0),
                "commands": self.recent_recursive_commands(limit=0),
                "events": self.recent_recursive_events(limit=0),
            },
            "coordination": self._coordination_digest.filtered_snapshot(
                limit=coordination_limit,
                operation=coordination_operation,
                topic=coordination_topic,
                branch_id=coordination_branch_id,
            ),
            "controls": self.get_runtime_control_state(),
            "strategy": {
                "active_recursive_strategy": self.get_active_recursive_strategy(),
            },
        }

    # -----------------------------------------------------------------
    # Sibling Bus / Coordination
    # -----------------------------------------------------------------

    def attach_sibling_bus(self, sibling_bus: Any, *, branch_id: int | None = None) -> None:
        self._sibling_bus = sibling_bus
        if branch_id is not None:
            self._sibling_branch_id = branch_id
        self._coordination_digest.attach(branch_id=self._sibling_branch_id)

        add_observer = getattr(sibling_bus, "add_observer", None)
        if callable(add_observer):
            add_observer(self._handle_sibling_bus_event)

        get_stats = getattr(sibling_bus, "get_stats", None)
        if callable(get_stats):
            try:
                stats = get_stats()
                self._coordination_digest.update_stats(
                    stats if isinstance(stats, dict) else None
                )
            except Exception:
                pass

    def _handle_sibling_bus_event(self, payload: dict[str, Any]) -> None:
        event = self._coordination_digest.record_event(
            payload.get("operation", "unknown"),
            topic=payload.get("topic", ""),
            sender_id=payload.get("sender_id"),
            receiver_id=payload.get("receiver_id"),
            payload=payload.get("payload"),
            metadata=payload.get("metadata"),
        )
        self._coordination_digest.update_stats(payload.get("stats"))
        self.record_runtime_event(
            "coordination.bus_event",
            {
                "operation": event["operation"],
                "topic": event["topic"],
                "sender_id": event["sender_id"],
                "receiver_id": event["receiver_id"],
            },
            origin="coordination",
        )
