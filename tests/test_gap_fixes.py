"""
Testes para as 5 lacunas corrigidas na comunicação P2P entre sub-agentes.

Lacuna 1: Memória compartilhada pai→filhos
Lacuna 2: CancelToken propaga para filhos seriais e async
Lacuna 3: SiblingBus unificado parallel+async
Lacuna 4: EventBus propaga para filhos
Lacuna 5: Artefatos em sub_rlm_parallel()
"""
import threading
import time
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# Helper: Mock RLM parent
# ═══════════════════════════════════════════════════════════════════════════

def _make_mock_parent(
    depth: int = 0,
    max_depth: int = 3,
    cancel_token: Any = None,
    event_bus: Any = None,
    shared_memory: Any = None,
):
    """Cria um mock do RLM pai com os atributos necessários."""
    parent = MagicMock()
    parent.depth = depth
    parent.max_depth = max_depth
    parent.backend = "mock"
    parent.backend_kwargs = {"model_name": "test-model"}
    parent.environment_type = "local"
    parent.environment_kwargs = {}
    parent.event_bus = event_bus

    # CancelToken
    if cancel_token is None:
        from rlm.core.lifecycle.cancellation import CancellationToken
        cancel_token = CancellationToken.NONE
    parent._cancel_token = cancel_token

    # Memory
    parent._shared_memory = shared_memory
    parent._persistent_env = None

    # Async bus (inicializado dinamicamente)
    if not hasattr(parent, "_async_bus"):
        parent._async_bus = None
        parent._async_branch_counter = 0

    return parent


# ═══════════════════════════════════════════════════════════════════════════
# Helper: Fake RLM class for tests (avoids real LM calls)
# ═══════════════════════════════════════════════════════════════════════════

class _FakeCompletion:
    def __init__(self, response: str, artifacts: dict | None = None):
        self.response = response
        self.artifacts = artifacts


class FakeRLM:
    """RLM fake que simula completion() sem LLM real."""
    def __init__(self, **kwargs):
        self.backend = kwargs.get("backend", "mock")
        self.backend_kwargs = kwargs.get("backend_kwargs", {})
        self.environment_type = kwargs.get("environment", "local")
        self.environment_kwargs = kwargs.get("environment_kwargs") or {}
        self.depth = kwargs.get("depth", 0)
        self.max_depth = kwargs.get("max_depth", 3)
        self.max_iterations = kwargs.get("max_iterations", 5)
        self.verbose = kwargs.get("verbose", False)
        self.event_bus = kwargs.get("event_bus")
        self._cancel_token = None
        self._shared_memory = None
        self._persistent_env = None
        self._async_bus = None
        self._async_branch_counter = 0

    def completion(self, prompt: str, capture_artifacts: bool = False, **kw):
        if capture_artifacts:
            return _FakeCompletion(
                response=f"processed: {prompt[:30]}",
                artifacts={"helper_fn": lambda x: x * 2, "data": [1, 2, 3]},
            )
        return _FakeCompletion(response=f"processed: {prompt[:30]}")


# ═══════════════════════════════════════════════════════════════════════════
# Lacuna 1: Memória compartilhada pai→filhos
# ═══════════════════════════════════════════════════════════════════════════

class TestGap1SharedMemory:
    """Verifica que _parent_memory é injetada via env_kwargs nos filhos."""

    def test_serial_child_receives_parent_memory(self):
        """sub_rlm() deve passar _parent_memory via env_kwargs."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_fn

        fake_memory = MagicMock()
        parent = _make_mock_parent(shared_memory=fake_memory)

        fn = make_sub_rlm_fn(parent, _rlm_cls=FakeRLM)
        result = fn("test task", timeout_s=5.0)

        assert "processed:" in result

    def test_serial_child_env_kwargs_contains_memory(self):
        """Verifica que env_kwargs do filho contém _parent_memory."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_fn

        fake_memory = MagicMock()
        parent = _make_mock_parent(shared_memory=fake_memory)

        created_children = []
        _orig_fake = FakeRLM.__init__

        def _tracking_init(self_inner, **kwargs):
            _orig_fake(self_inner, **kwargs)
            created_children.append(self_inner)

        with patch.object(FakeRLM, "__init__", _tracking_init):
            fn = make_sub_rlm_fn(parent, _rlm_cls=FakeRLM)
            fn("test", timeout_s=5.0)

        assert len(created_children) == 1
        child = created_children[0]
        assert child.environment_kwargs.get("_parent_memory") is fake_memory

    def test_no_memory_when_parent_has_none(self):
        """Se pai não tem memória, env_kwargs não deve conter _parent_memory."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_fn

        parent = _make_mock_parent(shared_memory=None)

        created_children = []
        _orig_fake = FakeRLM.__init__

        def _tracking_init(self_inner, **kwargs):
            _orig_fake(self_inner, **kwargs)
            created_children.append(self_inner)

        with patch.object(FakeRLM, "__init__", _tracking_init):
            fn = make_sub_rlm_fn(parent, _rlm_cls=FakeRLM)
            fn("test", timeout_s=5.0)

        child = created_children[0]
        assert "_parent_memory" not in child.environment_kwargs

    def test_async_child_receives_parent_memory(self):
        """sub_rlm_async() deve passar _parent_memory."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_async_fn

        fake_memory = MagicMock()
        parent = _make_mock_parent(shared_memory=fake_memory)

        created_children = []
        _orig_fake = FakeRLM.__init__

        def _tracking_init(self_inner, **kwargs):
            _orig_fake(self_inner, **kwargs)
            created_children.append(self_inner)

        with patch.object(FakeRLM, "__init__", _tracking_init):
            fn = make_sub_rlm_async_fn(parent, _rlm_cls=FakeRLM)
            handle = fn("async test", timeout_s=5.0)
            handle.result(timeout_s=5.0)

        assert len(created_children) == 1
        child = created_children[0]
        assert child.environment_kwargs.get("_parent_memory") is fake_memory

    def test_local_repl_uses_parent_memory(self):
        """LocalREPL deve reutilizar _parent_memory em vez de criar nova."""
        from rlm.environments.local_repl import LocalREPL

        fake_memory = MagicMock()
        repl = LocalREPL(
            lm_handler_address=("127.0.0.1", 9999),
            context_payload="test",
            _parent_memory=fake_memory,
        )
        assert repl._parent_memory is fake_memory
        repl.cleanup()


# ═══════════════════════════════════════════════════════════════════════════
# Lacuna 2: CancelToken propaga para filhos
# ═══════════════════════════════════════════════════════════════════════════

class TestGap2CancelTokenPropagation:
    """Verifica que _cancel_token do pai é propagado para filhos."""

    def test_serial_child_gets_cancel_token(self):
        """Filho serial deve ter _cancel_token derivado do pai."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_fn
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        parent_cts = CancellationTokenSource()
        parent = _make_mock_parent(cancel_token=parent_cts.token)

        created_children = []
        _orig_fake = FakeRLM.__init__

        def _tracking_init(self_inner, **kwargs):
            _orig_fake(self_inner, **kwargs)
            created_children.append(self_inner)

        with patch.object(FakeRLM, "__init__", _tracking_init):
            fn = make_sub_rlm_fn(parent, _rlm_cls=FakeRLM)
            fn("test", timeout_s=5.0)

        child = created_children[0]
        # Filho deve ter token não-NONE
        assert child._cancel_token is not None
        assert not child._cancel_token.is_cancelled

        # Quando pai cancela, filho deve ver cancelamento
        parent_cts.cancel("test abort")
        assert child._cancel_token.is_cancelled

    def test_async_child_gets_cancel_token(self):
        """Filho async deve ter _cancel_token derivado do pai."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_async_fn
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        parent_cts = CancellationTokenSource()
        parent = _make_mock_parent(cancel_token=parent_cts.token)

        created_children = []
        _orig_fake = FakeRLM.__init__

        def _tracking_init(self_inner, **kwargs):
            _orig_fake(self_inner, **kwargs)
            created_children.append(self_inner)

        with patch.object(FakeRLM, "__init__", _tracking_init):
            fn = make_sub_rlm_async_fn(parent, _rlm_cls=FakeRLM)
            handle = fn("async task", timeout_s=5.0)
            handle.result(timeout_s=5.0)

        child = created_children[0]
        assert child._cancel_token is not None
        # Cancel cascata
        parent_cts.cancel("abort")
        assert child._cancel_token.is_cancelled

    def test_cancel_token_none_parent_no_crash(self):
        """Se pai tem CancellationToken.NONE, filho não deve crashar."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_fn
        from rlm.core.lifecycle.cancellation import CancellationToken

        parent = _make_mock_parent(cancel_token=CancellationToken.NONE)
        fn = make_sub_rlm_fn(parent, _rlm_cls=FakeRLM)
        result = fn("test", timeout_s=5.0)
        assert "processed:" in result


# ═══════════════════════════════════════════════════════════════════════════
# Lacuna 3: SiblingBus unificado parallel+async
# ═══════════════════════════════════════════════════════════════════════════

class TestGap3UnifiedSiblingBus:
    """Verifica que parallel e async compartilham o mesmo SiblingBus."""

    def test_parallel_creates_bus_on_parent(self):
        """make_sub_rlm_parallel_fn deve criar _async_bus no pai se ausente."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_parallel_fn
        from rlm.core.orchestration.sibling_bus import SiblingBus

        parent = _make_mock_parent()
        parent._async_bus = None

        _par, _par_det = make_sub_rlm_parallel_fn(parent, _rlm_cls=FakeRLM)

        assert parent._async_bus is not None
        assert isinstance(parent._async_bus, SiblingBus)

    def test_async_reuses_existing_bus(self):
        """make_sub_rlm_async_fn deve reutilizar _async_bus se já existir."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_parallel_fn, make_sub_rlm_async_fn
        from rlm.core.orchestration.sibling_bus import SiblingBus

        parent = _make_mock_parent()
        parent._async_bus = None

        # Parallel cria o bus
        _par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=FakeRLM)
        bus_from_parallel = parent._async_bus

        # Async reutiliza
        _async_fn = make_sub_rlm_async_fn(parent, _rlm_cls=FakeRLM)
        bus_from_async = parent._async_bus

        assert bus_from_parallel is bus_from_async

    def test_parallel_reuses_async_bus(self):
        """make_sub_rlm_parallel_fn deve reutilizar _async_bus criado por async."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_parallel_fn, make_sub_rlm_async_fn
        from rlm.core.orchestration.sibling_bus import SiblingBus

        parent = _make_mock_parent()
        parent._async_bus = None

        # Async cria o bus
        _async_fn = make_sub_rlm_async_fn(parent, _rlm_cls=FakeRLM)
        bus_from_async = parent._async_bus

        # Parallel reutiliza
        _par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=FakeRLM)
        bus_from_parallel = parent._async_bus

        assert bus_from_async is bus_from_parallel


# ═══════════════════════════════════════════════════════════════════════════
# Lacuna 4: EventBus propaga para filhos
# ═══════════════════════════════════════════════════════════════════════════

class TestGap4EventBusPropagation:
    """Verifica que event_bus do pai é passado para filhos."""

    def test_serial_child_receives_event_bus(self):
        """Filho serial deve receber event_bus do pai."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_fn

        mock_bus = MagicMock()
        parent = _make_mock_parent(event_bus=mock_bus)

        created_children = []
        _orig_fake = FakeRLM.__init__

        def _tracking_init(self_inner, **kwargs):
            _orig_fake(self_inner, **kwargs)
            created_children.append(self_inner)

        with patch.object(FakeRLM, "__init__", _tracking_init):
            fn = make_sub_rlm_fn(parent, _rlm_cls=FakeRLM)
            fn("test", timeout_s=5.0)

        child = created_children[0]
        assert child.event_bus is mock_bus

    def test_async_child_receives_event_bus(self):
        """Filho async deve receber event_bus do pai."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_async_fn

        mock_bus = MagicMock()
        parent = _make_mock_parent(event_bus=mock_bus)

        created_children = []
        _orig_fake = FakeRLM.__init__

        def _tracking_init(self_inner, **kwargs):
            _orig_fake(self_inner, **kwargs)
            created_children.append(self_inner)

        with patch.object(FakeRLM, "__init__", _tracking_init):
            fn = make_sub_rlm_async_fn(parent, _rlm_cls=FakeRLM)
            handle = fn("test", timeout_s=5.0)
            handle.result(timeout_s=5.0)

        child = created_children[0]
        assert child.event_bus is mock_bus

    def test_no_event_bus_doesnt_crash(self):
        """Se pai não tem event_bus, filhos recebem None sem crash."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_fn

        parent = _make_mock_parent(event_bus=None)
        fn = make_sub_rlm_fn(parent, _rlm_cls=FakeRLM)
        result = fn("test", timeout_s=5.0)
        assert "processed:" in result


# ═══════════════════════════════════════════════════════════════════════════
# Lacuna 5: Artefatos em sub_rlm_parallel()
# ═══════════════════════════════════════════════════════════════════════════

class TestGap5ParallelArtifacts:
    """Verifica que sub_rlm_parallel() suporta return_artifacts=True."""

    def test_parallel_returns_artifact_results(self):
        """sub_rlm_parallel com return_artifacts=True retorna SubRLMArtifactResult."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_parallel_fn, SubRLMArtifactResult

        parent = _make_mock_parent()

        _par, _par_det = make_sub_rlm_parallel_fn(parent, _rlm_cls=FakeRLM)
        results = _par(
            ["task1", "task2"],
            return_artifacts=True,
            timeout_s=10.0,
            max_iterations=3,
        )

        assert len(results) == 2
        for r in results:
            assert isinstance(r, SubRLMArtifactResult)
            assert r.artifacts is not None
            assert "helper_fn" in r.artifacts
            assert callable(r.artifacts["helper_fn"])
            assert r.artifacts["data"] == [1, 2, 3]

    def test_parallel_without_artifacts_returns_strings(self):
        """sub_rlm_parallel sem return_artifacts retorna list[str] normalmente."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_parallel_fn

        parent = _make_mock_parent()

        _par, _par_det = make_sub_rlm_parallel_fn(parent, _rlm_cls=FakeRLM)
        results = _par(
            ["task1", "task2"],
            timeout_s=10.0,
            max_iterations=3,
        )

        assert len(results) == 2
        for r in results:
            assert isinstance(r, str)
            assert "processed:" in r

    def test_parallel_empty_tasks_returns_empty(self):
        """sub_rlm_parallel([]) retorna [] independente de return_artifacts."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_parallel_fn

        parent = _make_mock_parent()
        _par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=FakeRLM)

        assert _par([], return_artifacts=True) == []
        assert _par([], return_artifacts=False) == []

    def test_artifact_callables_and_values(self):
        """SubRLMArtifactResult.callables() e .values() filtram corretamente."""
        from rlm.core.engine.sub_rlm import SubRLMArtifactResult

        art = SubRLMArtifactResult(
            answer="ok",
            artifacts={
                "fn": lambda x: x,
                "data": [1, 2],
                "config": {"k": "v"},
            },
            depth=1,
        )
        assert "fn" in art.callables()
        assert "data" not in art.callables()
        assert "data" in art.values()
        assert "config" in art.values()
        assert "fn" not in art.values()

    def test_artifact_as_custom_tools(self):
        """SubRLMArtifactResult.as_custom_tools() retorna dict completo."""
        from rlm.core.engine.sub_rlm import SubRLMArtifactResult

        fn = lambda x: x * 2
        art = SubRLMArtifactResult(
            answer="ok",
            artifacts={"double": fn, "pi": 3.14},
            depth=1,
        )
        tools = art.as_custom_tools()
        assert tools["double"] is fn
        assert tools["pi"] == 3.14


# ═══════════════════════════════════════════════════════════════════════════
# Testes de integração cruzada entre lacunas
# ═══════════════════════════════════════════════════════════════════════════

class TestCrossGapIntegration:
    """Testes que verificam interações entre múltiplas lacunas."""

    def test_serial_child_gets_all_propagations(self):
        """Filho serial deve receber memória, cancel_token e event_bus."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_fn
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        fake_memory = MagicMock()
        mock_bus = MagicMock()
        parent_cts = CancellationTokenSource()
        parent = _make_mock_parent(
            shared_memory=fake_memory,
            event_bus=mock_bus,
            cancel_token=parent_cts.token,
        )

        created_children = []
        _orig_fake = FakeRLM.__init__

        def _tracking_init(self_inner, **kwargs):
            _orig_fake(self_inner, **kwargs)
            created_children.append(self_inner)

        with patch.object(FakeRLM, "__init__", _tracking_init):
            fn = make_sub_rlm_fn(parent, _rlm_cls=FakeRLM)
            fn("full integration test", timeout_s=5.0)

        child = created_children[0]
        # Lacuna 1: memória
        assert child.environment_kwargs.get("_parent_memory") is fake_memory
        # Lacuna 4: event_bus
        assert child.event_bus is mock_bus
        # Lacuna 2: cancel_token
        assert child._cancel_token is not None
        assert not child._cancel_token.is_cancelled
        parent_cts.cancel("cascade test")
        assert child._cancel_token.is_cancelled

    def test_depth_guard_still_works(self):
        """Depth guard deve continuar funcionando após todas as modificações."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_fn, SubRLMDepthError

        parent = _make_mock_parent(depth=2, max_depth=3)
        fn = make_sub_rlm_fn(parent, _rlm_cls=FakeRLM)
        with pytest.raises(SubRLMDepthError):
            fn("should fail", timeout_s=5.0)

    def test_parallel_depth_guard_still_works(self):
        """Parallel depth guard deve funcionar após modificações."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_parallel_fn, SubRLMDepthError

        parent = _make_mock_parent(depth=2, max_depth=3)
        _par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=FakeRLM)
        with pytest.raises(SubRLMDepthError):
            _par(["task1", "task2"], timeout_s=5.0)


# ═══════════════════════════════════════════════════════════════════════════
# Bridge bidirecional: CancellationToken ↔ threading.Event
# ═══════════════════════════════════════════════════════════════════════════

class TestCancelTokenEventBridge:
    """Verifica que CancellationToken e threading.Event são sincronizados."""

    def test_serial_token_cancel_sets_event(self):
        """Cancelar token do pai deve setar _cancel_event no filho serial."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_fn
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        parent_cts = CancellationTokenSource()
        parent = _make_mock_parent(cancel_token=parent_cts.token)

        created_children = []
        _orig_fake = FakeRLM.__init__

        def _tracking_init(self_inner, **kwargs):
            _orig_fake(self_inner, **kwargs)
            created_children.append(self_inner)

        with patch.object(FakeRLM, "__init__", _tracking_init):
            fn = make_sub_rlm_fn(parent, _rlm_cls=FakeRLM)
            # Passa _cancel_event explícito para a chamada serial
            cancel_evt = threading.Event()
            fn("test", timeout_s=5.0, _cancel_event=cancel_evt)

        child = created_children[0]
        assert not cancel_evt.is_set()
        # Bridge: token cancelado → event sinalizado
        parent_cts.cancel("bridge test")
        assert child._cancel_token.is_cancelled
        assert cancel_evt.is_set(), (
            "Bridge falhou: token cancelado mas threading.Event não foi sinalizado"
        )

    def test_async_token_cancel_sets_event(self):
        """Cancelar token do pai deve setar cancel_event no filho async."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_async_fn
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        parent_cts = CancellationTokenSource()
        parent = _make_mock_parent(cancel_token=parent_cts.token)

        fn = make_sub_rlm_async_fn(parent, _rlm_cls=FakeRLM)
        handle = fn("async task", timeout_s=5.0)
        handle.result(timeout_s=5.0)

        # O handle tem o cancel_event interno
        assert not handle._cancel_event.is_set()
        # Bridge: token cancelado → event sinalizado
        parent_cts.cancel("async bridge test")
        assert handle._cancel_event.is_set(), (
            "Bridge falhou: token cancelado mas AsyncHandle._cancel_event não sinalizado"
        )

    def test_async_handle_cancel_sets_token(self):
        """AsyncHandle.cancel() deve cancelar tanto event quanto token."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_async_fn
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        parent_cts = CancellationTokenSource()
        parent = _make_mock_parent(cancel_token=parent_cts.token)

        created_children = []
        _orig_fake = FakeRLM.__init__

        def _tracking_init(self_inner, **kwargs):
            _orig_fake(self_inner, **kwargs)
            created_children.append(self_inner)

        with patch.object(FakeRLM, "__init__", _tracking_init):
            fn = make_sub_rlm_async_fn(parent, _rlm_cls=FakeRLM)
            handle = fn("task", timeout_s=5.0)
            handle.result(timeout_s=5.0)

        child = created_children[0]
        assert not child._cancel_token.is_cancelled
        assert not handle._cancel_event.is_set()

        # Bridge reverso: handle.cancel() → event + token
        handle.cancel()
        assert handle._cancel_event.is_set()
        assert child._cancel_token.is_cancelled, (
            "Bridge reverso falhou: handle.cancel() não cancelou o CancellationToken"
        )

    def test_async_handle_cancel_without_token_no_crash(self):
        """AsyncHandle.cancel() deve funcionar mesmo sem CancellationToken."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_async_fn
        from rlm.core.lifecycle.cancellation import CancellationToken

        parent = _make_mock_parent(cancel_token=CancellationToken.NONE)
        fn = make_sub_rlm_async_fn(parent, _rlm_cls=FakeRLM)
        handle = fn("task", timeout_s=5.0)
        handle.result(timeout_s=5.0)
        # Deve funcionar sem crash mesmo sem token source
        handle.cancel()
        assert handle._cancel_event.is_set()

    def test_parallel_token_cancel_sets_branch_events(self):
        """Cancelar token do pai deve setar cancel_events dos filhos paralelos."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_parallel_fn
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        parent_cts = CancellationTokenSource()
        parent = _make_mock_parent(cancel_token=parent_cts.token)

        created_children = []
        _orig_fake = FakeRLM.__init__

        def _tracking_init(self_inner, **kwargs):
            _orig_fake(self_inner, **kwargs)
            created_children.append(self_inner)

        with patch.object(FakeRLM, "__init__", _tracking_init):
            _par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=FakeRLM)
            results = _par(["task1", "task2"], timeout_s=10.0, max_iterations=3)

        # Todos os filhos devem ter tokens derivados do pai
        assert len(created_children) == 2
        for child in created_children:
            assert child._cancel_token is not None
            assert not child._cancel_token.is_cancelled

        # Cancelar pai → todos os filhos cancelados via token hierarchy
        parent_cts.cancel("parallel bridge test")
        for child in created_children:
            assert child._cancel_token.is_cancelled

    def test_grandchild_cascade_via_bridge(self):
        """Cancelar avô deve propagar token→event em toda a cadeia."""
        from rlm.core.lifecycle.cancellation import CancellationTokenSource

        # Simula: avô → pai → neto, todos bridged
        grandparent_cts = CancellationTokenSource()
        parent_cts = CancellationTokenSource(parent=grandparent_cts.token)
        child_cts = CancellationTokenSource(parent=parent_cts.token)

        # Bridge: child token → child event
        child_event = threading.Event()
        child_cts.token.on_cancelled(lambda: child_event.set())

        assert not child_event.is_set()
        assert not child_cts.token.is_cancelled

        # Cancelar avô → cascata até neto
        grandparent_cts.cancel("top-level abort")
        assert parent_cts.token.is_cancelled
        assert child_cts.token.is_cancelled
        assert child_event.is_set(), (
            "Cascata falhou: cancel do avô não chegou ao threading.Event do neto"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Teste: _extract_turn_outcome — visibilidade cross-turn
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractTurnOutcome:
    """Testa extração de resumo conciso de turnos anteriores."""

    @staticmethod
    def _get_mixin():
        from rlm.core.engine.rlm_loop_mixin import RLMLoopMixin
        return RLMLoopMixin

    def test_empty_history(self):
        mixin = self._get_mixin()
        result = mixin._extract_turn_outcome([])
        assert "LAST TURN OUTCOME" in result
        assert "no errors detected" in result

    def test_detects_error_in_tool_output(self):
        mixin = self._get_mixin()
        history = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Run sub_rlm"},
            {"role": "assistant", "content": "```repl\nsub_rlm('do X')\n```"},
            {"role": "tool", "content": "SubRLMDepthError: sub_rlm: profundidade máxima atingida (depth=0, max_depth=1)"},
        ]
        result = mixin._extract_turn_outcome(history)
        assert "ERRORS detected" in result
        assert "SubRLMDepthError" in result

    def test_detects_traceback(self):
        mixin = self._get_mixin()
        history = [
            {"role": "assistant", "content": "```repl\nx = 1/0\n```"},
            {"role": "user", "content": "Traceback (most recent call last):\n  File ...\nZeroDivisionError: division by zero"},
        ]
        result = mixin._extract_turn_outcome(history)
        assert "ERRORS detected" in result
        assert "ZeroDivisionError" in result

    def test_no_errors_clean_turn(self):
        mixin = self._get_mixin()
        history = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "FINAL(\"Hello, how can I help?\")"},
        ]
        result = mixin._extract_turn_outcome(history)
        assert "no errors detected" in result
        assert "FINAL" in result  # preview da última resposta assistant

    def test_last_response_preview_truncated(self):
        mixin = self._get_mixin()
        long_response = "A" * 500
        history = [
            {"role": "assistant", "content": long_response},
        ]
        result = mixin._extract_turn_outcome(history)
        # Preview deve ter no máximo 300 chars
        assert "AAAA" in result
        assert len(result) < 500  # truncado, não inteiro

    def test_multiple_errors_capped_at_5(self):
        mixin = self._get_mixin()
        history = []
        for i in range(10):
            history.append({"role": "user", "content": f"Error line {i}: something failed here"})
        result = mixin._extract_turn_outcome(history)
        # Deve ter no máximo 5 bullets de erro
        assert result.count("•") <= 5


# ──────────────────────────────────────────────────────────────────────────────
# Lacuna 10: sub_rlm child root_prompt injection
# ──────────────────────────────────────────────────────────────────────────────

class TestSubRLMRootPromptInjection:
    """Verifica que sub_rlm passa root_prompt=task ao child.completion().

    Antes desse fix, o user message do filho era genérico —
    o LLM não via a task no prompt e declarava 'nenhum contexto fornecido'.
    """

    def test_serial_sub_rlm_passes_root_prompt(self):
        """child.completion() deve receber root_prompt igual à task."""
        from unittest.mock import MagicMock
        from rlm.core.engine.sub_rlm import make_sub_rlm_fn

        # Cria pai mock
        parent = MagicMock()
        parent.depth = 0
        parent.max_depth = 3
        parent.backend = "openai"
        parent.backend_kwargs = {"model": "gpt-4o-mini"}
        parent.environment_type = "local"
        parent.environment_kwargs = {}
        parent.event_bus = None
        parent._cancel_token = MagicMock()
        parent._cancel_token.is_cancelled = False
        parent._shared_memory = None
        parent._persistent_env = None
        parent._async_bus = None
        parent._async_branch_counter = 0

        # Filho mock que captura os kwargs de completion()
        captured = {}
        fake_completion = MagicMock()
        fake_completion.response = "ok"

        class FakeRLM:
            def __init__(self, **kw):
                self._cancel_token = MagicMock()
                self._cancel_token.is_cancelled = False

            def completion(self, prompt, root_prompt=None, **kw):
                captured["prompt"] = prompt
                captured["root_prompt"] = root_prompt
                return fake_completion

        fn = make_sub_rlm_fn(parent, _rlm_cls=FakeRLM)
        task_text = "Calcule 2+2 e retorne"
        result = fn(task_text, timeout_s=10)

        assert result == "ok"
        assert captured.get("root_prompt") is not None, (
            "root_prompt deve ser passado ao child — era None antes do fix"
        )
        assert task_text in captured["root_prompt"]

    def test_serial_sub_rlm_artifacts_passes_root_prompt(self):
        """Mesmo caminho com return_artifacts=True."""
        from unittest.mock import MagicMock
        from rlm.core.engine.sub_rlm import make_sub_rlm_fn

        parent = MagicMock()
        parent.depth = 0
        parent.max_depth = 3
        parent.backend = "openai"
        parent.backend_kwargs = {"model": "gpt-4o-mini"}
        parent.environment_type = "local"
        parent.environment_kwargs = {}
        parent.event_bus = None
        parent._cancel_token = MagicMock()
        parent._cancel_token.is_cancelled = False
        parent._shared_memory = None
        parent._persistent_env = None
        parent._async_bus = None
        parent._async_branch_counter = 0

        captured = {}
        fake_completion = MagicMock()
        fake_completion.response = "ok"
        fake_completion.artifacts = {}

        class FakeRLM:
            def __init__(self, **kw):
                self._cancel_token = MagicMock()
                self._cancel_token.is_cancelled = False

            def completion(self, prompt, root_prompt=None, capture_artifacts=False, **kw):
                captured["root_prompt"] = root_prompt
                captured["capture_artifacts"] = capture_artifacts
                return fake_completion

        fn = make_sub_rlm_fn(parent, _rlm_cls=FakeRLM)
        fn("Gere JSON com nomes", timeout_s=10, return_artifacts=True)

        assert captured.get("root_prompt") is not None
        assert captured.get("capture_artifacts") is True

    def test_user_prompt_includes_task_preview_when_root_prompt_set(self):
        """build_user_prompt com root_prompt deve incluir preview da task."""
        from rlm.utils.prompts import build_user_prompt

        msg = build_user_prompt(root_prompt="Analise vendas.csv", iteration=0)
        assert "Analise vendas.csv" in msg["content"]
        assert "Current task preview" in msg["content"]

    def test_user_prompt_generic_when_root_prompt_none(self):
        """build_user_prompt sem root_prompt é genérico (comportamento anterior)."""
        from rlm.utils.prompts import build_user_prompt

        msg = build_user_prompt(root_prompt=None, iteration=0)
        assert "Current task preview" not in msg["content"]
        assert "Use the REPL" in msg["content"]


# ---------------------------------------------------------------------------
# Supervisor: per-session execution lock (TUI ↔ Telegram contention fix)
# ---------------------------------------------------------------------------

class TestSupervisorSessionContention:
    """Verifica que chamadas concorrentes na mesma sessão AGUARDAM
    em vez de rejeitar imediatamente com 'Session is already running'.

    Antes desse fix, o Telegram recebia erro porque o TUI já estava usando
    a sessão — o supervisor retornava error sem esperar.
    """

    def _make_session(self, session_id="test-session"):
        from unittest.mock import MagicMock
        from rlm.core.session import SessionRecord
        session = SessionRecord(session_id=session_id, client_id="test:0", user_id="main")
        rlm = MagicMock()
        rlm.max_iterations = 5
        rlm._abort_event = None
        rlm._cancel_token = MagicMock()
        completion_result = MagicMock()
        completion_result.response = "ok"
        completion_result.usage_summary = None
        rlm.completion.return_value = completion_result
        rlm.start_turn_telemetry = None
        rlm.finish_turn_telemetry = None
        session.rlm_instance = rlm
        session.status = "idle"
        session.total_tokens_used = 0
        session.total_completions = 0
        session.last_error = None
        return session

    def test_second_caller_waits_and_succeeds(self):
        """Duas threads na mesma sessão: a segunda espera e também retorna 'completed'."""
        import threading
        from rlm.core.orchestration.supervisor import RLMSupervisor, SupervisorConfig

        session = self._make_session()

        # A primeira chamada demora 0.3s
        call_count = {"n": 0}
        original_completion = session.rlm_instance.completion

        def slow_completion(prompt, root_prompt=None, mcts_branches=0, **kw):
            call_count["n"] += 1
            import time
            time.sleep(0.3)
            return original_completion(prompt, root_prompt=root_prompt, mcts_branches=mcts_branches, **kw)

        session.rlm_instance.completion = slow_completion

        supervisor = RLMSupervisor(
            default_config=SupervisorConfig(queue_timeout=5.0)
        )

        results = [None, None]

        def run(idx, delay=0.0):
            import time
            if delay:
                time.sleep(delay)
            results[idx] = supervisor.execute(session, "hello")

        t1 = threading.Thread(target=run, args=(0,))
        t2 = threading.Thread(target=run, args=(1, 0.05))  # começa 50ms depois
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert results[0] is not None
        assert results[1] is not None
        assert results[0].status == "completed", f"Thread 1: {results[0].status} - {results[0].error_detail}"
        assert results[1].status == "completed", f"Thread 2: {results[1].status} - {results[1].error_detail}"
        assert call_count["n"] == 2, "Ambas as chamadas devem ter sido executadas"

    def test_queue_timeout_zero_rejects_immediately(self):
        """Com queue_timeout=0, a segunda chamada é rejeitada (comportamento legado)."""
        import threading
        from rlm.core.orchestration.supervisor import RLMSupervisor, SupervisorConfig

        session = self._make_session()

        original_completion = session.rlm_instance.completion

        def slow_completion(prompt, root_prompt=None, mcts_branches=0, **kw):
            import time
            time.sleep(0.5)
            return original_completion(prompt, root_prompt=root_prompt, mcts_branches=mcts_branches, **kw)

        session.rlm_instance.completion = slow_completion

        supervisor = RLMSupervisor(
            default_config=SupervisorConfig(queue_timeout=0)
        )

        results = [None, None]

        def run(idx, delay=0.0):
            import time
            if delay:
                time.sleep(delay)
            results[idx] = supervisor.execute(session, "hello")

        t1 = threading.Thread(target=run, args=(0,))
        t2 = threading.Thread(target=run, args=(1, 0.05))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert results[0].status == "completed"
        assert results[1].status == "error"
        assert "timed out" in results[1].error_detail.lower() or "wait" in results[1].error_detail.lower()

    def test_different_sessions_run_concurrently(self):
        """Sessões diferentes NÃO se bloqueiam: ambas executam em paralelo."""
        import threading
        import time as _time
        from rlm.core.orchestration.supervisor import RLMSupervisor, SupervisorConfig

        s1 = self._make_session("session-A")
        s2 = self._make_session("session-B")

        original1 = s1.rlm_instance.completion
        original2 = s2.rlm_instance.completion

        def slow_completion_1(prompt, root_prompt=None, mcts_branches=0, **kw):
            import time
            time.sleep(0.3)
            return original1(prompt, root_prompt=root_prompt, mcts_branches=mcts_branches, **kw)

        def slow_completion_2(prompt, root_prompt=None, mcts_branches=0, **kw):
            import time
            time.sleep(0.3)
            return original2(prompt, root_prompt=root_prompt, mcts_branches=mcts_branches, **kw)

        s1.rlm_instance.completion = slow_completion_1
        s2.rlm_instance.completion = slow_completion_2

        supervisor = RLMSupervisor(
            default_config=SupervisorConfig(queue_timeout=5.0)
        )

        results = [None, None]
        start = _time.perf_counter()

        def run(idx, session):
            results[idx] = supervisor.execute(session, "hello")

        t1 = threading.Thread(target=run, args=(0, s1))
        t2 = threading.Thread(target=run, args=(1, s2))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        elapsed = _time.perf_counter() - start

        assert results[0].status == "completed"
        assert results[1].status == "completed"
        # Se fossem sequenciais, levariam ~0.6s. Em paralelo, ~0.3s.
        assert elapsed < 0.55, f"Sessões diferentes devem rodar em paralelo, mas levaram {elapsed:.2f}s"
