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
        from rlm.core.cancellation import CancellationToken
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

    def completion(self, prompt: str, capture_artifacts: bool = False):
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
        from rlm.core.sub_rlm import make_sub_rlm_fn

        fake_memory = MagicMock()
        parent = _make_mock_parent(shared_memory=fake_memory)

        fn = make_sub_rlm_fn(parent, _rlm_cls=FakeRLM)
        result = fn("test task", timeout_s=5.0)

        assert "processed:" in result

    def test_serial_child_env_kwargs_contains_memory(self):
        """Verifica que env_kwargs do filho contém _parent_memory."""
        from rlm.core.sub_rlm import make_sub_rlm_fn

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
        from rlm.core.sub_rlm import make_sub_rlm_fn

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
        from rlm.core.sub_rlm import make_sub_rlm_async_fn

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
        from rlm.core.sub_rlm import make_sub_rlm_fn
        from rlm.core.cancellation import CancellationTokenSource

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
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        from rlm.core.cancellation import CancellationTokenSource

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
        from rlm.core.sub_rlm import make_sub_rlm_fn
        from rlm.core.cancellation import CancellationToken

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
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        from rlm.core.sibling_bus import SiblingBus

        parent = _make_mock_parent()
        parent._async_bus = None

        _par, _par_det = make_sub_rlm_parallel_fn(parent, _rlm_cls=FakeRLM)

        assert parent._async_bus is not None
        assert isinstance(parent._async_bus, SiblingBus)

    def test_async_reuses_existing_bus(self):
        """make_sub_rlm_async_fn deve reutilizar _async_bus se já existir."""
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn, make_sub_rlm_async_fn
        from rlm.core.sibling_bus import SiblingBus

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
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn, make_sub_rlm_async_fn
        from rlm.core.sibling_bus import SiblingBus

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
        from rlm.core.sub_rlm import make_sub_rlm_fn

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
        from rlm.core.sub_rlm import make_sub_rlm_async_fn

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
        from rlm.core.sub_rlm import make_sub_rlm_fn

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
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn, SubRLMArtifactResult

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
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn

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
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn

        parent = _make_mock_parent()
        _par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=FakeRLM)

        assert _par([], return_artifacts=True) == []
        assert _par([], return_artifacts=False) == []

    def test_artifact_callables_and_values(self):
        """SubRLMArtifactResult.callables() e .values() filtram corretamente."""
        from rlm.core.sub_rlm import SubRLMArtifactResult

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
        from rlm.core.sub_rlm import SubRLMArtifactResult

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
        from rlm.core.sub_rlm import make_sub_rlm_fn
        from rlm.core.cancellation import CancellationTokenSource

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
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMDepthError

        parent = _make_mock_parent(depth=2, max_depth=3)
        fn = make_sub_rlm_fn(parent, _rlm_cls=FakeRLM)
        with pytest.raises(SubRLMDepthError):
            fn("should fail", timeout_s=5.0)

    def test_parallel_depth_guard_still_works(self):
        """Parallel depth guard deve funcionar após modificações."""
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn, SubRLMDepthError

        parent = _make_mock_parent(depth=2, max_depth=3)
        _par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=FakeRLM)
        with pytest.raises(SubRLMDepthError):
            _par(["task1", "task2"], timeout_s=5.0)
