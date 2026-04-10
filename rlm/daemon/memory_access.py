from __future__ import annotations

import threading
from typing import Any

from rlm.core.engine.compaction import estimate_tokens


class DaemonMemoryAccess:
    """Camada explícita de memória do daemon.

    Compõe as memórias já existentes sem substituí-las:
    - RLMSession long-term memory (session/episodic recall)
    - Knowledge Base cross-session (semantic recall)
    - RLMMemory do workspace carregado no LocalREPL (shared/procedural recall)

    A ideia é tirar a orquestração de memória do runtime_pipeline e dar ao
    daemon um ponto único para leitura e ingestão pós-turno.
    """

    def __init__(
        self,
        *,
        prompt_budget_tokens: int = 2500,
        kb_budget_tokens: int = 700,
        workspace_budget_tokens: int = 700,
        workspace_result_limit: int = 3,
    ) -> None:
        self.prompt_budget_tokens = prompt_budget_tokens
        self.kb_budget_tokens = kb_budget_tokens
        self.workspace_budget_tokens = workspace_budget_tokens
        self.workspace_result_limit = workspace_result_limit
        self.lock = threading.RLock()
        self.stats: dict[str, int] = {
            "recall_requests": 0,
            "recall_hits": 0,
            "session_blocks": 0,
            "workspace_blocks": 0,
            "kb_blocks": 0,
            "post_turn_requests": 0,
            "post_turn_delegated": 0,
            "episodic_writes": 0,
            "failures": 0,
        }
        self.last_scope: dict[str, Any] = {}

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                **self.stats,
                "last_scope": dict(self.last_scope),
            }

    def inject_prompt(
        self,
        rlm_session: Any,
        query_text: str,
        prompt: Any,
        *,
        session: Any | None = None,
    ) -> Any:
        if rlm_session is None or not query_text:
            return prompt

        with self.lock:
            self.stats["recall_requests"] += 1

        try:
            memory_block = self.build_memory_block(
                rlm_session,
                query_text,
                session=session,
            )
        except Exception:
            with self.lock:
                self.stats["failures"] += 1
            return prompt

        if not memory_block:
            return prompt

        with self.lock:
            self.stats["recall_hits"] += 1

        if isinstance(prompt, str):
            return memory_block + "\n\n" + prompt
        if isinstance(prompt, list):
            return [{"role": "system", "content": memory_block}] + list(prompt)
        return prompt

    def build_memory_block(
        self,
        rlm_session: Any,
        query_text: str,
        *,
        session: Any | None = None,
    ) -> str:
        available_tokens = max(self.prompt_budget_tokens, 0)
        if available_tokens <= 0:
            return ""

        blocks: list[str] = []

        session_budget = max(available_tokens - self.kb_budget_tokens - self.workspace_budget_tokens, 600)
        session_budget = min(session_budget, available_tokens)
        session_block = self.build_session_block(
            rlm_session,
            query_text,
            available_tokens=session_budget,
        )
        if session_block:
            blocks.append(session_block)
            available_tokens = max(0, available_tokens - estimate_tokens(session_block))
            with self.lock:
                self.stats["session_blocks"] += 1

        workspace_budget = min(self.workspace_budget_tokens, available_tokens)
        workspace_block = self.build_workspace_block(
            rlm_session,
            query_text,
            session=session,
            available_tokens=workspace_budget,
        )
        if workspace_block:
            blocks.append(workspace_block)
            available_tokens = max(0, available_tokens - estimate_tokens(workspace_block))
            with self.lock:
                self.stats["workspace_blocks"] += 1

        kb_budget = min(self.kb_budget_tokens, available_tokens)
        kb_block = self.build_kb_block(
            rlm_session,
            query_text,
            available_tokens=kb_budget,
        )
        if kb_block:
            blocks.append(kb_block)
            with self.lock:
                self.stats["kb_blocks"] += 1

        return "\n\n".join(block for block in blocks if block)

    def build_session_block(
        self,
        rlm_session: Any,
        query_text: str,
        *,
        available_tokens: int,
    ) -> str:
        build_memory_block = getattr(rlm_session, "build_memory_block", None)
        if callable(build_memory_block):
            return str(build_memory_block(query_text, available_tokens=available_tokens) or "")
        return self.build_session_block_fallback(
            rlm_session,
            query_text,
            available_tokens=available_tokens,
        )

    def build_session_block_fallback(
        self,
        rlm_session: Any,
        query_text: str,
        *,
        available_tokens: int,
    ) -> str:
        memory = self.get_session_memory_store(rlm_session)
        if memory is None:
            return ""

        session_id = self.get_session_id(rlm_session)
        memory_cache = getattr(rlm_session, "_memory_cache", None)
        cached_chunks: list[Any] = []
        if memory_cache is not None:
            try:
                cached_chunks = list(memory_cache.read_sync() or [])
            except Exception:
                cached_chunks = []

        if cached_chunks:
            selected_chunks = cached_chunks
        else:
            from rlm.core.memory.memory_budget import inject_memory_with_budget

            selected_chunks, _tokens_used = inject_memory_with_budget(
                query=query_text,
                session_id=session_id,
                memory_manager=memory,
                available_tokens=available_tokens,
            )

        if not selected_chunks:
            return ""

        from rlm.core.memory.memory_budget import format_memory_block

        return str(format_memory_block(selected_chunks) or "")

    def build_kb_block(
        self,
        rlm_session: Any,
        query_text: str,
        *,
        available_tokens: int,
    ) -> str:
        if available_tokens <= 0:
            return ""
        retrieve_from_kb = getattr(rlm_session, "_retrieve_from_kb", None)
        if not callable(retrieve_from_kb):
            return ""
        try:
            return str(retrieve_from_kb(query_text, max_tokens=available_tokens) or "")
        except Exception:
            with self.lock:
                self.stats["failures"] += 1
            return ""

    def build_workspace_block(
        self,
        rlm_session: Any,
        query_text: str,
        *,
        session: Any | None = None,
        available_tokens: int,
    ) -> str:
        if available_tokens <= 0:
            return ""

        workspace_memory = self.get_workspace_memory(rlm_session)
        if workspace_memory is None:
            return ""

        search = getattr(workspace_memory, "search", None)
        if not callable(search):
            return ""

        try:
            raw_results = search(query_text)
        except Exception:
            with self.lock:
                self.stats["failures"] += 1
            return ""

        if not isinstance(raw_results, list) or not raw_results:
            return ""

        scope = self.collect_scope(rlm_session, session=session, workspace_memory=workspace_memory)
        scope_id = str(
            getattr(workspace_memory, "scope_id", "")
            or getattr(workspace_memory, "session_id", "")
            or "workspace"
        )
        header = f"[MEMORIA DE WORKSPACE — {scope_id}]"
        footer = "[FIM MEMORIA DE WORKSPACE]"

        lines = [header]
        tokens_used = estimate_tokens(header)

        scope_line = self.format_scope_line(scope)
        if scope_line:
            scope_tokens = estimate_tokens(scope_line)
            if tokens_used + scope_tokens <= available_tokens:
                lines.append(scope_line)
                tokens_used += scope_tokens

        added = 0
        for item in raw_results[: self.workspace_result_limit]:
            if not isinstance(item, dict):
                continue
            if item.get("error"):
                continue
            key = str(item.get("key", "") or "-")
            kind = str(item.get("type", "unknown") or "unknown")
            preview = " ".join(str(item.get("preview", "") or "").split())
            if not preview:
                continue
            line = f"• {key} [{kind}] — {preview[:180]}"
            line_tokens = estimate_tokens(line)
            if tokens_used + line_tokens + estimate_tokens(footer) > available_tokens:
                break
            lines.append(line)
            tokens_used += line_tokens
            added += 1

        if added == 0:
            return ""

        lines.append(footer)

        with self.lock:
            self.last_scope = scope

        return "\n".join(lines)

    def get_session_memory_store(self, rlm_session: Any) -> Any | None:
        return getattr(rlm_session, "memory", None) or getattr(rlm_session, "_memory", None)

    def get_session_id(self, rlm_session: Any) -> str:
        return str(
            getattr(rlm_session, "session_id", "")
            or getattr(rlm_session, "_session_id", "")
            or ""
        )

    def get_workspace_memory(self, rlm_session: Any) -> Any | None:
        rlm_core = getattr(rlm_session, "_rlm", None)
        live_env = getattr(rlm_core, "_persistent_env", None)
        workspace_memory = getattr(live_env, "_memory", None)
        if workspace_memory is None:
            return None

        scope_kind = str(getattr(workspace_memory, "scope_kind", "") or "")
        scope_id = str(
            getattr(workspace_memory, "scope_id", "")
            or getattr(workspace_memory, "session_id", "")
            or ""
        )
        if scope_kind == "workspace" or scope_id.startswith("workspace::"):
            return workspace_memory
        return None

    def collect_scope(
        self,
        rlm_session: Any,
        *,
        session: Any | None = None,
        workspace_memory: Any | None = None,
    ) -> dict[str, Any]:
        scope: dict[str, Any] = {
            "session_id": str(getattr(rlm_session, "session_id", "") or getattr(rlm_session, "_session_id", "") or ""),
            "channel": "",
            "actor": "",
            "active_channels": (),
            "workspace_scope": "",
            "agent_depth": None,
            "branch_id": None,
            "agent_role": "",
            "parent_session_id": "",
        }

        metadata = dict(getattr(session, "metadata", {}) or {}) if session is not None else {}
        channel_context = metadata.get("_channel_context") if isinstance(metadata.get("_channel_context"), dict) else {}
        active_channels = metadata.get("_active_channels") if isinstance(metadata.get("_active_channels"), list) else []
        if active_channels:
            scope["active_channels"] = tuple(str(item) for item in active_channels if str(item).strip())

        if isinstance(channel_context, dict):
            scope["channel"] = str(channel_context.get("channel", "") or "")
            scope["actor"] = str(channel_context.get("actor", "") or "")

        rlm_core = getattr(rlm_session, "_rlm", None)
        live_env = getattr(rlm_core, "_persistent_env", None)
        if not scope["channel"]:
            scope["channel"] = str(getattr(live_env, "_originating_channel", "") or "")

        workspace_memory = workspace_memory or self.get_workspace_memory(rlm_session)
        if workspace_memory is not None:
            scope["workspace_scope"] = str(
                getattr(workspace_memory, "scope_id", "")
                or getattr(workspace_memory, "session_id", "")
                or ""
            )
            agent_context = getattr(workspace_memory, "_agent_context", None)
            if agent_context is not None:
                scope["agent_depth"] = getattr(agent_context, "depth", None)
                scope["branch_id"] = getattr(agent_context, "branch_id", None)
                scope["agent_role"] = str(getattr(agent_context, "role", "") or "")
                scope["parent_session_id"] = str(getattr(agent_context, "parent_session_id", "") or "")
                if not scope["channel"]:
                    scope["channel"] = str(getattr(agent_context, "channel", "") or "")

        return scope

    def format_scope_line(self, scope: dict[str, Any]) -> str:
        parts: list[str] = []
        channel = str(scope.get("channel", "") or "")
        if channel:
            parts.append(f"channel={channel}")

        actor = str(scope.get("actor", "") or "")
        if actor:
            parts.append(f"actor={actor}")

        active_channels = scope.get("active_channels") or ()
        if active_channels:
            parts.append("active=" + ",".join(str(item) for item in active_channels))

        if scope.get("agent_depth") is not None:
            parts.append(f"depth={scope['agent_depth']}")

        if scope.get("branch_id") is not None:
            parts.append(f"branch={scope['branch_id']}")

        agent_role = str(scope.get("agent_role", "") or "")
        if agent_role:
            parts.append(f"role={agent_role}")

        parent_session_id = str(scope.get("parent_session_id", "") or "")
        if parent_session_id:
            parts.append(f"parent={parent_session_id}")

        if not parts:
            return ""
        return "[scope: " + " | ".join(parts) + "]"

    def _compact_text(self, text: str, *, limit: int) -> str:
        compacted = " ".join(str(text or "").split())
        if len(compacted) <= limit:
            return compacted
        return compacted[: max(limit - 3, 0)].rstrip() + "..."

    def _build_episode_content(
        self,
        query_text: str,
        response_text: str,
        *,
        scope: dict[str, Any],
    ) -> str:
        lines = ["[EPISODIC TURN]"]
        scope_line = self.format_scope_line(scope)
        if scope_line:
            lines.append(scope_line)
        lines.append(f"Usuario: {self._compact_text(query_text, limit=280)}")
        lines.append(f"Assistente: {self._compact_text(response_text, limit=560)}")
        return "\n".join(lines)

    def _build_episode_metadata(
        self,
        rlm_session: Any,
        query_text: str,
        response_text: str,
        *,
        scope: dict[str, Any],
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "source": "daemon_episode",
            "memory_kind": "episodic_turn",
            "query_preview": self._compact_text(query_text, limit=180),
            "response_preview": self._compact_text(response_text, limit=240),
        }
        turn = getattr(getattr(rlm_session, "_state", None), "total_turns", None)
        if isinstance(turn, int):
            metadata["turn"] = turn
        channel = str(scope.get("channel", "") or "")
        actor = str(scope.get("actor", "") or "")
        workspace_scope = str(scope.get("workspace_scope", "") or "")
        agent_role = str(scope.get("agent_role", "") or "")
        parent_session_id = str(scope.get("parent_session_id", "") or "")
        if channel:
            metadata["agent_channel"] = channel
        if actor:
            metadata["actor"] = actor
        active_channels = scope.get("active_channels") or ()
        if active_channels:
            metadata["active_channels"] = [str(item) for item in active_channels]
        if workspace_scope:
            metadata["workspace_scope"] = workspace_scope
        if scope.get("agent_depth") is not None:
            metadata["agent_depth"] = int(scope["agent_depth"])
        if scope.get("branch_id") is not None:
            metadata["branch_id"] = int(scope["branch_id"])
        if agent_role:
            metadata["agent_role"] = agent_role
        if parent_session_id:
            metadata["parent_session_id"] = parent_session_id
        return metadata

    def _write_episode_memory(
        self,
        rlm_session: Any,
        query_text: str,
        response_text: str,
        *,
        session: Any | None = None,
    ) -> bool:
        memory = self.get_session_memory_store(rlm_session)
        session_id = self.get_session_id(rlm_session)
        if memory is None or not session_id:
            return False

        add_memory = getattr(memory, "add_memory", None)
        if not callable(add_memory):
            return False

        scope = self.collect_scope(rlm_session, session=session)
        add_memory(
            session_id=session_id,
            content=self._build_episode_content(query_text, response_text, scope=scope),
            metadata=self._build_episode_metadata(
                rlm_session,
                query_text,
                response_text,
                scope=scope,
            ),
            importance_score=0.6,
        )
        with self.lock:
            self.stats["episodic_writes"] += 1
            self.last_scope = scope
        return True

    def _schedule_hot_cache_update(self, rlm_session: Any, query_text: str) -> None:
        memory = self.get_session_memory_store(rlm_session)
        memory_cache = getattr(rlm_session, "_memory_cache", None)
        if memory is None or memory_cache is None:
            return
        schedule_update = getattr(memory_cache, "schedule_update", None)
        if not callable(schedule_update):
            return
        schedule_update(
            query=query_text,
            memory_manager=memory,
            available_tokens=8000,
        )

    def _record_post_turn_async(
        self,
        rlm_session: Any,
        query_text: str,
        response_text: str,
        *,
        session: Any | None = None,
    ) -> None:
        episode_written = False
        try:
            episode_written = self._write_episode_memory(
                rlm_session,
                query_text,
                response_text,
                session=session,
            )
        except Exception:
            with self.lock:
                self.stats["failures"] += 1

        try:
            post_turn_async = getattr(rlm_session, "_post_turn_async", None)
            if callable(post_turn_async):
                post_turn_async(query_text, response_text)
                with self.lock:
                    self.stats["post_turn_delegated"] += 1
                return

            schedule_post_turn_memory = getattr(rlm_session, "schedule_post_turn_memory", None)
            if callable(schedule_post_turn_memory):
                schedule_post_turn_memory(query_text, response_text)
                with self.lock:
                    self.stats["post_turn_delegated"] += 1
                return

            if episode_written:
                self._schedule_hot_cache_update(rlm_session, query_text)
        except Exception:
            with self.lock:
                self.stats["failures"] += 1

    def record_post_turn(
        self,
        rlm_session: Any,
        query_text: str,
        response_text: str,
        *,
        session: Any | None = None,
    ) -> None:
        if rlm_session is None or not query_text or not response_text:
            return

        with self.lock:
            self.stats["post_turn_requests"] += 1

        worker = threading.Thread(
            target=self._record_post_turn_async,
            args=(rlm_session, query_text, response_text),
            kwargs={"session": session},
            daemon=True,
            name="rlm-memory-post-turn-daemon",
        )
        try:
            worker.start()
        except Exception:
            with self.lock:
                self.stats["failures"] += 1


__all__ = ["DaemonMemoryAccess"]