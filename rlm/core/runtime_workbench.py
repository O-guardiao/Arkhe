from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview_text(text: str, limit: int = 240) -> str:
    compact = text.replace("\r", " ").replace("\n", " ").strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _stringify_content(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    except Exception:
        return repr(payload)


TASK_STATUSES: frozenset[str] = frozenset(
    {
        "not-started",
        "in-progress",
        "blocked",
        "completed",
        "cancelled",
    }
)


@dataclass
class TaskEntry:
    task_id: int
    title: str
    status: str = "not-started"
    parent_id: int | None = None
    note: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskLedger:
    def __init__(self) -> None:
        self._entries: dict[int, TaskEntry] = {}
        self._next_task_id = 1
        self._current_task_id: int | None = None
        self._lock = threading.RLock()

    def create(
        self,
        title: str,
        *,
        parent_id: int | None = None,
        status: str = "not-started",
        note: str = "",
        metadata: dict[str, Any] | None = None,
        current: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            normalized_title = str(title).strip()
            if not normalized_title:
                raise ValueError("task title must be a non-empty string")
            self._validate_status(status)
            if parent_id is not None and parent_id not in self._entries:
                raise KeyError(f"parent task {parent_id} not found")

            task = TaskEntry(
                task_id=self._next_task_id,
                title=normalized_title,
                status=status,
                parent_id=parent_id,
                note=str(note).strip(),
                metadata=dict(metadata or {}),
            )
            self._entries[task.task_id] = task
            self._next_task_id += 1
            if current or status == "in-progress":
                self._current_task_id = task.task_id
            return task.to_dict()

    def start(
        self,
        title: str,
        *,
        parent_id: int | None = None,
        note: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.create(
            title,
            parent_id=parent_id,
            status="in-progress",
            note=note,
            metadata=metadata,
            current=True,
        )

    def update(
        self,
        task_id: int,
        *,
        title: str | None = None,
        status: str | None = None,
        note: str | None = None,
        metadata: dict[str, Any] | None = None,
        current: bool | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            task = self._require(task_id)
            if title is not None:
                normalized_title = str(title).strip()
                if not normalized_title:
                    raise ValueError("task title must be a non-empty string")
                task.title = normalized_title
            if status is not None:
                self._validate_status(status)
                task.status = status
                if status == "in-progress" and current is None:
                    current = True
            if note is not None:
                task.note = str(note).strip()
            if metadata:
                task.metadata.update(dict(metadata))
            task.updated_at = _now_iso()
            if current is True:
                self._current_task_id = task.task_id
            elif current is False and self._current_task_id == task.task_id:
                self._current_task_id = None
            return task.to_dict()

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if status is not None:
                self._validate_status(status)
            items = sorted(self._entries.values(), key=lambda item: item.task_id)
            if status is not None:
                items = [item for item in items if item.status == status]
            return [item.to_dict() for item in items]

    def current(self) -> dict[str, Any] | None:
        with self._lock:
            if self._current_task_id is None:
                return None
            task = self._entries.get(self._current_task_id)
            return task.to_dict() if task is not None else None

    def set_current(self, task_id: int | None) -> dict[str, Any] | None:
        with self._lock:
            if task_id is None:
                self._current_task_id = None
                return None
            task = self._require(task_id)
            self._current_task_id = task.task_id
            return task.to_dict()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "next_task_id": self._next_task_id,
                "current_task_id": self._current_task_id,
                "entries": [item.to_dict() for item in sorted(self._entries.values(), key=lambda x: x.task_id)],
            }

    def restore(self, payload: dict[str, Any] | None) -> None:
        with self._lock:
            self._entries.clear()
            self._next_task_id = 1
            self._current_task_id = None
            if not payload:
                return
            for raw in payload.get("entries", []):
                item = TaskEntry(**raw)
                self._entries[item.task_id] = item
            self._next_task_id = int(payload.get("next_task_id", len(self._entries) + 1))
            self._current_task_id = payload.get("current_task_id")

    def _require(self, task_id: int) -> TaskEntry:
        normalized = int(task_id)
        if normalized not in self._entries:
            raise KeyError(f"task {normalized} not found")
        return self._entries[normalized]

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in TASK_STATUSES:
            raise ValueError(f"invalid task status: {status!r}")


@dataclass
class ContextAttachment:
    attachment_id: str
    kind: str
    label: str
    content: str
    preview: str
    source_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    pinned: bool = False
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if not include_content:
            payload.pop("content", None)
        return payload


class ContextAttachmentStore:
    def __init__(self) -> None:
        self._attachments: dict[str, ContextAttachment] = {}
        self._next_attachment_id = 1

    def add_text(
        self,
        label: str,
        content: str,
        *,
        kind: str = "text",
        metadata: dict[str, Any] | None = None,
        source_ref: str = "",
    ) -> dict[str, Any]:
        normalized_label = str(label).strip() or f"attachment-{self._next_attachment_id}"
        normalized_content = str(content)
        attachment = ContextAttachment(
            attachment_id=f"att_{self._next_attachment_id:03d}",
            kind=str(kind).strip() or "text",
            label=normalized_label,
            content=normalized_content,
            preview=_preview_text(normalized_content),
            source_ref=str(source_ref).strip(),
            metadata=dict(metadata or {}),
        )
        self._attachments[attachment.attachment_id] = attachment
        self._next_attachment_id += 1
        return attachment.to_dict()

    def add_context(
        self,
        label: str,
        payload: Any,
        *,
        kind: str = "context",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        content = _stringify_content(payload)
        return self.add_text(label, content, kind=kind, metadata=metadata)

    def list(
        self,
        *,
        kind: str | None = None,
        pinned_only: bool | None = None,
        include_content: bool = False,
    ) -> list[dict[str, Any]]:
        items = sorted(self._attachments.values(), key=lambda item: item.attachment_id)
        if kind is not None:
            items = [item for item in items if item.kind == kind]
        if pinned_only is True:
            items = [item for item in items if item.pinned]
        if pinned_only is False:
            items = [item for item in items if not item.pinned]
        return [item.to_dict(include_content=include_content) for item in items]

    def get(self, attachment_id: str, *, include_content: bool = True) -> dict[str, Any] | None:
        item = self._attachments.get(str(attachment_id).strip())
        if item is None:
            return None
        return item.to_dict(include_content=include_content)

    def pin(self, attachment_id: str, pinned: bool = True) -> dict[str, Any]:
        item = self._require(attachment_id)
        item.pinned = bool(pinned)
        item.updated_at = _now_iso()
        return item.to_dict()

    def snapshot(self) -> dict[str, Any]:
        return {
            "next_attachment_id": self._next_attachment_id,
            "attachments": [
                item.to_dict(include_content=True)
                for item in sorted(self._attachments.values(), key=lambda x: x.attachment_id)
            ],
        }

    def restore(self, payload: dict[str, Any] | None) -> None:
        self._attachments.clear()
        self._next_attachment_id = 1
        if not payload:
            return
        for raw in payload.get("attachments", []):
            item = ContextAttachment(**raw)
            self._attachments[item.attachment_id] = item
        self._next_attachment_id = int(payload.get("next_attachment_id", len(self._attachments) + 1))

    def _require(self, attachment_id: str) -> ContextAttachment:
        normalized = str(attachment_id).strip()
        if normalized not in self._attachments:
            raise KeyError(f"attachment {normalized!r} not found")
        return self._attachments[normalized]


@dataclass
class TimelineEntry:
    entry_id: int
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    origin: str = "runtime"
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExecutionTimeline:
    def __init__(
        self,
        *,
        max_entries: int = 500,
        on_record: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._entries: deque[TimelineEntry] = deque(maxlen=max_entries)
        self._next_entry_id = 1
        self._max_entries = max_entries
        self._on_record = on_record

    def record(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        origin: str = "runtime",
    ) -> dict[str, Any]:
        entry = TimelineEntry(
            entry_id=self._next_entry_id,
            event_type=str(event_type).strip() or "unknown",
            data=dict(data or {}),
            origin=str(origin).strip() or "runtime",
        )
        self._entries.append(entry)
        self._next_entry_id += 1
        payload = entry.to_dict()
        if self._on_record is not None:
            try:
                self._on_record(payload)
            except Exception:
                pass
        return payload

    def recent(self, limit: int = 20, *, event_type: str | None = None) -> list[dict[str, Any]]:
        items = list(self._entries)
        if event_type is not None:
            items = [item for item in items if item.event_type == event_type]
        if limit > 0:
            items = items[-limit:]
        return [item.to_dict() for item in items]

    def clear(self) -> None:
        self._entries.clear()

    def snapshot(self) -> dict[str, Any]:
        return {
            "next_entry_id": self._next_entry_id,
            "max_entries": self._max_entries,
            "entries": [item.to_dict() for item in self._entries],
        }

    def restore(self, payload: dict[str, Any] | None) -> None:
        self._entries.clear()
        self._next_entry_id = 1
        if not payload:
            return
        self._max_entries = int(payload.get("max_entries", self._max_entries))
        entries = payload.get("entries", [])
        self._entries = deque((TimelineEntry(**raw) for raw in entries), maxlen=self._max_entries)
        self._next_entry_id = int(payload.get("next_entry_id", len(self._entries) + 1))


@dataclass
class RecursiveMessageEntry:
    message_id: int
    role: str
    content: str
    branch_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecursiveCommandEntry:
    command_id: int
    command_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    branch_id: int | None = None
    outcome: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecursiveEventEntry:
    event_id: int
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    branch_id: int | None = None
    source: str = "runtime"
    visibility: str = "internal"
    correlation_id: str | None = None
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RecursiveSessionLedger:
    def __init__(
        self,
        *,
        max_messages: int = 400,
        max_commands: int = 200,
        max_events: int = 600,
    ) -> None:
        self._max_messages = max_messages
        self._max_commands = max_commands
        self._max_events = max_events
        self._messages: deque[RecursiveMessageEntry] = deque(maxlen=max_messages)
        self._commands: deque[RecursiveCommandEntry] = deque(maxlen=max_commands)
        self._events: deque[RecursiveEventEntry] = deque(maxlen=max_events)
        self._next_message_id = 1
        self._next_command_id = 1
        self._next_event_id = 1
        self._lock = threading.RLock()

    def add_message(
        self,
        role: str,
        content: str,
        *,
        branch_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            entry = RecursiveMessageEntry(
                message_id=self._next_message_id,
                role=str(role).strip() or "unknown",
                content=str(content),
                branch_id=branch_id,
                metadata=dict(metadata or {}),
            )
            self._messages.append(entry)
            self._next_message_id += 1
            return entry.to_dict()

    def recent_messages(self, limit: int = 20, *, role: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._messages)
            if role is not None:
                items = [item for item in items if item.role == role]
            if limit > 0:
                items = items[-limit:]
            return [item.to_dict() for item in items]

    def queue_command(
        self,
        command_type: str,
        payload: dict[str, Any] | None = None,
        *,
        status: str = "queued",
        branch_id: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            entry = RecursiveCommandEntry(
                command_id=self._next_command_id,
                command_type=str(command_type).strip() or "unknown",
                payload=dict(payload or {}),
                status=str(status).strip() or "queued",
                branch_id=branch_id,
            )
            self._commands.append(entry)
            self._next_command_id += 1
            return entry.to_dict()

    def update_command(
        self,
        command_id: int,
        *,
        status: str,
        outcome: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            normalized = int(command_id)
            for entry in self._commands:
                if entry.command_id == normalized:
                    entry.status = str(status).strip() or entry.status
                    if outcome is not None:
                        entry.outcome = dict(outcome)
                    entry.updated_at = _now_iso()
                    return entry.to_dict()
        raise KeyError(f"recursive command {command_id} not found")

    def recent_commands(
        self,
        limit: int = 20,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._commands)
            if status is not None:
                items = [item for item in items if item.status == status]
            if limit > 0:
                items = items[-limit:]
            return [item.to_dict() for item in items]

    def emit_event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        branch_id: int | None = None,
        source: str = "runtime",
        visibility: str = "internal",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            entry = RecursiveEventEntry(
                event_id=self._next_event_id,
                event_type=str(event_type).strip() or "unknown",
                payload=dict(payload or {}),
                branch_id=branch_id,
                source=str(source).strip() or "runtime",
                visibility=str(visibility).strip() or "internal",
                correlation_id=None if correlation_id is None else str(correlation_id),
            )
            self._events.append(entry)
            self._next_event_id += 1
            return entry.to_dict()

    def recent_events(
        self,
        limit: int = 20,
        *,
        event_type: str | None = None,
        branch_id: int | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._events)
            if event_type is not None:
                items = [item for item in items if item.event_type == event_type]
            if branch_id is not None:
                items = [item for item in items if item.branch_id == branch_id]
            if source is not None:
                items = [item for item in items if item.source == source]
            if limit > 0:
                items = items[-limit:]
            return [item.to_dict() for item in items]

    def state(self) -> dict[str, Any]:
        with self._lock:
            queued = sum(1 for item in self._commands if item.status == "queued")
            return {
                "message_count": len(self._messages),
                "command_count": len(self._commands),
                "event_count": len(self._events),
                "queued_commands": queued,
                "latest_message": self._messages[-1].to_dict() if self._messages else None,
                "latest_command": self._commands[-1].to_dict() if self._commands else None,
                "latest_event": self._events[-1].to_dict() if self._events else None,
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "max_messages": self._max_messages,
                "max_commands": self._max_commands,
                "max_events": self._max_events,
                "next_message_id": self._next_message_id,
                "next_command_id": self._next_command_id,
                "next_event_id": self._next_event_id,
                "messages": [item.to_dict() for item in self._messages],
                "commands": [item.to_dict() for item in self._commands],
                "events": [item.to_dict() for item in self._events],
            }

    def restore(self, payload: dict[str, Any] | None) -> None:
        with self._lock:
            self._messages.clear()
            self._commands.clear()
            self._events.clear()
            self._next_message_id = 1
            self._next_command_id = 1
            self._next_event_id = 1
            if not payload:
                return
            self._max_messages = int(payload.get("max_messages", self._max_messages))
            self._max_commands = int(payload.get("max_commands", self._max_commands))
            self._max_events = int(payload.get("max_events", self._max_events))
            self._messages = deque(
                (RecursiveMessageEntry(**raw) for raw in payload.get("messages", [])),
                maxlen=self._max_messages,
            )
            self._commands = deque(
                (RecursiveCommandEntry(**raw) for raw in payload.get("commands", [])),
                maxlen=self._max_commands,
            )
            self._events = deque(
                (RecursiveEventEntry(**raw) for raw in payload.get("events", [])),
                maxlen=self._max_events,
            )
            self._next_message_id = int(payload.get("next_message_id", len(self._messages) + 1))
            self._next_command_id = int(payload.get("next_command_id", len(self._commands) + 1))
            self._next_event_id = int(payload.get("next_event_id", len(self._events) + 1))


@dataclass
class CoordinationEvent:
    event_id: int
    operation: str
    topic: str = ""
    sender_id: int | None = None
    receiver_id: int | None = None
    payload_preview: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BranchTaskBinding:
    branch_id: int
    task_id: int
    mode: str
    title: str
    parent_task_id: int | None = None
    status: str = "not-started"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CoordinationDigest:
    def __init__(self, *, max_events: int = 200) -> None:
        self._max_events = max_events
        self._events: deque[CoordinationEvent] = deque(maxlen=max_events)
        self._next_event_id = 1
        self._branch_id: int | None = None
        self._attached = False
        self._latest_stats: dict[str, Any] = {}
        self._branch_bindings: dict[int, BranchTaskBinding] = {}
        self._latest_parallel_summary: dict[str, Any] = {}
        self._lock = threading.RLock()

    def attach(self, *, branch_id: int | None = None) -> None:
        with self._lock:
            if branch_id is not None:
                self._branch_id = branch_id
            self._attached = True

    def attached_branch(self) -> int | None:
        with self._lock:
            return self._branch_id

    def record_event(
        self,
        operation: str,
        *,
        topic: str = "",
        sender_id: int | None = None,
        receiver_id: int | None = None,
        payload: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            event = CoordinationEvent(
                event_id=self._next_event_id,
                operation=str(operation).strip() or "unknown",
                topic=str(topic).strip(),
                sender_id=sender_id,
                receiver_id=receiver_id,
                payload_preview=_preview_text(_stringify_content(payload)) if payload is not None else "",
                metadata=dict(metadata or {}),
            )
            self._events.append(event)
            self._next_event_id += 1
            return event.to_dict()

    def update_stats(self, stats: dict[str, Any] | None) -> dict[str, Any]:
        with self._lock:
            self._latest_stats = dict(stats or {})
            return dict(self._latest_stats)

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
        with self._lock:
            task_ids_by_branch = {
                str(branch_id): binding.task_id
                for branch_id, binding in sorted(self._branch_bindings.items())
            }
            self._latest_parallel_summary = {
                "winner_branch_id": winner_branch_id,
                "cancelled_count": int(cancelled_count),
                "failed_count": int(failed_count),
                "total_tasks": int(total_tasks),
                "task_ids_by_branch": task_ids_by_branch,
                "strategy": dict(strategy or {}),
                "stop_evaluation": dict(stop_evaluation or {}),
            }
            return dict(self._latest_parallel_summary)

    def bind_branch_task(
        self,
        branch_id: int,
        task_id: int,
        *,
        mode: str,
        title: str,
        parent_task_id: int | None = None,
        status: str = "not-started",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            binding = self._branch_bindings.get(branch_id)
            if binding is None:
                binding = BranchTaskBinding(
                    branch_id=int(branch_id),
                    task_id=int(task_id),
                    mode=str(mode).strip() or "unknown",
                    title=str(title).strip() or f"branch-{branch_id}",
                    parent_task_id=parent_task_id,
                    status=status,
                    metadata=dict(metadata or {}),
                )
                self._branch_bindings[binding.branch_id] = binding
            else:
                binding.task_id = int(task_id)
                binding.mode = str(mode).strip() or binding.mode
                binding.title = str(title).strip() or binding.title
                binding.parent_task_id = parent_task_id
                binding.status = str(status).strip() or binding.status
                if metadata:
                    binding.metadata.update(dict(metadata))
                binding.updated_at = _now_iso()
            return binding.to_dict()

    def update_branch_task(
        self,
        branch_id: int,
        *,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            binding = self._branch_bindings.get(int(branch_id))
            if binding is None:
                return None
            if status is not None:
                binding.status = str(status).strip() or binding.status
            if metadata:
                binding.metadata.update(dict(metadata))
            binding.updated_at = _now_iso()
            return binding.to_dict()

    def list_branch_tasks(
        self,
        *,
        branch_id: int | None = None,
        task_id: int | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = sorted(self._branch_bindings.values(), key=lambda item: item.branch_id)
            if branch_id is not None:
                items = [item for item in items if item.branch_id == int(branch_id)]
            if task_id is not None:
                items = [item for item in items if item.task_id == int(task_id)]
            return [item.to_dict() for item in items]

    def recent_events(
        self,
        limit: int = 20,
        *,
        operation: str | None = None,
        topic: str | None = None,
        branch_id: int | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._events)
            if operation is not None:
                items = [item for item in items if item.operation == operation]
            if topic is not None:
                items = [item for item in items if item.topic == topic]
            if branch_id is not None:
                normalized_branch = int(branch_id)
                items = [
                    item
                    for item in items
                    if item.sender_id == normalized_branch or item.receiver_id == normalized_branch
                ]
            if limit > 0:
                items = items[-limit:]
            return [item.to_dict() for item in items]

    def filtered_snapshot(
        self,
        *,
        limit: int = 0,
        operation: str | None = None,
        topic: str | None = None,
        branch_id: int | None = None,
    ) -> dict[str, Any]:
        return {
            "attached": self._attached,
            "branch_id": self._branch_id,
            "next_event_id": self._next_event_id,
            "max_events": self._max_events,
            "latest_stats": dict(self._latest_stats),
            "latest_parallel_summary": dict(self._latest_parallel_summary),
            "branch_tasks": self.list_branch_tasks(branch_id=branch_id),
            "filters": {
                "limit": limit,
                "operation": operation,
                "topic": topic,
                "branch_id": branch_id,
            },
            "events": self.recent_events(
                limit=limit,
                operation=operation,
                topic=topic,
                branch_id=branch_id,
            ),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "attached": self._attached,
                "branch_id": self._branch_id,
                "next_event_id": self._next_event_id,
                "max_events": self._max_events,
                "latest_stats": dict(self._latest_stats),
                "latest_parallel_summary": dict(self._latest_parallel_summary),
                "branch_tasks": self.list_branch_tasks(),
                "events": [item.to_dict() for item in self._events],
            }

    def restore(self, payload: dict[str, Any] | None) -> None:
        with self._lock:
            self._attached = False
            self._branch_id = None
            self._next_event_id = 1
            self._latest_stats = {}
            self._latest_parallel_summary = {}
            self._events.clear()
            self._branch_bindings.clear()
            if not payload:
                return
            self._attached = bool(payload.get("attached", False))
            self._branch_id = payload.get("branch_id")
            self._max_events = int(payload.get("max_events", self._max_events))
            self._latest_stats = dict(payload.get("latest_stats", {}))
            self._latest_parallel_summary = dict(payload.get("latest_parallel_summary", {}))
            for raw in payload.get("branch_tasks", []):
                binding = BranchTaskBinding(**raw)
                self._branch_bindings[binding.branch_id] = binding
            entries = payload.get("events", [])
            self._events = deque((CoordinationEvent(**raw) for raw in entries), maxlen=self._max_events)
            self._next_event_id = int(payload.get("next_event_id", len(self._events) + 1))