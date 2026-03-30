"""
RLM Lifecycle Hooks — Fase 8.3

Inspirado em: OpenClaw hooks/internal-hooks.ts (449 LOC)

Sistema de eventos assíncronos que permite plugins e código externo 
reagirem a eventos do ciclo de vida do RLM.

Eventos disponíveis:
- session.created, session.closed, session.error
- session.status_changed, session.origin.updated, session.delivery.updated
- session.operation
- completion.started, completion.finished, completion.aborted
- message.received, message.sent
- repl.executed, repl.error
- plugin.loaded, plugin.unloaded
- supervisor.timeout, supervisor.abort
- compaction.triggered, compaction.completed
- scheduler.job_started, scheduler.job_finished
"""
import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Hook Event
# ---------------------------------------------------------------------------

@dataclass
class HookEvent:
    """Dados passados para handlers de hook."""
    event_type: str           # ex: "completion.started"
    timestamp: str = ""       # ISO timestamp
    session_id: str = ""      # Sessão que gerou o evento
    context: dict = field(default_factory=dict)  # Dados extras

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Hook Handler Types
# ---------------------------------------------------------------------------

# Sync handler: def handler(event: HookEvent) -> None
SyncHandler = Callable[[HookEvent], None]
# Async handler: async def handler(event: HookEvent) -> None
AsyncHandler = Callable[[HookEvent], Awaitable[None]]
# Union type
HookHandler = SyncHandler | AsyncHandler


# ---------------------------------------------------------------------------
# Hook System
# ---------------------------------------------------------------------------

class HookSystem:
    """
    Event-driven hook system for the RLM.
    
    Supports both sync and async handlers. Handlers are called in the
    order they were registered. Errors in handlers are caught and logged
    but never propagate to the caller.
    
    Usage:
        hooks = HookSystem()
        
        # Register a handler
        def on_complete(event: HookEvent):
            print(f"Completion finished in session {event.session_id}")
        hooks.register("completion.finished", on_complete)
        
        # Trigger from RLM internals
        hooks.trigger("completion.finished", 
                      session_id="abc", 
                      context={"response": "Hello!"})
        
        # Pattern matching: register for all completion events
        hooks.register("completion.*", on_any_completion)
        
        # Cleanup
        hooks.unregister("completion.finished", on_complete)
    """

    def __init__(self):
        self._handlers: dict[str, list[HookHandler]] = {}
        self._lock = threading.Lock()
        self._trigger_count = 0
        self._error_count = 0

    def register(self, event_key: str, handler: HookHandler) -> None:
        """
        Register a handler for an event.
        
        Args:
            event_key: Event type (e.g., "completion.started") or 
                      wildcard (e.g., "completion.*" for all completion events).
            handler: Sync or async function to call when event triggers.
        """
        with self._lock:
            if event_key not in self._handlers:
                self._handlers[event_key] = []
            if handler not in self._handlers[event_key]:
                self._handlers[event_key].append(handler)

    def unregister(self, event_key: str, handler: HookHandler) -> bool:
        """
        Remove a registered handler.
        Returns True if the handler was found and removed.
        """
        with self._lock:
            handlers = self._handlers.get(event_key, [])
            if handler in handlers:
                handlers.remove(handler)
                return True
            return False

    def trigger(
        self,
        event_type: str,
        session_id: str = "",
        context: dict | None = None,
    ) -> None:
        """
        Fire an event synchronously.
        
        Calls all matching handlers. Errors are caught and counted
        but never propagate.
        """
        event = HookEvent(
            event_type=event_type,
            session_id=session_id,
            context=context or {},
        )

        handlers = self._get_matching_handlers(event_type)
        self._trigger_count += 1

        for handler in handlers:
            try:
                result = handler(event)
                # If handler is async, run it in event loop
                if asyncio.iscoroutine(result):
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.ensure_future(result)
                        else:
                            loop.run_until_complete(result)
                    except RuntimeError:
                        # No event loop available — close coroutine to avoid warning
                        result.close()
            except Exception as e:
                self._error_count += 1
                # Silently continue - hooks must never break the main flow

    async def trigger_async(
        self,
        event_type: str,
        session_id: str = "",
        context: dict | None = None,
    ) -> None:
        """
        Fire an event asynchronously.
        
        Calls all matching handlers, awaiting async handlers properly.
        """
        event = HookEvent(
            event_type=event_type,
            session_id=session_id,
            context=context or {},
        )

        handlers = self._get_matching_handlers(event_type)
        self._trigger_count += 1

        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                self._error_count += 1

    def clear(self) -> None:
        """Remove all registered handlers."""
        with self._lock:
            self._handlers.clear()

    def get_registered_events(self) -> list[str]:
        """Get list of event keys that have handlers registered."""
        with self._lock:
            return [k for k, v in self._handlers.items() if v]

    def get_stats(self) -> dict:
        """Get hook system statistics."""
        with self._lock:
            total_handlers = sum(len(v) for v in self._handlers.values())
        return {
            "registered_events": len(self._handlers),
            "total_handlers": total_handlers,
            "triggers_fired": self._trigger_count,
            "errors_caught": self._error_count,
        }

    # --- Internal ---

    def _get_matching_handlers(self, event_type: str) -> list[HookHandler]:
        """Get all handlers matching an event type, including wildcards."""
        handlers = []
        with self._lock:
            # Exact match
            handlers.extend(self._handlers.get(event_type, []))

            # Wildcard matches (e.g., "completion.*" matches "completion.started")
            parts = event_type.split(".")
            if len(parts) >= 2:
                wildcard_key = f"{parts[0]}.*"
                handlers.extend(self._handlers.get(wildcard_key, []))

            # Global wildcard
            handlers.extend(self._handlers.get("*", []))

        return handlers
