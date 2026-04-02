"""
Testes para a infraestrutura Fase 10: Disposable, CancellationToken,
ReentrancyBarrier, AsyncLimiter, SyncLimiter, ShutdownManager.
"""
import asyncio
import threading
import time
import pytest


# ═══════════════════════════════════════════════════════════════════════════
# 1. Disposable + DisposableStore
# ═══════════════════════════════════════════════════════════════════════════

class TestDisposable:
    def test_disposable_store_basic(self):
        from rlm.core.lifecycle.disposable import DisposableStore, CallbackDisposable

        disposed = []
        store = DisposableStore()
        store.add(CallbackDisposable(lambda: disposed.append("a")))
        store.add(CallbackDisposable(lambda: disposed.append("b")))
        store.add(CallbackDisposable(lambda: disposed.append("c")))

        assert len(store) == 3
        store.dispose()
        # Ordem reversa
        assert disposed == ["c", "b", "a"]
        assert store.is_disposed
        assert len(store) == 0

    def test_disposable_store_idempotent(self):
        from rlm.core.lifecycle.disposable import DisposableStore, CallbackDisposable

        count = []
        store = DisposableStore()
        store.add(CallbackDisposable(lambda: count.append(1)))
        store.dispose()
        store.dispose()  # segunda chamada é no-op
        assert len(count) == 1

    def test_disposable_store_error_isolation(self):
        from rlm.core.lifecycle.disposable import DisposableStore, CallbackDisposable

        disposed = []
        store = DisposableStore()
        store.add(CallbackDisposable(lambda: disposed.append("a")))
        store.add(CallbackDisposable(lambda: (_ for _ in ()).throw(RuntimeError("boom"))))
        store.add(CallbackDisposable(lambda: disposed.append("c")))

        store.dispose()
        # "c" dispara primeiro (reversa), depois o erro, depois "a"
        assert "c" in disposed
        assert "a" in disposed

    def test_disposable_store_context_manager(self):
        from rlm.core.lifecycle.disposable import DisposableStore, CallbackDisposable

        disposed = []
        with DisposableStore() as store:
            store.add(CallbackDisposable(lambda: disposed.append("x")))
        assert disposed == ["x"]

    def test_add_after_dispose_calls_immediately(self):
        from rlm.core.lifecycle.disposable import DisposableStore, CallbackDisposable

        disposed = []
        store = DisposableStore()
        store.dispose()
        store.add(CallbackDisposable(lambda: disposed.append("late")))
        assert disposed == ["late"]

    def test_to_disposable(self):
        from rlm.core.lifecycle.disposable import to_disposable

        called = []
        d = to_disposable(lambda: called.append(True))
        d.dispose()
        d.dispose()  # idempotente
        assert len(called) == 1

    def test_adapt_closeable(self):
        from rlm.core.lifecycle.disposable import adapt_closeable

        class HasClose:
            def __init__(self):
                self.closed = False
            def close(self):
                self.closed = True

        obj = HasClose()
        d = adapt_closeable(obj)
        d.dispose()
        assert obj.closed

    def test_adapt_closeable_no_method_raises(self):
        from rlm.core.lifecycle.disposable import adapt_closeable

        with pytest.raises(TypeError, match="does not have"):
            adapt_closeable(42)

    def test_remove_from_store(self):
        from rlm.core.lifecycle.disposable import DisposableStore, CallbackDisposable

        disposed = []
        store = DisposableStore()
        d = CallbackDisposable(lambda: disposed.append("removed"))
        store.add(d)
        assert store.remove(d) is True
        store.dispose()
        assert disposed == []

    def test_idisposable_protocol(self):
        from rlm.core.lifecycle.disposable import IDisposable

        class Custom:
            def dispose(self):
                pass

        assert isinstance(Custom(), IDisposable)


# ═══════════════════════════════════════════════════════════════════════════
# 2. CancellationToken
# ═══════════════════════════════════════════════════════════════════════════

class TestCancellationToken:
    def test_none_token_never_cancels(self):
        from rlm.core.lifecycle.cancellation import CancellationToken

        token = CancellationToken.NONE
        assert not token.is_cancelled
        # Tenta "cancelar" — NONE é imutável
        token._fire("test")
        assert not token.is_cancelled

    def test_cancelled_token_always_cancelled(self):
        from rlm.core.lifecycle.cancellation import CancellationToken

        token = CancellationToken.CANCELLED
        assert token.is_cancelled

    def test_source_creates_and_cancels_token(self):
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        source = CancellationTokenSource()
        token = source.token
        assert not token.is_cancelled

        source.cancel(reason="test abort")
        assert token.is_cancelled
        assert token.reason == "test abort"

    def test_cancel_is_idempotent(self):
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        source = CancellationTokenSource()
        source.cancel()
        source.cancel()  # no-op
        assert source.token.is_cancelled

    def test_on_cancelled_callback(self):
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        called = []
        source = CancellationTokenSource()
        source.token.on_cancelled(lambda: called.append("fired"))

        assert called == []
        source.cancel()
        assert called == ["fired"]

    def test_on_cancelled_after_cancel_fires_immediately(self):
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        source = CancellationTokenSource()
        source.cancel()

        called = []
        source.token.on_cancelled(lambda: called.append("late"))
        assert called == ["late"]

    def test_listener_removal(self):
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        called = []
        source = CancellationTokenSource()
        registration = source.token.on_cancelled(lambda: called.append("x"))
        registration.dispose()  # remove listener

        source.cancel()
        assert called == []  # never fired

    def test_parent_child_cancellation(self):
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        parent = CancellationTokenSource()
        child = CancellationTokenSource(parent=parent.token)

        assert not child.token.is_cancelled
        parent.cancel(reason="parent abort")
        assert child.token.is_cancelled
        assert "parent" in child.token.reason

    def test_child_cancel_independent_of_parent(self):
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        parent = CancellationTokenSource()
        child = CancellationTokenSource(parent=parent.token)

        child.cancel(reason="child only")
        assert child.token.is_cancelled
        assert not parent.token.is_cancelled

    def test_dispose_disconnects_from_parent(self):
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        parent = CancellationTokenSource()
        child = CancellationTokenSource(parent=parent.token)
        child.dispose()

        parent.cancel()
        # child foi desconectado — não deve ser cancelado
        assert not child.token.is_cancelled

    def test_cancelled_token_on_cancelled_fires(self):
        from rlm.core.lifecycle.cancellation import CancellationToken

        called = []
        CancellationToken.CANCELLED.on_cancelled(lambda: called.append(True))
        assert called == [True]


# ═══════════════════════════════════════════════════════════════════════════
# 3. ReentrancyBarrier
# ═══════════════════════════════════════════════════════════════════════════

class TestReentrancyBarrier:
    def test_run_or_skip_normal(self):
        from rlm.core.engine.control_flow import ReentrancyBarrier

        barrier = ReentrancyBarrier()
        assert barrier.run_or_skip(lambda: 42) == 42
        assert not barrier.is_occupied

    def test_run_or_skip_reentrant(self):
        from rlm.core.engine.control_flow import ReentrancyBarrier

        barrier = ReentrancyBarrier()
        inner_result = []

        def outer():
            result = barrier.run_or_skip(lambda: "inner")
            inner_result.append(result)
            return "outer"

        assert barrier.run_or_skip(outer) == "outer"
        assert inner_result == [None]  # inner was skipped (reentrancy)

    def test_run_or_throw_normal(self):
        from rlm.core.engine.control_flow import ReentrancyBarrier

        barrier = ReentrancyBarrier()
        assert barrier.run_or_throw(lambda: "ok") == "ok"

    def test_run_or_throw_reentrant_raises(self):
        from rlm.core.engine.control_flow import ReentrancyBarrier

        barrier = ReentrancyBarrier()

        def outer():
            barrier.run_or_throw(lambda: None)

        with pytest.raises(RuntimeError, match="reentrante"):
            barrier.run_or_throw(outer)

    def test_barrier_releases_on_exception(self):
        from rlm.core.engine.control_flow import ReentrancyBarrier

        barrier = ReentrancyBarrier()

        with pytest.raises(ValueError):
            barrier.run_or_skip(lambda: (_ for _ in ()).throw(ValueError("test")))

        # Barrier deve estar livre após exceção
        assert not barrier.is_occupied
        assert barrier.run_or_skip(lambda: "recovered") == "recovered"


# ═══════════════════════════════════════════════════════════════════════════
# 4. AsyncLimiter
# ═══════════════════════════════════════════════════════════════════════════

class TestAsyncLimiter:
    def test_basic_limiting(self):
        from rlm.core.engine.control_flow import AsyncLimiter

        async def _test():
            limiter = AsyncLimiter(max_concurrent=2)
            results = []

            async def task(val):
                results.append(f"start-{val}")
                await asyncio.sleep(0.01)
                results.append(f"end-{val}")
                return val

            # Roda 3 tarefas com limite de 2
            await asyncio.gather(
                limiter.queue(task(1)),
                limiter.queue(task(2)),
                limiter.queue(task(3)),
            )
            assert len(results) == 6
            assert limiter.size == 0

        asyncio.run(_test())

    def test_limiter_dispose_rejects(self):
        from rlm.core.engine.control_flow import AsyncLimiter

        async def _test():
            limiter = AsyncLimiter(max_concurrent=1)
            limiter.dispose()

            with pytest.raises(RuntimeError, match="disposed"):
                await limiter.queue(asyncio.sleep(0))

        asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════════
# 5. SyncLimiter
# ═══════════════════════════════════════════════════════════════════════════

class TestSyncLimiter:
    def test_basic_context_manager(self):
        from rlm.core.engine.control_flow import SyncLimiter

        limiter = SyncLimiter(max_concurrent=3)
        assert limiter.active == 0

        with limiter:
            assert limiter.active == 1
        assert limiter.active == 0

    def test_concurrency_limit(self):
        from rlm.core.engine.control_flow import SyncLimiter

        limiter = SyncLimiter(max_concurrent=2)
        max_concurrent_seen = [0]
        current = [0]
        lock = threading.Lock()

        def worker():
            with limiter:
                with lock:
                    current[0] += 1
                    max_concurrent_seen[0] = max(max_concurrent_seen[0], current[0])
                time.sleep(0.05)
                with lock:
                    current[0] -= 1

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert max_concurrent_seen[0] <= 2


# ═══════════════════════════════════════════════════════════════════════════
# 6. Throttler
# ═══════════════════════════════════════════════════════════════════════════

class TestThrottler:
    def test_coalesces_calls(self):
        from rlm.core.engine.control_flow import Throttler

        throttler = Throttler()
        results = []

        def work():
            results.append(len(results))
            return len(results)

        r1 = throttler.queue(work)
        r2 = throttler.queue(work)
        assert r1 == 1  # primeiro
        assert r2 == 2  # segundo — não foi coalescido pois o primeiro já terminou

    def test_dispose_stops_processing(self):
        from rlm.core.engine.control_flow import Throttler

        throttler = Throttler()
        throttler.dispose()
        assert throttler.queue(lambda: 42) is None


# ═══════════════════════════════════════════════════════════════════════════
# 7. ShutdownManager
# ═══════════════════════════════════════════════════════════════════════════

class TestShutdownManager:
    def test_shutdown_no_vetos(self):
        from rlm.core.lifecycle.shutdown import ShutdownManager

        manager = ShutdownManager()
        manager.shutdown_sync(timeout=1.0)
        assert manager.is_shutting_down

    def test_shutdown_with_temporary_veto(self):
        from rlm.core.lifecycle.shutdown import ShutdownManager

        manager = ShutdownManager()
        call_count = [0]

        def veto_fn():
            call_count[0] += 1
            return call_count[0] < 3  # veta as duas primeiras checagens

        manager.register_veto("test", veto_fn)
        manager.shutdown_sync(timeout=5.0)
        assert call_count[0] >= 3
        assert manager.is_shutting_down

    def test_shutdown_disposes_resources(self):
        from rlm.core.lifecycle.shutdown import ShutdownManager
        from rlm.core.lifecycle.disposable import CallbackDisposable

        disposed = []
        manager = ShutdownManager()
        manager.register_disposable(CallbackDisposable(lambda: disposed.append("r1")))
        manager.register_disposable(CallbackDisposable(lambda: disposed.append("r2")))

        manager.shutdown_sync(timeout=1.0)
        assert "r1" in disposed
        assert "r2" in disposed

    def test_veto_unregistration(self):
        from rlm.core.lifecycle.shutdown import ShutdownManager

        manager = ShutdownManager()
        registration = manager.register_veto("test", lambda: True)
        registration.dispose()  # remove o veto

        # Shutdown deve completar sem espera
        manager.shutdown_sync(timeout=0.5)
        assert manager.is_shutting_down

    def test_async_shutdown(self):
        from rlm.core.lifecycle.shutdown import ShutdownManager

        async def _test():
            manager = ShutdownManager()
            await manager.shutdown(timeout=1.0)
            assert manager.is_shutting_down

        asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════════
# 8. Integração: RLM com CancellationToken
# ═══════════════════════════════════════════════════════════════════════════

class TestRLMInfraIntegration:
    def test_rlm_has_cancel_token(self):
        from rlm.core.engine.rlm import RLM
        from rlm.core.lifecycle.cancellation import CancellationToken

        rlm = RLM(backend="openai", backend_kwargs={"model_name": "test"})
        assert rlm._cancel_token is CancellationToken.NONE
        assert not rlm._cancel_token.is_cancelled

    def test_rlm_has_disposable_store(self):
        from rlm.core.engine.rlm import RLM
        from rlm.core.lifecycle.disposable import DisposableStore

        rlm = RLM(backend="openai", backend_kwargs={"model_name": "test"})
        assert isinstance(rlm._disposables, DisposableStore)

    def test_rlm_has_compaction_barrier(self):
        from rlm.core.engine.rlm import RLM
        from rlm.core.engine.control_flow import ReentrancyBarrier

        rlm = RLM(backend="openai", backend_kwargs={"model_name": "test"})
        assert isinstance(rlm._compaction_barrier, ReentrancyBarrier)

    def test_rlm_dispose(self):
        from rlm.core.engine.rlm import RLM

        rlm = RLM(backend="openai", backend_kwargs={"model_name": "test"})
        rlm.dispose()  # should not raise
        assert rlm._disposables.is_disposed

    def test_rlm_context_manager_calls_dispose(self):
        from rlm.core.engine.rlm import RLM

        with RLM(backend="openai", backend_kwargs={"model_name": "test"}) as rlm:
            pass
        assert rlm._disposables.is_disposed

    def test_session_has_cancel_token_proxy(self):
        from rlm.session import RLMSession
        from rlm.core.lifecycle.cancellation import CancellationToken

        session = RLMSession(
            backend="openai",
            backend_kwargs={"model_name": "test"},
        )
        assert session._cancel_token is CancellationToken.NONE

    def test_session_cancel_token_propagates(self):
        from rlm.session import RLMSession
        from rlm.core.lifecycle.cancellation import CancellationToken

        session = RLMSession(
            backend="openai",
            backend_kwargs={"model_name": "test"},
        )
        token = CancellationToken()
        session._cancel_token = token
        assert session._rlm._cancel_token is token

    def test_session_dispose(self):
        from rlm.session import RLMSession

        session = RLMSession(
            backend="openai",
            backend_kwargs={"model_name": "test"},
        )
        session.dispose()  # should not raise

    def test_supervisor_has_shutdown_manager(self):
        from rlm.core.orchestration.supervisor import RLMSupervisor

        sup = RLMSupervisor()
        assert hasattr(sup, '_shutdown_manager')
        assert hasattr(sup, '_cancel_sources')

    def test_lm_handler_has_limiter(self):
        """LMHandler deve ter rate limiter configurado."""
        from unittest.mock import MagicMock
        from rlm.core.engine.lm_handler import LMHandler
        from rlm.core.engine.control_flow import SyncLimiter

        mock_client = MagicMock()
        mock_client.model_name = "test-model"

        handler = LMHandler(client=mock_client, port=0)
        assert isinstance(handler._llm_limiter, SyncLimiter)
