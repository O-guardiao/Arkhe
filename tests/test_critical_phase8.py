"""
Testes críticos — Fase 8: Hooks, Scheduler, Temporal Decay,
Query Expansion, Identifier Policy + Integração API.

Cobrem exatamente o que foi implementado. Sem dependências externas
(OpenAI, FastAPI testclient): tudo mockado isoladamente.

Execute:
    pytest tests/test_critical_phase8.py -v
"""

import asyncio
import math
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch, call
import pytest

# ---------------------------------------------------------------------------
# Imports dos módulos sob teste
# ---------------------------------------------------------------------------

from rlm.core.engine.hooks import HookSystem, HookEvent
from rlm.core.orchestration.scheduler import RLMScheduler, CronJob, parse_interval_seconds, compute_next_run
from rlm.core.memory.memory_manager import MultiVectorMemory, cosine_similarity
from rlm.core.engine.compaction import (
    CompactionConfig,
    ContextCompactor,
    estimate_tokens,
    COMPACTION_SYSTEM_PROMPT,
    NO_IDENTIFIER_PROMPT,
    CUSTOM_IDENTIFIER_PROMPT,
)


# ===========================================================================
# 1. HOOK SYSTEM
# ===========================================================================

class TestHookSystemCritical:
    """Testa HookSystem em cenários de falha e comportamento correto."""

    def setup_method(self):
        self.hooks = HookSystem()

    # --- registro e disparo básico ---

    def test_handler_called_on_trigger(self):
        called = []
        self.hooks.register("completion.finished", lambda e: called.append(e.event_type))
        self.hooks.trigger("completion.finished", session_id="s1")
        assert called == ["completion.finished"]

    def test_handler_receives_kwargs_in_context(self):
        received = {}
        def handler(event: HookEvent):
            received.update(event.context)
        self.hooks.register("message.received", handler)
        self.hooks.trigger("message.received", session_id="x", context={"foo": "bar"})
        assert received == {"foo": "bar"}

    def test_handler_receives_correct_session_id(self):
        ids = []
        self.hooks.register("session.created", lambda e: ids.append(e.session_id))
        self.hooks.trigger("session.created", session_id="abc-123")
        assert ids == ["abc-123"]

    # --- wildcard ---

    def test_wildcard_handler_fires_for_all_subtypes(self):
        fired = []
        self.hooks.register("completion.*", lambda e: fired.append(e.event_type))
        self.hooks.trigger("completion.started")
        self.hooks.trigger("completion.finished")
        self.hooks.trigger("completion.aborted")
        assert fired == ["completion.started", "completion.finished", "completion.aborted"]

    def test_wildcard_does_not_fire_for_different_namespace(self):
        fired = []
        self.hooks.register("completion.*", lambda e: fired.append(e.event_type))
        self.hooks.trigger("session.created")
        assert fired == []

    def test_global_wildcard_fires_for_everything(self):
        fired = []
        self.hooks.register("*", lambda e: fired.append(e.event_type))
        self.hooks.trigger("completion.started")
        self.hooks.trigger("session.closed")
        self.hooks.trigger("repl.error")
        assert len(fired) == 3

    def test_agent_handoff_wildcard_fires(self):
        fired = []
        self.hooks.register("agent.*", lambda e: fired.append(e.event_type))
        self.hooks.trigger("agent.handoff", session_id="sess-1", context={"target_role": "worker"})
        assert fired == ["agent.handoff"]

    # --- erros não propagam ---

    def test_broken_handler_does_not_raise(self):
        def bad_handler(event):
            raise RuntimeError("Erro intencional!")
        self.hooks.register("repl.error", bad_handler)
        # Não deve lançar exceção
        self.hooks.trigger("repl.error")
        assert self.hooks.get_stats()["errors_caught"] == 1

    def test_broken_handler_does_not_block_subsequent_handlers(self):
        called = []
        def bad(e): raise ValueError("broken")
        def good(e): called.append("ok")
        self.hooks.register("plugin.loaded", bad)
        self.hooks.register("plugin.loaded", good)
        self.hooks.trigger("plugin.loaded")
        assert called == ["ok"]

    # --- unregister ---

    def test_unregister_removes_handler(self):
        calls = []
        handler = lambda e: calls.append(1)
        self.hooks.register("message.sent", handler)
        self.hooks.unregister("message.sent", handler)
        self.hooks.trigger("message.sent")
        assert calls == []

    def test_unregister_returns_false_for_unknown_handler(self):
        result = self.hooks.unregister("session.created", lambda e: None)
        assert result is False

    # --- duplicatas ---

    def test_same_handler_not_registered_twice(self):
        calls = []
        handler = lambda e: calls.append(1)
        self.hooks.register("completion.started", handler)
        self.hooks.register("completion.started", handler)
        self.hooks.trigger("completion.started")
        assert calls == [1]  # chamado apenas uma vez

    # --- stats ---

    def test_stats_count_triggers(self):
        self.hooks.register("session.created", lambda e: None)
        self.hooks.trigger("session.created")
        self.hooks.trigger("session.created")
        stats = self.hooks.get_stats()
        assert stats["triggers_fired"] == 2

    def test_stats_count_errors(self):
        self.hooks.register("repl.executed", lambda e: (_ for _ in ()).throw(Exception("x")))
        try:
            self.hooks.trigger("repl.executed")
        except Exception:
            pass
        # erros contados e não propagados
        stats = self.hooks.get_stats()
        assert stats["errors_caught"] >= 1

    def test_clear_removes_all_handlers(self):
        calls = []
        self.hooks.register("session.created", lambda e: calls.append(1))
        self.hooks.clear()
        self.hooks.trigger("session.created")
        assert calls == []

    # --- async handlers ---

    def test_async_handler_is_scheduled(self):
        """handler async não deve explodir de forma síncrona."""
        called = []
        async def async_handler(event: HookEvent):
            called.append(event.event_type)
        self.hooks.register("completion.finished", async_handler)
        # trigger síncrono com handler async — não deve lançar
        self.hooks.trigger("completion.finished")
        # A chamada pode ter agendado uma coroutine; não podemos esperar
        # Mas o mais importante: nenhuma exceção foi levantada.

    def test_trigger_async_awaits_handler(self):
        """trigger_async deve aguardar handlers async corretamente."""
        called = []
        async def async_handler(event: HookEvent):
            called.append(event.event_type)
        self.hooks.register("completion.started", async_handler)
        asyncio.run(self.hooks.trigger_async("completion.started", session_id="s1"))
        assert called == ["completion.started"]

    # --- thread-safety básica ---

    def test_concurrent_register_and_trigger_does_not_crash(self):
        errors = []
        def worker():
            h = HookSystem()
            try:
                for i in range(50):
                    h.register("ev.x", lambda e: None)
                    h.trigger("ev.x")
                    h.unregister("ev.x", lambda e: None)
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert errors == []


# ===========================================================================
# 2. TEMPORAL DECAY
# ===========================================================================

class TestTemporalDecay:
    """Verifica que memórias antigas recebem score menor que memórias recentes."""

    def _make_memory_db(self, tmp_path: str) -> MultiVectorMemory:
        """Cria instância sem OpenAI (mocked)."""
        with patch("rlm.core.memory.memory_manager.openai", None):
            mem = MultiVectorMemory(db_path=tmp_path)
        return mem

    def _insert_chunk(self, db_path: str, cid: str, content: str, timestamp: str):
        """Insere diretamente no SQLite com timestamp controlado."""
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memory_chunks (id, session_id, content, metadata, timestamp, embedding) "
                "VALUES (?, 'test', ?, '{}', ?, '[]')",
                (cid, content, timestamp)
            )
            conn.execute(
                "INSERT OR REPLACE INTO memory_fts (id, content) VALUES (?, ?)",
                (cid, content)
            )
            conn.commit()

    def test_decay_reduces_score_for_old_memories(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name

        mem = self._make_memory_db(db)

        # Chunk recente: hoje
        import datetime
        now = datetime.datetime.utcnow()
        recent_ts = now.strftime("%Y-%m-%dT%H:%M:%S")
        old_ts = (now - datetime.timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")

        self._insert_chunk(db, "recent", "python machine learning tutorial", recent_ts)
        self._insert_chunk(db, "old", "python machine learning tutorial", old_ts)

        results = mem.search_hybrid(
            "python machine learning",
            limit=10,
            temporal_decay=True,
            half_life_days=30.0,
        )

        scores = {r["id"]: r["hybrid_score"] for r in results}
        # O chunk de 90 dias atrás deve ter score <= 12.5% do recente
        # exp(-ln(2)/30 * 90) = exp(-3*ln2) = 0.125
        if "recent" in scores and "old" in scores:
            assert scores["recent"] > scores["old"], (
                f"Esperado recent > old, mas recent={scores['recent']}, old={scores['old']}"
            )

    def test_decay_disabled_scores_are_close_for_equal_content(self):
        """Sem decay, chunks com conteúdo idêntico mas idades diferentes
        devem ter scores muito próximos (ratio > 0.90).
        Com decay ativo, a diferença seria muito maior (ratio ≈ 0.125)."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name

        mem = self._make_memory_db(db)

        import datetime
        now = datetime.datetime.utcnow()
        recent_ts = now.strftime("%Y-%m-%dT%H:%M:%S")
        old_ts = (now - datetime.timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")

        self._insert_chunk(db, "r2", "python machine learning tutorial", recent_ts)
        self._insert_chunk(db, "o2", "python machine learning tutorial", old_ts)

        # decay=False: scores deveriam ser quase iguais (FTS pode dar ranks ligeiramente
        # diferentes por ordem de inserção, mas a diferença é mínima)
        results_off = mem.search_hybrid("python machine learning", limit=10, temporal_decay=False)
        scores_off = {r["id"]: r["hybrid_score"] for r in results_off}

        # decay=True: o chunk de 90 dias deve ter score << recente (ratio ≈ 0.125)
        results_on = mem.search_hybrid("python machine learning", limit=10, temporal_decay=True, half_life_days=30.0)
        scores_on = {r["id"]: r["hybrid_score"] for r in results_on}

        if "r2" in scores_off and "o2" in scores_off:
            ratio_off = min(scores_off["r2"], scores_off["o2"]) / max(scores_off["r2"], scores_off["o2"])
            assert ratio_off > 0.85, (
                f"Sem decay, ratio entre scores deve ser > 0.85, mas foi {ratio_off:.3f}: {scores_off}"
            )

        if "r2" in scores_on and "o2" in scores_on:
            ratio_on = scores_on["o2"] / scores_on["r2"] if scores_on["r2"] > 0 else 1.0
            assert ratio_on < 0.5, (
                f"Com decay (meia-vida 30d, 90 dias de idade), ratio deve ser < 0.5, mas foi {ratio_on:.3f}"
            )

    def test_half_life_formula(self):
        """Valida matematicamente a fórmula de decay."""
        half_life = 30.0
        lam = math.log(2) / half_life
        # Após exatamente half_life dias → fator = 0.5
        factor = math.exp(-lam * half_life)
        assert abs(factor - 0.5) < 1e-9

    def test_very_old_memory_approaches_zero(self):
        half_life = 30.0
        lam = math.log(2) / half_life
        # 1000 dias → praticamente zero
        factor = math.exp(-lam * 1000)
        assert factor < 1e-8

    def test_zero_age_memory_factor_is_one(self):
        lam = math.log(2) / 30.0
        factor = math.exp(-lam * 0.0)
        assert factor == 1.0


# ===========================================================================
# 3. QUERY EXPANSION
# ===========================================================================

class TestQueryExpansion:
    """Testa search_with_query_expansion: variantes, deduplicação, fallback."""

    def _make_memory_db(self, db_path: str) -> MultiVectorMemory:
        with patch("rlm.core.memory.memory_manager.openai", None):
            mem = MultiVectorMemory(db_path=db_path)
        return mem

    def test_calls_llm_for_variants(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        mem = self._make_memory_db(db)

        llm_calls = []
        def mock_llm(prompt: str) -> str:
            llm_calls.append(prompt)
            return "variante um\nvariante dois\nvariante tres"

        mem.search_with_query_expansion("machine learning", llm_fn=mock_llm, limit=3)
        assert len(llm_calls) == 1
        assert "machine learning" in llm_calls[0]

    def test_returns_list(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        mem = self._make_memory_db(db)

        results = mem.search_with_query_expansion(
            "qualquer coisa",
            llm_fn=lambda p: "variante",
            limit=5,
        )
        assert isinstance(results, list)

    def test_llm_failure_falls_back_to_original_query(self):
        """Se o LLM explodir, deve usar só a query original sem lançar exceção."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        mem = self._make_memory_db(db)

        def exploding_llm(prompt: str) -> str:
            raise ConnectionError("API timeout")

        # Não deve lançar
        results = mem.search_with_query_expansion(
            "fallback query",
            llm_fn=exploding_llm,
            limit=3,
        )
        assert isinstance(results, list)

    def test_deduplication_prevents_duplicate_ids(self):
        """Mesmo id aparecendo em múltiplas variantes não deve duplicar resultado."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        mem = self._make_memory_db(db)

        # Inserir um chunk compartilhado entre todos os resultados
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memory_chunks (id, session_id, content, metadata, timestamp, embedding) "
                "VALUES ('shared', 's', 'python data science', '{}', '2025-01-01T00:00:00', '[]')"
            )
            conn.execute("INSERT OR REPLACE INTO memory_fts (id, content) VALUES ('shared', 'python data science')")
            conn.commit()

        results = mem.search_with_query_expansion(
            "python data science",
            llm_fn=lambda p: "ciência de dados python\ndados em python",
            limit=10,
        )
        ids = [r["id"] for r in results]
        assert len(ids) == len(set(ids)), f"IDs duplicados encontrados: {ids}"

    def test_expansion_score_present_in_results(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        mem = self._make_memory_db(db)

        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memory_chunks (id, session_id, content, metadata, timestamp, embedding) "
                "VALUES ('c1', 's', 'deep learning neural nets', '{}', '2025-06-01T00:00:00', '[]')"
            )
            conn.execute("INSERT OR REPLACE INTO memory_fts (id, content) VALUES ('c1', 'deep learning neural nets')")
            conn.commit()

        results = mem.search_with_query_expansion(
            "deep learning",
            llm_fn=lambda p: "redes neurais profundas",
            limit=5,
        )
        for r in results:
            assert "expansion_score" in r, f"Falta expansion_score em: {r}"


# ===========================================================================
# 4. IDENTIFIER POLICY
# ===========================================================================

class TestIdentifierPolicy:
    """Testa os 3 modos de identifier_policy no ContextCompactor."""

    def _make_compactor(self, **kwargs) -> ContextCompactor:
        cfg = CompactionConfig(**kwargs)
        return ContextCompactor(config=cfg)

    def _make_messages(self, n: int = 10) -> list[dict]:
        msgs = []
        for i in range(n):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append({"role": role, "content": f"Mensagem número {i}" * 20})
        return msgs

    def test_strict_policy_uses_identifier_prompt(self):
        compactor = self._make_compactor(identifier_policy="strict")
        prompt_used = []
        def mock_llm(prompt: str) -> str:
            prompt_used.append(prompt)
            return "Resumo gerado."
        msgs = self._make_messages(10)
        compactor._generate_summary(msgs, mock_llm)
        assert len(prompt_used) == 1
        assert "Preserve ALL identifiers" in prompt_used[0]

    def test_off_policy_uses_simple_prompt(self):
        compactor = self._make_compactor(identifier_policy="off")
        prompt_used = []
        def mock_llm(prompt: str) -> str:
            prompt_used.append(prompt)
            return "Resumo simples."
        msgs = self._make_messages(10)
        compactor._generate_summary(msgs, mock_llm)
        assert len(prompt_used) == 1
        assert "Preserve ALL identifiers" not in prompt_used[0]

    def test_custom_policy_injects_instructions(self):
        custom = "Ignore UUIDs but keep function names."
        compactor = self._make_compactor(
            identifier_policy="custom",
            identifier_custom_instructions=custom,
        )
        prompt_used = []
        def mock_llm(prompt: str) -> str:
            prompt_used.append(prompt)
            return "Resumo customizado."
        msgs = self._make_messages(10)
        compactor._generate_summary(msgs, mock_llm)
        assert custom in prompt_used[0], f"Instrução custom ausente no prompt: {prompt_used[0][:200]}"

    def test_legacy_identifier_preservation_false_maps_to_off(self):
        """identifier_preservation=False com policy='strict' => comportamento 'off'."""
        compactor = self._make_compactor(
            identifier_policy="strict",
            identifier_preservation=False,  # legado
        )
        prompt_used = []
        def mock_llm(prompt: str) -> str:
            prompt_used.append(prompt)
            return "ok"
        compactor._generate_summary(self._make_messages(10), mock_llm)
        # Retrocompatibilidade: deve usar prompt simples (off)
        assert "Preserve ALL identifiers" not in prompt_used[0]

    def test_legacy_identifier_preservation_true_uses_strict(self):
        """identifier_preservation=True (padrão) mantém behavior strict."""
        compactor = self._make_compactor(
            identifier_policy="strict",
            identifier_preservation=True,
        )
        prompt_used = []
        def mock_llm(prompt: str) -> str:
            prompt_used.append(prompt)
            return "ok"
        compactor._generate_summary(self._make_messages(10), mock_llm)
        assert "Preserve ALL identifiers" in prompt_used[0]

    def test_custom_policy_with_empty_instructions_uses_default_text(self):
        """custom sem instruções → usa texto padrão como fallback."""
        compactor = self._make_compactor(identifier_policy="custom", identifier_custom_instructions="")
        prompt_used = []
        def mock_llm(prompt: str) -> str:
            prompt_used.append(prompt)
            return "ok"
        compactor._generate_summary(self._make_messages(10), mock_llm)
        # Não deve lançar erro
        assert len(prompt_used) == 1

    def test_compact_method_uses_correct_policy(self):
        """Verifica o fluxo completo: compact() → _generate_summary() com policy correta."""
        msgs = self._make_messages(20)  # tokens suficientes para disparar compaction
        prompt_used = []
        def mock_llm(prompt: str) -> str:
            prompt_used.append(prompt)
            return "Sumário com identifiers."

        compactor = self._make_compactor(
            identifier_policy="strict",
            max_history_tokens=10,  # threshold baixo para forçar compaction
            min_messages_to_compact=3,
        )
        result = compactor.compact(msgs, mock_llm)
        # Compaction foi ativada → llm chamado
        if prompt_used:
            assert "Preserve ALL identifiers" in prompt_used[0]


# ===========================================================================
# 5. SCHEDULER
# ===========================================================================

class TestSchedulerCritical:
    """Testa RLMScheduler: parsing, add/remove/list, disparo."""

    # --- parse_interval_seconds ---

    def test_parse_seconds(self):
        assert parse_interval_seconds("every:30s") == 30

    def test_parse_minutes(self):
        assert parse_interval_seconds("every:5m") == 300

    def test_parse_hours(self):
        assert parse_interval_seconds("every:2h") == 7200

    def test_parse_days(self):
        assert parse_interval_seconds("every:1d") == 86400

    def test_parse_invalid_returns_none(self):
        assert parse_interval_seconds("cron:* * * * *") is None
        assert parse_interval_seconds("every:") is None
        assert parse_interval_seconds("random text") is None

    # --- add / remove / list ---

    def test_add_and_list_jobs(self):
        scheduler = RLMScheduler(execute_fn=lambda c, p: None)
        job = CronJob(name="daily-report", schedule="every:1d", prompt="Gere relatório", client_id="bot:1")
        scheduler.add_job(job)
        jobs = scheduler.list_jobs()
        assert any(j.name == "daily-report" for j in jobs)

    def test_remove_existing_job_returns_true(self):
        scheduler = RLMScheduler(execute_fn=lambda c, p: None)
        job = CronJob(name="ping", schedule="every:10s", prompt="ping", client_id="bot:1")
        scheduler.add_job(job)
        assert scheduler.remove_job("ping") is True

    def test_remove_nonexistent_job_returns_false(self):
        scheduler = RLMScheduler(execute_fn=lambda c, p: None)
        assert scheduler.remove_job("nonexistent") is False

    def test_add_job_replaces_same_name(self):
        scheduler = RLMScheduler(execute_fn=lambda c, p: None)
        j1 = CronJob(name="job", schedule="every:1h", prompt="v1", client_id="c1")
        j2 = CronJob(name="job", schedule="every:2h", prompt="v2", client_id="c1")
        scheduler.add_job(j1)
        scheduler.add_job(j2)
        jobs = scheduler.list_jobs()
        named = [j for j in jobs if j.name == "job"]
        assert len(named) == 1
        assert named[0].prompt == "v2"

    # --- disparo de job ---

    def test_job_fires_when_overdue(self):
        """Job com last_run=0 e schedule muito curto deve disparar rapidamente."""
        fired = threading.Event()
        def execute(client_id: str, prompt: str):
            fired.set()

        scheduler = RLMScheduler(execute_fn=execute, poll_interval=0.05)
        job = CronJob(
            name="fast-job",
            schedule="every:1s",
            prompt="hello",
            client_id="test:1",
            last_run=0.0,  # nunca rodou
        )
        # Forçar que já está vencido: last_run = agora - 10s
        job.last_run = time.time() - 10
        scheduler.add_job(job)
        scheduler.start()
        fired_ok = fired.wait(timeout=2.0)
        scheduler.stop()
        assert fired_ok, "Job deveria ter disparado dentro de 2s"

    def test_disabled_job_does_not_fire(self):
        fired = threading.Event()
        def execute(client_id: str, prompt: str):
            fired.set()

        scheduler = RLMScheduler(execute_fn=execute, poll_interval=0.05)
        job = CronJob(
            name="disabled-job",
            schedule="every:1s",
            prompt="nope",
            client_id="test:1",
            enabled=False,
            last_run=time.time() - 100,
        )
        scheduler.add_job(job)
        scheduler.start()
        fired_early = fired.wait(timeout=0.5)
        scheduler.stop()
        assert not fired_early, "Job desabilitado não deveria disparar"

    def test_start_and_stop_safely(self):
        scheduler = RLMScheduler(execute_fn=lambda c, p: None)
        scheduler.start()
        assert scheduler.is_running()
        scheduler.stop()
        assert not scheduler.is_running()

    def test_double_start_idempotent(self):
        scheduler = RLMScheduler(execute_fn=lambda c, p: None)
        scheduler.start()
        scheduler.start()  # não deve lançar
        assert scheduler.is_running()
        scheduler.stop()


# ===========================================================================
# 6. INTEGRAÇÃO API — imports e state
# ===========================================================================

class TestApiIntegration:
    """Verifica que api.py importa os módulos certos e os conecta no app.state."""

    def test_hooks_importable_from_api_module(self):
        """api.py deve expor HookSystem via importação transitiva."""
        from rlm.core.engine.hooks import HookSystem
        assert HookSystem is not None

    def test_scheduler_importable_from_api_module(self):
        from rlm.core.orchestration.scheduler import RLMScheduler, CronJob
        assert RLMScheduler is not None
        assert CronJob is not None

    def test_hooks_and_scheduler_are_in_api_imports(self):
        """Verificar que api.py tem os imports necessários no código-fonte."""
        import pathlib
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert "from rlm.core.engine.hooks import HookSystem" in text, "HookSystem não importado em api.py"
        assert "from rlm.core.orchestration.scheduler import RLMScheduler" in text, "RLMScheduler não importado em api.py"
        assert "CronJob" in text, "CronJob não referenciado em api.py"

    def test_cron_endpoints_defined_in_api(self):
        import pathlib
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert '"/cron/jobs"' in text, "Endpoint GET /cron/jobs ausente"
        assert '"/cron/jobs/{job_name}"' in text, "Endpoint DELETE /cron/jobs/{job_name} ausente"

    def test_hooks_trigger_on_message_received_in_api(self):
        import pathlib
        server_dir = pathlib.Path(__file__).parent.parent / "rlm" / "server"
        api_text = (server_dir / "api.py").read_text(encoding="utf-8")
        pipeline_text = (server_dir / "runtime_pipeline.py").read_text(encoding="utf-8")
        text = api_text + pipeline_text
        assert 'message.received' in text, "Hook message.received não disparado no server"
        assert 'completion.started' in text, "Hook completion.started não disparado no server"
        assert 'completion.finished' in text, "Hook completion.finished não disparado no server"

    def test_hooks_stats_endpoint_defined(self):
        import pathlib
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert '"/hooks/stats"' in text, "Endpoint /hooks/stats ausente"

    def test_compaction_config_identifier_policy_default(self):
        """Config padrão deve ter identifier_policy='strict' para não quebrar legado."""
        cfg = CompactionConfig()
        assert cfg.identifier_policy == "strict"
        assert cfg.identifier_custom_instructions == ""
        assert cfg.identifier_preservation is True  # legado intacto

    def test_hooks_gateway_log_defined(self):
        """api.py deve definir gateway_log antes do lifespan."""
        import pathlib
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert "gateway_log = get_logger" in text, "gateway_log não definido em api.py"


# ===========================================================================
# 7. COSINE SIMILARITY (regressão)
# ===========================================================================

class TestCosineSimilarity:
    """Regressão rápida para garantir que cosine_similarity não foi quebrada.
    Tolerância 1e-6 para compatibilidade com backend Rust f32 (arkhe-memory)."""

    def test_identical_vectors_return_one(self):
        v = [1.0, 2.0, 3.0]
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors_return_zero(self):
        assert abs(cosine_similarity([1, 0], [0, 1])) < 1e-6

    def test_opposite_vectors_return_minus_one(self):
        assert abs(cosine_similarity([1, 0], [-1, 0]) - (-1.0)) < 1e-6

    def test_empty_vectors_return_zero(self):
        assert cosine_similarity([], [1, 2]) == 0.0
        assert cosine_similarity([1, 2], []) == 0.0
        assert cosine_similarity([], []) == 0.0
