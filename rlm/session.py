"""
RLMSession — Sessão conversacional sobre RLM.

Resolve o problema central: cada `completion()` reconstruía `message_history`
do zero. Aqui, o histórico de turnos é mantido externamente, compactado
automaticamente quando cresce, e injetado como contexto em cada novo turno.

Uso básico:
    session = RLMSession(backend="openai", backend_kwargs={"model_name": "gpt-4o-mini"})
    resp = session.chat("Olá, vamos criar um app de entregas")
    resp = session.chat("Qual o próximo passo?")
    resp = session.chat("Lembra o que discutimos no início?")

O LLM sempre vê um contexto limpo ≤ max_context_tokens, com resumo do
que já foi compactado. Compactação acontece em background thread durante
o tempo em que o usuário está digitando.
"""
from __future__ import annotations

import os
import queue as _queue_mod
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from rlm.core.compaction import ContextCompactor, CompactionConfig, estimate_tokens
from rlm.core.types import ClientBackend
from rlm.core.control_flow import ReentrancyBarrier
from rlm.core.cancellation import CancellationToken

# Pipeline de memória expandido (importação lazy nas classes para evitar circular)
# TurnTelemetryStore, MemorySessionCache, inject_memory_with_budget


@dataclass
class SessionTurn:
    user: str
    assistant: str
    elapsed_s: float = 0.0


@dataclass
class SessionState:
    """Estado completo da sessão, serializável."""
    turns: list[SessionTurn] = field(default_factory=list)
    compacted_summary: str = ""          # Resumo gerado de turnos já compactados
    compacted_turn_count: int = 0        # Quantos turnos estão no resumo
    total_turns: int = 0


# ---------------------------------------------------------------------------
# SessionAsyncHandle — handle de um turno rodando em background
# ---------------------------------------------------------------------------

class SessionAsyncHandle:
    """
    Handle retornado por ``RLMSession.chat_async()``.

    O turno roda em daemon thread. O chamador pode:
        handle.is_done          → True se o turno terminou
        handle.result()         → bloqueia e retorna resposta final
        handle.log_poll()       → lê mensagens parent_log() do RLM filho
        handle.elapsed_s        → segundos desde o início

    Uso típico (pai conversacional):
        h = session.chat_async("Analisa /dados/vendas.csv")
        # ... responde ao usuário que o processo começou ...
        while not h.is_done:
            for msg in h.log_poll():
                print(f"[progresso] {msg}")
            time.sleep(0.5)
        resposta = h.result()
    """

    def __init__(self, session: "RLMSession", user_message: str) -> None:
        self._session = session
        self.user_message = user_message
        self._result_holder: list[str] = []
        self._error_holder: list[BaseException] = []
        self._log_queue: _queue_mod.Queue[str] = _queue_mod.Queue()
        self._started_at = time.perf_counter()

        # Injeta a log_queue na sessão temporariamente para que o RLM filho
        # consiga publicar via parent_log — passa pela environment_kwargs
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="session-async-turn",
        )
        self._thread.start()

    def _run(self) -> None:
        # Captura antes do try para garantir que finally sempre restaura
        orig_kwargs = self._session._rlm.environment_kwargs or {}
        try:
            # Injeta _parent_log_queue para que o RLM (via sub_rlm_async interno)
            # possa fazer parent_log() e as msgs chegarem aqui
            patched = dict(orig_kwargs)
            patched["_parent_log_queue"] = self._log_queue
            self._session._rlm.environment_kwargs = patched

            response = self._session.chat(self.user_message)
            self._result_holder.append(response)
        except Exception as exc:  # noqa: BLE001
            self._error_holder.append(exc)
        finally:
            # Restaura kwargs originais
            self._session._rlm.environment_kwargs = orig_kwargs

    @property
    def is_done(self) -> bool:
        """True se o turno terminou (com sucesso ou erro)."""
        return not self._thread.is_alive()

    @property
    def elapsed_s(self) -> float:
        return time.perf_counter() - self._started_at

    def result(self, timeout_s: float = 600.0) -> str:
        """
        Bloqueia até o turno terminar e retorna a resposta final.

        Args:
            timeout_s: Máximo de segundos a esperar. Default 600s.

        Returns:
            Resposta textual do RLM.

        Raises:
            TimeoutError: Se o turno não terminar no prazo.
            RuntimeError: Se o turno falhou com exceção.
        """
        if not self.is_done:
            self._thread.join(timeout=timeout_s)
        if self._thread.is_alive():
            raise TimeoutError(
                f"session.chat_async: turno não terminou em {timeout_s:.0f}s. "
                "Use poll_logs() para ver o progresso parcial."
            )
        if self._error_holder:
            raise RuntimeError(
                f"session.chat_async: turno falhou: {self._error_holder[0]}"
            ) from self._error_holder[0]
        if not self._result_holder:
            raise RuntimeError("session.chat_async: turno não retornou resposta.")
        return self._result_holder[0]

    def log_poll(self) -> list[str]:
        """
        Lê mensagens de progresso publicadas pelo RLM filho (não bloqueia).

        O RLM filho publica via ``parent_log("msg")`` no seu REPL.

        Returns:
            Lista de strings (pode ser vazia).
        """
        msgs: list[str] = []
        while True:
            try:
                msgs.append(self._log_queue.get_nowait())
            except _queue_mod.Empty:
                break
        return msgs

    def __repr__(self) -> str:
        status = "done" if self.is_done else f"running {self.elapsed_s:.1f}s"
        return f"<SessionAsyncHandle {status} msg={self.user_message[:40]!r}>"


class RLMSession:
    """
    Sessão conversacional que mantém contexto entre chamadas `completion()`.

    Arquitetura:
      - Fast lane: últimos `max_hot_turns` turnos passados inline como contexto
      - Cold lane: turnos antigos compactados em `_state.compacted_summary`
      - Background: compactação roda em daemon thread entre turnos (não bloqueia)

    Args:
        backend: Backend LLM (ex: "openai", "ollama").
        backend_kwargs: Kwargs passados ao backend (model_name etc).
        max_hot_turns: Quantos turnos recentes ficam no contexto quente. Default 3.
        max_context_tokens: Limiar para disparar compactação. Default 3000.
        rlm_max_iterations: Iterações internas do RLM por turno. Default 4.
        rlm_kwargs: Kwargs extras para o RLM (system_prompt, verbose, etc).
    """

    def __init__(
        self,
        backend: ClientBackend = "openai",
        backend_kwargs: dict[str, Any] | None = None,
        max_hot_turns: int = 3,
        max_context_tokens: int = 3000,
        rlm_max_iterations: int = 4,
        memory_db_path: str | None = None,
        session_id: str = "",
        state_dir: str | None = None,
        **rlm_kwargs: Any,
    ):
        # Lazy import para evitar circular no nível de módulo
        from rlm.core.rlm import RLM

        self._rlm = RLM(
            backend=backend,
            backend_kwargs=backend_kwargs,
            persistent=True,
            max_iterations=rlm_max_iterations,
            **rlm_kwargs,
        )

        # ── Runtime infrastructure awareness ─────────────────────────────────
        # Injeta caminhos reais no system prompt para que o agente saiba onde
        # seus dados persistentes vivem no disco (evita "eu não tenho memória").
        _effective_state_dir = state_dir or (
            os.path.dirname(memory_db_path) if memory_db_path else None
        )
        _effective_memory_db = memory_db_path or "rlm_memory_v2.db"
        if _effective_state_dir:
            infra_block = (
                "\n\n--- RUNTIME INFRASTRUCTURE ---\n"
                f"session_id: {session_id or 'anonymous'}\n"
                f"state_dir: {os.path.abspath(_effective_state_dir)}\n"
                f"memory_db: {os.path.abspath(_effective_memory_db)}\n"
                "Your persistent storage lives at state_dir. Files there survive across turns and restarts.\n"
                "memory_db is a SQLite FTS5 + vector database with your long-term conversational memories.\n"
                "Use session_memory_search/session_memory_status to query it, or fs_read/fs_ls to inspect the directory.\n"
                "You also have a GLOBAL Knowledge Base (cross-session). Relevant knowledge is auto-injected into your prompt.\n"
                "Use kb_search(query) to find persistent knowledge, kb_get_full_context(doc_id) for deep details, kb_status() for stats.\n"
                "--- END RUNTIME INFRASTRUCTURE ---"
            )
            self._rlm.system_prompt = self._rlm.system_prompt.rstrip() + infra_block
        self._state = SessionState()
        self._max_hot_turns = max_hot_turns
        self._compactor = ContextCompactor(
            CompactionConfig(
                max_history_tokens=max_context_tokens,
                preserve_last_n=0,       # Gerenciamos preservação manualmente
                summary_max_tokens=600,
            )
        )
        self._compact_lock = threading.Lock()
        self._compact_thread: threading.Thread | None = None
        self._compaction_barrier = ReentrancyBarrier()

        # Identidade da sessão — isola memórias de longo prazo por sessão
        self._session_id: str = session_id or str(uuid.uuid4())

        # Memória de longo prazo (opcional — nunca trava o init)
        self._memory: Any = None
        try:
            from rlm.core.memory_manager import MultiVectorMemory
            self._memory = MultiVectorMemory(db_path=memory_db_path or "rlm_memory_v2.db")
        except Exception:
            pass

        # Global Knowledge Base — cross-session persistent memory
        self._kb: Any = None
        self._memory_db_path: str = memory_db_path or "rlm_memory_v2.db"
        try:
            from rlm.core.knowledge_base import GlobalKnowledgeBase
            _kb_dir = os.path.join(
                os.path.dirname(_effective_state_dir), "global"
            ) if _effective_state_dir else None
            if _kb_dir:
                os.makedirs(_kb_dir, exist_ok=True)
                self._kb = GlobalKnowledgeBase(
                    db_path=os.path.join(_kb_dir, "knowledge_base.db"),
                )
        except Exception:
            pass

        # ObsidianBridge — synced materialized view of KB (optional)
        self._obsidian_bridge: Any = None
        try:
            vault_path = os.environ.get("ARKHE_VAULT_PATH", "")
            if vault_path and self._kb is not None:
                from rlm.core.obsidian_bridge import ObsidianBridge
                self._obsidian_bridge = ObsidianBridge(
                    vault_path=vault_path, kb=self._kb
                )
                self._kb.register_hook(self._obsidian_bridge.handle_kb_event)
        except Exception:
            pass

        # Telemetria de turno — rastreia tokens, latência, memória injetada
        self._telemetry: Any = None
        try:
            from rlm.core.turn_telemetry import TurnTelemetryStore
            _model_name = (backend_kwargs or {}).get("model_name", backend)
            self._telemetry = TurnTelemetryStore(
                session_id=self._session_id,
                model_name=str(_model_name),
            )
        except Exception:
            pass

        # Cache quente de memória — leitura síncrona <1ms, atualização em background
        self._memory_cache: Any = None
        try:
            from rlm.core.memory_hot_cache import get_or_create_cache
            self._memory_cache = get_or_create_cache(self._session_id)
        except Exception:
            pass

        # Fase 12: Stream ativo (completion_stream generator) — sobrevive entre turnos
        self._active_stream: Any = None
        self._last_injection_meta = self._empty_injection_meta()
        self._memory_injection_pending = False

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def chat(self, user_message: str) -> str:
        """
        Envia uma mensagem ao RLM e recebe a resposta.

        O contexto de turnos anteriores é injetado automaticamente.
        Memórias relevantes de longo prazo são injetadas via budget gate tripartito.
        Compactação de turnos antigos roda em background após cada turno.
        Telemetria de tokens/latência/memória é registrada em JSONL.
        """
        # ── 1. Telemetria: marca início do turno ────────────────────────────
        tel = self.start_turn_telemetry(user_message)

        t_start = time.perf_counter()
        self._record_recursive_message("user", user_message, metadata={"source": "chat"})

        # ── 2. Monta prompt com memória injetada via budget gate ─────────────
        prompt = self._build_prompt(user_message)

        # ── 3. Executa o RLM ─────────────────────────────────────────────────
        completion = self._rlm.completion(prompt)
        response = completion.response

        elapsed = time.perf_counter() - t_start

        self._record_recursive_message(
            "assistant",
            response,
            metadata={"source": "chat", "elapsed_s": elapsed},
        )

        # Acumula turno
        self._state.turns.append(SessionTurn(user_message, response, elapsed))
        self._state.total_turns += 1

        # ── 4. Finaliza telemetria ───────────────────────────────────────────
        self.finish_turn_telemetry(tel, completion=completion, compaction_triggered=False)

        # ── 5. Background: salva turno na memória + atualiza cache ───────────
        self.schedule_post_turn_memory(user_message, response)

        # ── 6. Dispara compactação em background se necessário ───────────────
        self._compact_background_if_needed()

        return response

    @property
    def turns(self) -> list[SessionTurn]:
        return list(self._state.turns)

    @property
    def summary(self) -> str:
        """Retorna o resumo compactado do histórico antigo, se houver."""
        return self._state.compacted_summary

    @property
    def memory(self) -> Any:
        """MultiVectorMemory da sessão (pode ser None se init falhou)."""
        return self._memory

    @property
    def kb(self) -> Any:
        """GlobalKnowledgeBase cross-session (pode ser None)."""
        return self._kb

    @property
    def session_id(self) -> str:
        """ID único desta sessão."""
        return self._session_id

    @property
    def telemetry(self) -> Any:
        """TurnTelemetryStore desta sessão (pode ser None)."""
        return self._telemetry

    def reset(self) -> None:
        """Zera histórico e resumo. Mantém o RLM persistente ativo."""
        self._state = SessionState()
        self._last_injection_meta = self._empty_injection_meta()
        self._memory_injection_pending = False
        if self._memory_cache is not None:
            try:
                self._memory_cache.invalidate()
            except Exception:
                pass
        # Fase 12: Fecha stream ativo se houver
        if self._active_stream is not None:
            try:
                self._active_stream.close()
            except (StopIteration, GeneratorExit):
                pass
            self._active_stream = None

    def chat_async(self, user_message: str) -> "SessionAsyncHandle":
        """
        Inicia um turno de conversa em background e retorna imediatamente.

        O RLM processa a mensagem em daemon thread. O chamador pode continuar
        fazendo outras coisas (responder o usuário com status, lançar mais
        tarefas, etc.) e chamar handle.result() quando precisar da resposta.

        Útil para o padrão "pai conversacional":
            h = session.chat_async("Analisa os dados de vendas de 2025")
            # ... responde ao usuário que a análise começou ...
            while not h.is_done:
                logs = h.log_poll()
                if logs:
                    entregar_ao_usuario(logs)
                time.sleep(0.2)
            resposta = h.result()

        Returns:
            SessionAsyncHandle — verifique .is_done, leia .log_poll(),
            bloqueie em .result() quando precisar da resposta final.
        """
        return SessionAsyncHandle(session=self, user_message=user_message)

    def chat_stream(self, user_message: str) -> str:
        """
        Fase 12 — Solução B: Chat via completion_stream (generator).

        Na primeira chamada, inicia o generator. Nas chamadas seguintes,
        reutiliza o MESMO generator — o environment, lm_handler, variáveis
        REPL, tudo permanece vivo entre turnos. Zero zona morta.

        Se o generator terminar (ex: erro), cria um novo automaticamente.

        Uso::

            session = RLMSession(backend="openai", backend_kwargs={"model_name": "gpt-4o-mini"})
            resp = session.chat_stream("Cria uma função parse_log()")
            # → lm_handler FICA VIVO, REPL mantém a função criada
            resp = session.chat_stream("Agora usa parse_log() no arquivo X")
            # → parse_log() ainda existe no REPL, sem reconstrução
            session.close()  # encerra o generator graciosamente
        """
        t_start = time.perf_counter()
        self._record_recursive_message("user", user_message, metadata={"source": "chat_stream"})

        try:
            if self._active_stream is None:
                # Primeiro turno: cria o generator com contexto da sessão
                prompt = self._build_prompt(user_message)
                self._active_stream = self._rlm.completion_stream(prompt)
                completion = next(self._active_stream)
            else:
                # Turnos seguintes: send() no generator existente
                completion = self._active_stream.send(user_message)
        except (StopIteration, GeneratorExit):
            # Generator morreu — fallback para novo generator
            self._active_stream = None
            prompt = self._build_prompt(user_message)
            self._active_stream = self._rlm.completion_stream(prompt)
            completion = next(self._active_stream)

        response = completion.response
        elapsed = time.perf_counter() - t_start

        self._record_recursive_message(
            "assistant",
            response,
            metadata={
                "source": "chat_stream",
                "elapsed_s": elapsed,
                "is_complete": completion.is_complete,
            },
        )

        self._state.turns.append(SessionTurn(user_message, response, elapsed))
        self._state.total_turns += 1
        self._compact_background_if_needed()

        return response



    def poll_logs(self, handles: "list[SessionAsyncHandle]") -> list[str]:
        """
        Lê mensagens de progresso de múltiplos handles sem bloquear.

        Útil para monitorar vários turnos paralelos:
            h1 = session.chat_async("tarefa pesada 1")
            h2 = session.chat_async("tarefa pesada 2")
            while not (h1.is_done and h2.is_done):
                msgs = session.poll_logs([h1, h2])
                for m in msgs:
                    print(m)
                time.sleep(0.5)

        Returns:
            Lista de strings com todas as mensagens disponíveis dos handles.
        """
        all_msgs: list[str] = []
        for h in handles:
            all_msgs.extend(h.log_poll())
        return all_msgs

    def queue_recursive_command(
        self,
        command_type: str,
        payload: dict[str, Any] | None = None,
        *,
        status: str = "queued",
        branch_id: int | None = None,
    ) -> dict[str, Any] | None:
        env = self._persistent_env
        if env is None or not hasattr(env, "queue_recursive_command"):
            return None
        return env.queue_recursive_command(
            command_type,
            payload=payload,
            status=status,
            branch_id=branch_id,
        )

    def update_recursive_command(
        self,
        command_id: int,
        *,
        status: str,
        outcome: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        env = self._persistent_env
        if env is None or not hasattr(env, "update_recursive_command"):
            return None
        return env.update_recursive_command(
            command_id,
            status=status,
            outcome=outcome,
        )

    def recent_recursive_messages(
        self,
        *,
        limit: int = 20,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        env = self._persistent_env
        if env is None or not hasattr(env, "recent_recursive_messages"):
            return []
        return env.recent_recursive_messages(limit=limit, role=role)

    def recent_recursive_commands(
        self,
        *,
        limit: int = 20,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        env = self._persistent_env
        if env is None or not hasattr(env, "recent_recursive_commands"):
            return []
        return env.recent_recursive_commands(limit=limit, status=status)

    def recent_recursive_events(
        self,
        *,
        limit: int = 20,
        event_type: str | None = None,
        branch_id: int | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        env = self._persistent_env
        if env is None or not hasattr(env, "recent_recursive_events"):
            return []
        return env.recent_recursive_events(
            limit=limit,
            event_type=event_type,
            branch_id=branch_id,
            source=source,
        )

    def emit_recursive_event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        branch_id: int | None = None,
        source: str = "session",
        visibility: str = "internal",
        correlation_id: str | None = None,
    ) -> dict[str, Any] | None:
        env = self._persistent_env
        if env is None or not hasattr(env, "emit_recursive_event"):
            return None
        return env.emit_recursive_event(
            event_type,
            payload=payload,
            branch_id=branch_id,
            source=source,
            visibility=visibility,
            correlation_id=correlation_id,
        )

    def recursive_session_state(self) -> dict[str, Any]:
        env = self._persistent_env
        if env is None or not hasattr(env, "get_recursive_session_state"):
            return {
                "message_count": 0,
                "command_count": 0,
                "event_count": 0,
                "queued_commands": 0,
                "latest_message": None,
                "latest_command": None,
                "latest_event": None,
            }
        return env.get_recursive_session_state()

    # ──────────────────────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────────────────────

    def _record_recursive_message(
        self,
        role: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        env = self._persistent_env
        if env is None or not hasattr(env, "record_recursive_message"):
            return None
        try:
            return env.record_recursive_message(role, content, metadata=metadata)
        except Exception:
            return None

    @staticmethod
    def _empty_injection_meta() -> dict[str, Any]:
        return {
            "query": None,
            "retrieved": 0,
            "injected": 0,
            "tokens": 0,
            "budget_pct": 0.0,
        }

    def _stage_memory_injection_meta(
        self,
        *,
        query_text: str | None,
        retrieved: int = 0,
        injected: int = 0,
        tokens: int = 0,
        budget_pct: float = 0.0,
    ) -> None:
        self._last_injection_meta = {
            "query": query_text,
            "retrieved": retrieved,
            "injected": injected,
            "tokens": tokens,
            "budget_pct": budget_pct,
        }
        self._memory_injection_pending = True

    def _consume_memory_injection_meta(self) -> dict[str, Any]:
        if not self._memory_injection_pending:
            return self._empty_injection_meta()

        injected = dict(self._last_injection_meta)
        self._last_injection_meta = self._empty_injection_meta()
        self._memory_injection_pending = False
        return injected

    def start_turn_telemetry(self, query_text: str | None = None) -> Any | None:
        if query_text is not None:
            pending_query = self._last_injection_meta.get("query")
            if pending_query != query_text:
                self._last_injection_meta = self._empty_injection_meta()
                self._memory_injection_pending = False

        if self._telemetry is None:
            return None
        try:
            return self._telemetry.start_turn()
        except Exception:
            return None

    def finish_turn_telemetry(
        self,
        tel: Any | None,
        *,
        completion: Any | None = None,
        compaction_triggered: bool = False,
    ) -> None:
        if tel is None or self._telemetry is None:
            self._consume_memory_injection_meta()
            return

        try:
            injected = self._consume_memory_injection_meta()
            self._telemetry.finish_turn(
                tel,
                completion=completion,
                memory_chunks_retrieved=injected.get("retrieved", 0),
                memory_chunks_injected=injected.get("injected", 0),
                memory_tokens_injected=injected.get("tokens", 0),
                memory_budget_used_pct=injected.get("budget_pct", 0.0),
                compaction_triggered=compaction_triggered,
            )
        except Exception:
            pass

    def _select_memory_chunks(
        self,
        query_text: str,
        *,
        available_tokens: int,
    ) -> tuple[list[dict[str, Any]], int]:
        if self._memory is None or not query_text:
            return [], 0

        cached_chunks: list[dict[str, Any]] = []
        if self._memory_cache is not None:
            try:
                cached_chunks = self._memory_cache.read_sync()
            except Exception:
                cached_chunks = []

        if cached_chunks:
            selected_chunks = cached_chunks
            tokens_used = sum(
                max(1, int(len(chunk.get("content", "")) * 0.25))
                for chunk in selected_chunks
            )
            return selected_chunks, tokens_used

        from rlm.core.memory_budget import inject_memory_with_budget

        selected_chunks, tokens_used = inject_memory_with_budget(
            query=query_text,
            session_id=self._session_id,
            memory_manager=self._memory,
            available_tokens=available_tokens,
        )

        if selected_chunks and self._memory_cache is not None:
            try:
                with self._memory_cache._lock:
                    self._memory_cache.chunks = list(selected_chunks)
                    self._memory_cache.last_updated = time.time()
            except Exception:
                pass

        return selected_chunks, tokens_used

    def build_memory_block(self, query_text: str, *, available_tokens: int) -> str:
        self._stage_memory_injection_meta(query_text=query_text)

        selected_chunks, tokens_used = self._select_memory_chunks(
            query_text,
            available_tokens=available_tokens,
        )
        if not selected_chunks:
            return ""

        from rlm.core.memory_budget import format_memory_block

        mem_block = format_memory_block(selected_chunks)
        if not mem_block:
            return ""

        budget_pct = round(tokens_used / max(available_tokens, 1), 4)
        self._stage_memory_injection_meta(
            query_text=query_text,
            retrieved=len(selected_chunks),
            injected=len(selected_chunks),
            tokens=tokens_used,
            budget_pct=budget_pct,
        )
        return mem_block

    def inject_memory_prompt(
        self,
        prompt: Any,
        query_text: str,
        *,
        available_tokens: int = 2500,
    ) -> Any:
        mem_block = self.build_memory_block(query_text, available_tokens=available_tokens)
        if not mem_block:
            return prompt

        if isinstance(prompt, str):
            return mem_block + "\n\n" + prompt
        if isinstance(prompt, list):
            return [{"role": "system", "content": mem_block}] + list(prompt)
        return prompt

    def schedule_post_turn_memory(self, user_message: str, assistant_response: str) -> None:
        if not user_message or not assistant_response:
            return
        threading.Thread(
            target=self._post_turn_async,
            args=(user_message, assistant_response),
            daemon=True,
            name="rlm-memory-post-turn",
        ).start()

    def _release_memory_runtime(self) -> None:
        self._last_injection_meta = self._empty_injection_meta()
        self._memory_injection_pending = False
        if self._memory_cache is not None:
            try:
                from rlm.core.memory_hot_cache import evict_cache

                evict_cache(self._session_id)
            except Exception:
                pass
            self._memory_cache = None

        if self._memory is not None:
            try:
                close_fn = getattr(self._memory, "close", None)
                if callable(close_fn):
                    close_fn()
            except Exception:
                pass
            self._memory = None

    def _consolidate_to_kb(self) -> None:
        """Consolida nuggets da sessão na Knowledge Base global (síncrono, no close)."""
        if self._kb is None:
            return
        try:
            from rlm.core.knowledge_consolidator import consolidate_session
            consolidate_session(
                memory_db_path=self._memory_db_path,
                session_id=self._session_id,
                kb=self._kb,
            )
        except Exception:
            pass  # Consolidação nunca impede o fechamento da sessão

    def _retrieve_from_kb(self, query: str, max_tokens: int = 1200) -> str:
        """
        Recuperação progressiva da Knowledge Base cross-session.
        Retorna bloco formatado com título + sumário dos docs mais relevantes.
        Contexto completo fica disponível via kb_get_full_context(doc_id).
        """
        if self._kb is None:
            return ""
        results = self._kb.search_hybrid(query, limit=5)
        if not results:
            return ""

        lines = ["[CONHECIMENTO PERSISTENTE — memória cross-session]"]
        tokens_used = estimate_tokens(lines[0])
        for doc in results:
            entry = (
                f"• {doc['title']} (imp: {doc.get('importance', 0):.2f}, "
                f"id: {doc['id'][:8]}) — {doc.get('summary', '')}"
            )
            entry_tokens = estimate_tokens(entry)
            if tokens_used + entry_tokens > max_tokens:
                break
            lines.append(entry)
            tokens_used += entry_tokens

        if len(lines) <= 1:
            return ""

        lines.append("[Para contexto completo: kb_get_full_context(doc_id)]")
        lines.append("[FIM CONHECIMENTO PERSISTENTE]")
        return "\n".join(lines)

    def _build_prompt(self, user_message: str) -> str:
        """
        Monta o prompt completo para o RLM, incluindo:
          - Memórias relevantes de longo prazo via budget gate tripartito
          - Resumo de turnos compactados (se houver)
          - Últimos max_hot_turns turnos completos (fast lane)
          - Mensagem atual do usuário

        Estratégia de memória:
          1. Tenta ler do hot cache (síncrono, <1ms) — chunks do turno anterior
          2. Se cache vazio (primeiro turno), faz busca direta via inject_memory_with_budget
          3. Metadados de injeção são salvos em self._last_injection_meta para telemetria
        """
        parts: list[str] = []

        # ── Conhecimento Persistente (cross-session KB) ──────────────────────
        if self._kb is not None:
            try:
                kb_block = self._retrieve_from_kb(user_message)
                if kb_block:
                    parts.append(kb_block)
            except Exception:
                pass  # KB nunca trava o prompt

        # ── Memórias de longo prazo via budget gate tripartito ───────────────
        if self._memory is not None:
            try:
                # Estimativa de tokens disponíveis para o contexto atual
                hot_text = " ".join(f"{t.user} {t.assistant}" for t in self._state.turns[-self._max_hot_turns:])
                summary_tokens = estimate_tokens(self._state.compacted_summary)
                hot_tokens = estimate_tokens(hot_text)
                user_tokens = estimate_tokens(user_message)
                # Assume janela de 8k tokens — ajusta baseado no uso atual
                available_tokens = max(1000, 8000 - summary_tokens - hot_tokens - user_tokens - 200)
                mem_block = self.build_memory_block(
                    user_message,
                    available_tokens=available_tokens,
                )
                if mem_block:
                    parts.append(mem_block)

            except Exception:
                # Memória nunca pode travar o prompt — fallback silencioso
                pass

        # ── Resumo de turnos antigos (cold lane) ─────────────────────────────
        if self._state.compacted_summary:
            parts.append(
                f"[HISTÓRICO ANTERIOR COMPACTADO — {self._state.compacted_turn_count} turnos]\n"
                f"{self._state.compacted_summary}\n"
                f"[FIM DO HISTÓRICO COMPACTADO]"
            )

        # ── Turnos recentes (fast lane) ───────────────────────────────────────
        hot_turns = self._state.turns[-self._max_hot_turns:]
        for t in hot_turns:
            parts.append(f"Usuário: {t.user}")
            parts.append(f"Assistente: {t.assistant}")

        # ── Mensagem atual ────────────────────────────────────────────────────
        parts.append(f"Usuário: {user_message}")

        return "\n\n".join(parts)

    def _post_turn_async(self, user_message: str, assistant_response: str) -> None:
        """
        Executado em daemon thread após cada turno. Responsabilidades:
          1. Extrai "nuggets" memorizáveis do turno via GPT-4.1-nano (mini agent)
          2. Avalia a importância de cada nugget (mini agent)
          3. Salva nuggets com importance_score no MultiVectorMemory
          4. Detecta relações (contradicts/extends/updates) com memórias recentes
          5. Agenda atualização do hot cache para o próximo turno

        Falha silenciosa total — nunca propaga exceção.
        """
        if self._memory is None:
            return

        try:
            from rlm.core.memory_mini_agent import (
                extract_memory_nuggets,
                assign_importance,
                identify_edge,
            )

            # 1. Extrai nuggets do turno
            nuggets = extract_memory_nuggets(user_message, assistant_response)

            if nuggets:
                # 2. Para cada nugget: avalia importância + salva
                saved_ids: list[str] = []
                for nugget in nuggets:
                    if not nugget or not nugget.strip():
                        continue
                    importance = assign_importance(nugget)
                    memory_id = self._memory.add_memory(
                        session_id=self._session_id,
                        content=nugget,
                        metadata={
                            "source": "mini_agent",
                            "turn": self._state.total_turns,
                            "model": "gpt-4.1-nano",
                        },
                        importance_score=importance,
                    )
                    saved_ids.append(memory_id)

                # 3. Detecta arestas entre nuggets novos e memórias recentes
                # (apenas entre os nuggets recém-salvos — evita O(n²) em toda a memória)
                if len(saved_ids) > 1:
                    try:
                        for i in range(len(saved_ids) - 1):
                            from_id = saved_ids[i]
                            to_id = saved_ids[i + 1]
                            from_content = nuggets[i]
                            to_content = nuggets[i + 1]
                            edge_type = identify_edge(from_content, to_content)
                            if edge_type is not None:
                                self._memory.add_edge(from_id, to_id, edge_type)
                                # Se a nova memória contradiz a anterior, depreca a antiga
                                if edge_type in ("contradicts", "updates"):
                                    self._memory.deprecate(from_id)
                    except Exception:
                        pass  # grafo de arestas nunca bloqueia o salvamento

        except Exception:
            pass  # extração de nuggets nunca bloqueia nada

        # 4. Agenda atualização do hot cache para o próximo turno
        if self._memory_cache is not None:
            try:
                self._memory_cache.schedule_update(
                    query=user_message,
                    memory_manager=self._memory,
                    available_tokens=8000,
                )
            except Exception:
                pass

    def _compact_background_if_needed(self) -> None:
        """
        Verifica se turnos antigos precisam ser movidos para o resumo compactado.
        Se sim, dispara compactação em daemon thread (não bloqueia o usuário).
        """
        # Não inicia nova thread se já há uma rodando
        if self._compact_thread and self._compact_thread.is_alive():
            return

        # Mede tokens do histórico quente atual
        hot_turns = self._state.turns[-self._max_hot_turns:]
        hot_text = " ".join(f"{t.user} {t.assistant}" for t in hot_turns)
        if estimate_tokens(hot_text) < 1200:
            return  # Ainda confortável, sem necessidade

        # Turnos além da janela quente são candidatos à compactação
        cold_candidates = self._state.turns[:-self._max_hot_turns]
        if len(cold_candidates) < 2:
            return  # Poucos turnos, não vale

        self._compact_thread = threading.Thread(
            target=self._run_compaction,
            args=(cold_candidates,),
            daemon=True,
            name="rlm-session-compactor",
        )
        self._compact_thread.start()

    def _run_compaction(self, turns: list[SessionTurn]) -> None:
        """
        Roda em background. Gera resumo dos turnos antigos via LLM e
        atualiza `_state.compacted_summary`.
        Fase 10: Protegido por ReentrancyBarrier — reentrância é ignorada.
        """
        def _inner():
            self._do_compaction(turns)
        self._compaction_barrier.run_or_skip(_inner)

    def _do_compaction(self, turns: list[SessionTurn]) -> None:
        """Lógica real de compactação, separada para uso pelo barrier."""
        # Converte turnos para formato de mensagem para o compactor
        messages: list[dict] = []
        for t in turns:
            messages.append({"role": "user", "content": t.user})
            messages.append({"role": "assistant", "content": t.assistant})

        if not self._compactor.should_compact(messages):
            return

        try:
            # Cliente throwaway só para resumo — evita acoplar aos internos do RLM
            from typing import cast
            from rlm.clients import get_client
            _backend = cast(ClientBackend, self._rlm.backend)
            _client = get_client(_backend, self._rlm.backend_kwargs or {})

            def _llm_fn(prompt: Any) -> str:
                return _client.completion(str(prompt) if not isinstance(prompt, str) else prompt)

            compacted = self._compactor.compact(messages, llm_fn=_llm_fn)

            # Extrai o resumo gerado (mensagem de role=system inserida pelo compactor)
            new_summary = ""
            for m in compacted:
                if m.get("role") == "system" and "CONVERSATION SUMMARY" in m.get("content", ""):
                    new_summary = m["content"]
                    break

            if not new_summary:
                return

            with self._compact_lock:
                # Prepend ao resumo existente se já havia um
                if self._state.compacted_summary:
                    self._state.compacted_summary = (
                        self._state.compacted_summary + "\n\n" + new_summary
                    )
                else:
                    self._state.compacted_summary = new_summary

                self._state.compacted_turn_count += len(turns)

                # Remove os turnos compactados (sempre no início da lista).
                # Slice é thread-safe: cria nova lista a partir do estado atual,
                # preservando turnos adicionados por chat() durante a compactação.
                self._state.turns = self._state.turns[len(turns):]

            # Persiste conteúdo compactado na memória de longo prazo
            if self._memory is not None:
                try:
                    combined = "\n---\n".join(
                        f"U: {t.user}\nA: {t.assistant}" for t in turns
                    )
                    self._memory.add_memory(
                        session_id=self._session_id,
                        content=combined[:2000],
                        metadata={"source": "compaction", "turn_count": len(turns)},
                        importance_score=0.6,  # compactações têm importância média-alta
                    )
                except Exception:
                    pass  # memória nunca trava o compactor

        except Exception:
            # Compactação em background nunca pode travar o usuário
            pass

    # ---------------------------------------------------------------------------
    # Proxies — compatibilidade com SessionManager / Supervisor / api.py
    # ---------------------------------------------------------------------------

    def completion(self, prompt: Any, **kwargs: Any):
        """Delega ao RLM interno (usado pelo Supervisor com prompts pré-construídos)."""
        return self._rlm.completion(prompt, **kwargs)

    def save_state(self, state_dir: str) -> None:
        """Persiste o estado REPL em disco (chamado por SessionManager.close_session)."""
        self._rlm.save_state(state_dir)

    def close(self) -> None:
        """Fecha a instância RLM interna (chamado por SessionManager.close_session)."""
        # Consolidação KB — extrai conhecimento da sessão para memória persistente
        self._consolidate_to_kb()

        # Fase 12: Encerra o generator stream ativo antes de fechar o RLM
        if self._active_stream is not None:
            try:
                self._active_stream.close()
            except (StopIteration, GeneratorExit):
                pass
            self._active_stream = None
        self._rlm.shutdown_persistent()
        self._rlm.close()
        self._release_memory_runtime()

    def dispose(self) -> None:
        """Fase 10: Unified cleanup — libera RLM, compactor, cache e memória."""
        self._rlm.dispose()
        self._release_memory_runtime()

    @property
    def _cancel_token(self) -> CancellationToken:
        return self._rlm._cancel_token

    @_cancel_token.setter
    def _cancel_token(self, value: CancellationToken) -> None:
        self._rlm._cancel_token = value

    @property
    def _abort_event(self) -> Any:
        return self._rlm._abort_event

    @_abort_event.setter
    def _abort_event(self, value: Any) -> None:
        self._rlm._abort_event = value

    @property
    def max_iterations(self) -> int:
        return self._rlm.max_iterations

    @max_iterations.setter
    def max_iterations(self, value: int) -> None:
        self._rlm.max_iterations = value

    @property
    def _persistent_env(self) -> Any:
        """Delega acesso ao REPL env para injeção de plugins (api.py)."""
        return getattr(self._rlm, "_persistent_env", None)

    @property
    def skills_context(self) -> str:
        return getattr(self._rlm, "skills_context", "")

    @skills_context.setter
    def skills_context(self, value: str) -> None:
        self._rlm.skills_context = value
