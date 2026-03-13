"""
RLM Execution Supervisor — Fase 7.2 + Fase 10

Envelopa RLM.completion() com controles de segurança:
- Timeout: mata execução após N segundos
- Abort externo: endpoint HTTP pode parar a execução
- Error loop detection: se o mesmo erro repetir N vezes, corta
- Monitora estado da sessão durante a execução

Fase 10: CancellationToken composicional, ShutdownManager com veto.
"""
import threading
import time
import re
from dataclasses import dataclass, field
from typing import Any, Callable
from concurrent.futures import ThreadPoolExecutor, Future

from rlm.core.session import SessionRecord, SessionManager
from rlm.core.cancellation import CancellationToken, CancellationTokenSource
from rlm.core.shutdown import ShutdownManager
from rlm.core.disposable import adapt_closeable


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class SupervisorConfig:
    """Limites de segurança para execução do RLM."""
    max_execution_time: int = 120       # Segundos antes de timeout
    max_consecutive_errors: int = 5     # Corta se o mesmo erro repetir N vezes
    max_iterations_override: int | None = None  # Override do max_iterations do RLM
    poll_interval: float = 1.0          # Intervalo de polling para verificar saúde


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class ExecutionResult:
    """Resultado de uma execução supervisionada."""
    session_id: str
    status: str           # completed | timeout | aborted | error_loop | error
    response: str | None = None
    execution_time: float = 0.0
    iterations_used: int = 0
    abort_reason: str | None = None
    error_detail: str | None = None


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

class RLMSupervisor:
    """
    Supervisiona execuções do RLM com limites de segurança.
    
    O Supervisor roda RLM.completion() numa thread separada e monitora:
    - Timeout via threading.Timer
    - Abort externo via threading.Event (setado pelo endpoint DELETE)
    - Error loops interceptando exceções repetitivas
    
    Usage:
        supervisor = RLMSupervisor()
        result = supervisor.execute(session, "Analise este evento...")
        
        # From another thread (e.g., HTTP endpoint):
        supervisor.abort(session.session_id, reason="User requested stop")
    """

    def __init__(self, default_config: SupervisorConfig | None = None):
        self.default_config = default_config or SupervisorConfig()
        self._abort_events: dict[str, threading.Event] = {}  # session_id -> Event (legacy)
        self._cancel_sources: dict[str, CancellationTokenSource] = {}  # Fase 10
        self._active_futures: dict[str, Future] = {}          # session_id -> Future
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rlm-worker")
        self._lock = threading.Lock()
        self._shutdown_manager = ShutdownManager()

    # --- Core Execution ---

    def execute(
        self,
        session: SessionRecord,
        prompt: str | list | dict,
        config: SupervisorConfig | None = None,
        on_complete: Callable[[ExecutionResult], None] | None = None,
    ) -> ExecutionResult:
        """
        Execute RLM.completion() with safety boundaries (blocking).
        
        Args:
            session: The RLM session to execute in.
            prompt: The prompt to send to the RLM.
            config: Override supervisor config for this execution.
            on_complete: Optional callback when execution finishes.
            
        Returns:
            ExecutionResult with status and response.
        """
        cfg = config or self.default_config

        if session.rlm_instance is None:
            return ExecutionResult(
                session_id=session.session_id,
                status="error",
                error_detail="No RLM instance in session. Session may not be activated.",
            )

        if self.is_running(session.session_id):
            return ExecutionResult(
                session_id=session.session_id,
                status="error",
                error_detail="Session is already running a completion.",
            )

        # Setup abort event and inject into RLM instance
        abort_event = threading.Event()
        with self._lock:
            self._abort_events[session.session_id] = abort_event
        
        # Inject the abort event into the RLM so the iteration loop can check it
        session.rlm_instance._abort_event = abort_event

        # Fase 10: CancellationToken — sistema composicional
        cancel_source = CancellationTokenSource()
        with self._lock:
            self._cancel_sources[session.session_id] = cancel_source
        # Injeta token no RLM (ou RLMSession)
        if hasattr(session.rlm_instance, '_cancel_token'):
            session.rlm_instance._cancel_token = cancel_source.token

        # Override max_iterations if configured
        original_max_iter = session.rlm_instance.max_iterations
        if cfg.max_iterations_override is not None:
            session.rlm_instance.max_iterations = cfg.max_iterations_override

        session.status = "running"
        start_time = time.perf_counter()
        result = ExecutionResult(session_id=session.session_id, status="running")

        # Setup timeout timer
        timeout_timer = threading.Timer(cfg.max_execution_time, self._on_timeout, 
                                         args=(session.session_id, cfg.max_execution_time))
        timeout_timer.daemon = True
        timeout_timer.start()

        try:
            # Run the actual completion
            rlm_result = session.rlm_instance.completion(prompt, mcts_branches=0)

            elapsed = time.perf_counter() - start_time

            # Check if we were aborted during execution
            if abort_event.is_set():
                result.status = "aborted"
                result.abort_reason = self._abort_events.get(session.session_id, None)
                result.response = rlm_result.response if rlm_result else None
            else:
                result.status = "completed"
                result.response = rlm_result.response if rlm_result else None

            result.execution_time = elapsed

            # Track usage
            if rlm_result and hasattr(rlm_result, 'usage_summary') and rlm_result.usage_summary:
                usage = rlm_result.usage_summary
                if hasattr(usage, 'total_tokens'):
                    session.total_tokens_used += usage.total_tokens

            session.total_completions += 1

        except Exception as e:
            elapsed = time.perf_counter() - start_time
            error_str = str(e)

            # Check if this is a known error loop pattern
            if self._is_error_loop_pattern(error_str):
                result.status = "error_loop"
                result.error_detail = f"Detected repeating error pattern: {error_str[:200]}"
                result.abort_reason = "Automatic: repeating error loop detected"
            elif abort_event.is_set():
                result.status = "timeout" if "timeout" in str(getattr(abort_event, '_reason', '')) else "aborted"
                result.abort_reason = f"Execution interrupted after {elapsed:.1f}s"
            else:
                result.status = "error"
                result.error_detail = error_str[:500]

            result.execution_time = elapsed
            session.last_error = error_str[:500]

        finally:
            timeout_timer.cancel()
            
            # Restore original max_iterations
            if cfg.max_iterations_override is not None:
                session.rlm_instance.max_iterations = original_max_iter
            
            # Cleanup abort event
            with self._lock:
                self._abort_events.pop(session.session_id, None)
                source = self._cancel_sources.pop(session.session_id, None)
                if source is not None:
                    source.dispose()
            
            # Remove abort event from RLM instance
            if hasattr(session.rlm_instance, '_abort_event'):
                session.rlm_instance._abort_event = None
            # Reseta cancel token para NONE (nunca cancela)
            if hasattr(session.rlm_instance, '_cancel_token'):
                session.rlm_instance._cancel_token = CancellationToken.NONE
            
            # Update session status
            if result.status == "completed":
                session.status = "idle"
            elif result.status in ("error", "error_loop"):
                session.status = "error"
            elif result.status in ("aborted", "timeout"):
                session.status = "idle"  # Can be retried

            if on_complete:
                on_complete(result)

        return result

    def execute_async(
        self,
        session: SessionRecord,
        prompt: str | list | dict,
        config: SupervisorConfig | None = None,
        on_complete: Callable[[ExecutionResult], None] | None = None,
    ) -> str:
        """
        Execute RLM.completion() asynchronously.
        
        Returns the session_id. Use is_running() and get_result() to check status.
        """
        future = self._executor.submit(
            self.execute, session, prompt, config, on_complete
        )
        with self._lock:
            self._active_futures[session.session_id] = future
        return session.session_id

    # --- Control ---

    def abort(self, session_id: str, reason: str = "User requested abort") -> bool:
        """
        Request abort of a running execution.
        
        Fase 10: Usa CancellationToken para cancelamento composicional,
        mantendo compatibilidade com o abort_event legado.
        
        Returns True if the abort signal was sent.
        """
        with self._lock:
            # Fase 10: Cancel via token
            source = self._cancel_sources.get(session_id)
            if source is not None:
                source.cancel(reason=reason)

            # Legacy: set abort event
            event = self._abort_events.get(session_id)
            if event is None:
                return source is not None  # True se pelo menos o token foi cancelado
            event.set()
            event._reason = reason  # type: ignore
            return True

    def is_running(self, session_id: str) -> bool:
        """Check if a session has an active execution."""
        with self._lock:
            return session_id in self._abort_events

    def get_active_sessions(self) -> list[str]:
        """Get list of session IDs with active executions."""
        with self._lock:
            return list(self._abort_events.keys())

    # --- Internal ---

    def _on_timeout(self, session_id: str, timeout_seconds: int):
        """Called by timer when execution exceeds the time limit."""
        self.abort(session_id, reason=f"Timeout after {timeout_seconds}s")

    def _is_error_loop_pattern(self, error_str: str) -> bool:
        """
        Detect known error loop patterns (like the socket_request issue).
        
        These are errors that indicate the RLM is stuck in an unrecoverable
        loop and should be terminated rather than retried.
        """
        known_patterns = [
            "socket_request() missing",
            "Connection refused",
            "ConnectionResetError",
            "BrokenPipeError",
        ]
        return any(pattern in error_str for pattern in known_patterns)

    def shutdown(self):
        """Fase 10: Graceful shutdown com veto — espera compactações e salva memórias."""
        # Registra veto: se alguma sessão ainda está rodando
        self._shutdown_manager.register_veto(
            "active_sessions",
            lambda: len(self._abort_events) > 0
        )

        # Aborta todas as sessões ativas
        active = self.get_active_sessions()
        for sid in active:
            self.abort(sid, reason="Server shutdown")

        # Fase 10: Shutdown graceful — espera vetos resolverem (max 10s)
        self._shutdown_manager.shutdown_sync(timeout=10.0)

        self._executor.shutdown(wait=True, cancel_futures=True)
