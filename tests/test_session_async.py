"""
Testes — RLMSession + sub_rlm_async + parent_log

Cobre:
  1. RLMSession.chat()       — mantém contexto entre turnos
  2. SessionAsyncHandle      — turno não bloqueia, result() retorna resposta
  3. parent_log injection    — filho publica, pai lê via log_poll()
  4. AsyncHandle             — sub_rlm_async fire-and-forget
  5. make_sub_rlm_async_fn   — factory, atributos, depth guard

Execute:
    pytest tests/test_session_async.py -v
"""
from __future__ import annotations

import queue
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers compartilhados
# ---------------------------------------------------------------------------

def _make_parent_mock(depth: int = 0, max_depth: int = 3) -> MagicMock:
    parent = MagicMock()
    parent.depth = depth
    parent.max_depth = max_depth
    parent.backend = "openai"
    parent.backend_kwargs = {"model_name": "gpt-5-mini"}
    parent.environment_type = "local"
    parent.environment_kwargs = {}
    return parent


def _make_parent_simple(depth: int = 0, max_depth: int = 3):
    """Pai sem MagicMock — atributos ausentes dão AttributeError, não mock.
    Necessário para testes que verificam criação real de SiblingBus.
    """
    import types
    parent = types.SimpleNamespace()
    parent.depth = depth
    parent.max_depth = max_depth
    parent.backend = "openai"
    parent.backend_kwargs = {"model_name": "gpt-5-mini"}
    parent.environment_type = "local"
    parent.environment_kwargs = {}
    parent.completion = MagicMock(return_value=MagicMock(response="ok"))
    return parent


def _make_mock_rlm_cls(response: str = "resultado do filho"):
    """Classe RLM mockada que retorna `response` imediatamente."""
    mock_completion = MagicMock()
    mock_completion.response = response
    mock_instance = MagicMock()
    mock_instance.completion.return_value = mock_completion
    mock_cls = MagicMock(return_value=mock_instance)
    return mock_cls, mock_instance


def _make_mock_rlm_cls_with_log(response: str, log_msg: str):
    """Classe RLM mockada que publica uma mensagem via parent_log antes de responder."""
    mock_completion = MagicMock()
    mock_completion.response = response
    mock_instance = MagicMock()

    def _completion_with_log(prompt):
        # Simula filho escrevendo no canal pai
        env = mock_instance._env
        if env and hasattr(env, "globals") and "parent_log" in env.globals:
            env.globals["parent_log"](log_msg)
        return mock_completion

    mock_instance.completion.side_effect = _completion_with_log
    mock_cls = MagicMock(return_value=mock_instance)
    return mock_cls, mock_instance


# ===========================================================================
# 1. AsyncHandle — comportamento básico
# ===========================================================================

class TestAsyncHandle:

    def _make_handle(self, response: str = "ok", delay_s: float = 0.0):
        from rlm.core.sub_rlm import AsyncHandle
        result_holder: list = []
        error_holder: list = []
        log_q: queue.Queue = queue.Queue()

        def _work():
            if delay_s:
                time.sleep(delay_s)
            result_holder.append(response)

        t = threading.Thread(target=_work, daemon=True)
        t.start()
        return AsyncHandle(
            task="tarefa-teste",
            depth=1,
            thread=t,
            result_holder=result_holder,
            error_holder=error_holder,
            log_queue=log_q,
        )

    def test_is_done_false_while_running(self):
        handle = self._make_handle(delay_s=5.0)
        assert handle.is_done is False
        handle._thread.join(timeout=0)  # não espera

    def test_is_done_true_after_finish(self):
        handle = self._make_handle(delay_s=0.0)
        handle._thread.join(timeout=2.0)
        assert handle.is_done is True

    def test_result_returns_response(self):
        handle = self._make_handle(response="resposta final")
        assert handle.result(timeout_s=3.0) == "resposta final"

    def test_result_timeout_raises(self):
        from rlm.core.sub_rlm import SubRLMTimeoutError
        handle = self._make_handle(delay_s=10.0)
        with pytest.raises(SubRLMTimeoutError):
            handle.result(timeout_s=0.05)

    def test_result_propagates_error(self):
        from rlm.core.sub_rlm import AsyncHandle, SubRLMError
        error_holder: list = []
        result_holder: list = []
        log_q: queue.Queue = queue.Queue()

        def _fail():
            error_holder.append(ValueError("algo deu errado"))

        t = threading.Thread(target=_fail, daemon=True)
        t.start()
        handle = AsyncHandle("falha", 1, t, result_holder, error_holder, log_q)

        with pytest.raises(SubRLMError, match="filho falhou"):
            handle.result(timeout_s=3.0)

    def test_log_poll_returns_messages(self):
        from rlm.core.sub_rlm import AsyncHandle
        log_q: queue.Queue = queue.Queue()
        log_q.put("msg 1")
        log_q.put("msg 2")

        t = threading.Thread(target=lambda: None, daemon=True)
        t.start()
        handle = AsyncHandle("t", 1, t, [], [], log_q)

        msgs = handle.log_poll()
        assert msgs == ["msg 1", "msg 2"]

    def test_log_poll_empty_returns_empty_list(self):
        from rlm.core.sub_rlm import AsyncHandle
        t = threading.Thread(target=lambda: None, daemon=True)
        t.start()
        handle = AsyncHandle("t", 1, t, [], [], queue.Queue())
        assert handle.log_poll() == []

    def test_log_poll_drains_incrementally(self):
        from rlm.core.sub_rlm import AsyncHandle
        log_q: queue.Queue = queue.Queue()
        t = threading.Thread(target=lambda: None, daemon=True)
        t.start()
        handle = AsyncHandle("t", 1, t, [], [], log_q)

        log_q.put("a")
        first = handle.log_poll()
        log_q.put("b")
        second = handle.log_poll()

        assert first == ["a"]
        assert second == ["b"]

    def test_elapsed_s_increases(self):
        handle = self._make_handle(delay_s=0.1)
        t0 = handle.elapsed_s
        time.sleep(0.05)
        t1 = handle.elapsed_s
        assert t1 > t0

    def test_repr_shows_status(self):
        handle = self._make_handle(response="x")
        handle._thread.join(timeout=2.0)
        assert "done" in repr(handle) or "running" in repr(handle)

    def test_cancel_sets_flag(self):
        handle = self._make_handle(delay_s=5.0)
        handle.cancel()
        assert handle._cancelled is True

    def test_cancel_sets_event(self):
        """cancel() deve acionar o threading.Event que o filho lê."""
        handle = self._make_handle(delay_s=5.0)
        assert not handle._cancel_event.is_set()
        handle.cancel()
        assert handle._cancel_event.is_set()

    def test_cancel_event_is_threading_event(self):
        """_cancel_event deve ser threading.Event mesmo sem cancel_event explicito."""
        handle = self._make_handle()
        assert isinstance(handle._cancel_event, threading.Event)

    def test_cancel_event_param_is_shared(self):
        """AsyncHandle aceita cancel_event externo e o compartilha."""
        from rlm.core.sub_rlm import AsyncHandle
        event = threading.Event()
        t = threading.Thread(target=lambda: None, daemon=True)
        t.start()
        handle = AsyncHandle("t", 1, t, [], [], queue.Queue(), cancel_event=event)
        handle.cancel()
        assert event.is_set()  # mesmo objeto


# ===========================================================================
# 2. make_sub_rlm_async_fn — factory
# ===========================================================================

class TestMakeSubRLMAsyncFn:

    def test_returns_callable(self):
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock()
        fn = make_sub_rlm_async_fn(parent)
        assert callable(fn)

    def test_callable_named_sub_rlm_async(self):
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock()
        fn = make_sub_rlm_async_fn(parent)
        assert fn.__name__ == "sub_rlm_async"

    def test_has_parent_depth_attribute(self):
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock(depth=1, max_depth=4)
        fn = make_sub_rlm_async_fn(parent)
        assert fn._parent_depth == 1
        assert fn._parent_max_depth == 4

    def test_depth_guard_raises_on_max(self):
        from rlm.core.sub_rlm import make_sub_rlm_async_fn, SubRLMDepthError
        parent = _make_parent_mock(depth=2, max_depth=2)
        fn = make_sub_rlm_async_fn(parent)
        with pytest.raises(SubRLMDepthError):
            fn("tarefa")

    def test_returns_async_handle_immediately(self):
        from rlm.core.sub_rlm import make_sub_rlm_async_fn, AsyncHandle
        parent = _make_parent_mock(depth=0, max_depth=3)
        mock_cls, _ = _make_mock_rlm_cls("resposta async")
        fn = make_sub_rlm_async_fn(parent, _rlm_cls=mock_cls)

        t_start = time.perf_counter()
        handle = fn("tarefa leve")
        elapsed = time.perf_counter() - t_start

        assert isinstance(handle, AsyncHandle)
        assert elapsed < 0.2

    def test_handle_result_correct(self):
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock(depth=0, max_depth=3)
        mock_cls, _ = _make_mock_rlm_cls("valor esperado")
        fn = make_sub_rlm_async_fn(parent, _rlm_cls=mock_cls)

        handle = fn("tarefa")
        assert handle.result(timeout_s=5.0) == "valor esperado"

    def test_log_queue_injected_into_child_env_kwargs(self):
        """_parent_log_queue deve ser passado nos environment_kwargs do filho."""
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock(depth=0, max_depth=3)
        captured_kwargs: list[dict] = []

        def _capturing_cls(**kwargs):
            captured_kwargs.append(dict(kwargs))
            mock_completion = MagicMock()
            mock_completion.response = "ok"
            inst = MagicMock()
            inst.completion.return_value = mock_completion
            return inst

        fn = make_sub_rlm_async_fn(parent, _rlm_cls=_capturing_cls)
        handle = fn("tarefa")
        handle.result(timeout_s=3.0)

        assert len(captured_kwargs) == 1
        env_kwargs = captured_kwargs[0].get("environment_kwargs") or {}
        assert "_parent_log_queue" in env_kwargs

    def test_two_calls_produce_independent_handles(self):
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock(depth=0, max_depth=3)
        mock_cls, _ = _make_mock_rlm_cls("ok")
        fn = make_sub_rlm_async_fn(parent, _rlm_cls=mock_cls)

        h1 = fn("t1")
        h2 = fn("t2")
        assert h1 is not h2
        assert h1._thread is not h2._thread

    # ------------------------------------------------------------------
    # Testes do P2P dinâmico
    # ------------------------------------------------------------------

    def test_creates_async_bus_on_parent(self):
        """make_sub_rlm_async_fn deve criar _async_bus no pai."""
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        from rlm.core.sibling_bus import SiblingBus
        # SimpleNamespace — hasattr() retorna False real (sem auto-create como MagicMock)
        parent = _make_parent_simple()
        assert not hasattr(parent, "_async_bus")
        make_sub_rlm_async_fn(parent)
        assert hasattr(parent, "_async_bus")
        assert isinstance(parent._async_bus, SiblingBus)

    def test_bus_reused_across_calls_same_parent(self):
        """Duas chamadas a make_sub_rlm_async_fn no mesmo pai devem reusar o bus."""
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock()
        make_sub_rlm_async_fn(parent)
        bus1 = parent._async_bus
        make_sub_rlm_async_fn(parent)
        bus2 = parent._async_bus
        assert bus1 is bus2

    def test_different_parents_get_different_buses(self):
        """Pais diferentes devem ter buses independentes."""
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        p1 = _make_parent_mock()
        p2 = _make_parent_mock()
        make_sub_rlm_async_fn(p1)
        make_sub_rlm_async_fn(p2)
        assert p1._async_bus is not p2._async_bus

    def test_handle_has_bus_reference(self):
        """AsyncHandle deve expor o mesmo bus do pai."""
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock(depth=0, max_depth=3)
        mock_cls, _ = _make_mock_rlm_cls("ok")
        fn = make_sub_rlm_async_fn(parent, _rlm_cls=mock_cls)
        handle = fn("t")
        assert handle.bus is parent._async_bus

    def test_handles_have_unique_branch_ids(self):
        """Cada handle deve ter um branch_id único e monotônico."""
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock(depth=0, max_depth=3)
        mock_cls, _ = _make_mock_rlm_cls("ok")
        fn = make_sub_rlm_async_fn(parent, _rlm_cls=mock_cls)

        h1 = fn("t1")
        h2 = fn("t2")
        h3 = fn("t3")
        ids = {h1.branch_id, h2.branch_id, h3.branch_id}
        assert len(ids) == 3  # todos distintos

    def test_sibling_bus_injected_into_child_env_kwargs(self):
        """_sibling_bus do pai deve ser passado nos env_kwargs de cada filho."""
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock(depth=0, max_depth=3)
        captured_kwargs: list[dict] = []

        def _capturing_cls(**kwargs):
            captured_kwargs.append(dict(kwargs))
            mock_completion = MagicMock()
            mock_completion.response = "ok"
            inst = MagicMock()
            inst.completion.return_value = mock_completion
            return inst

        fn = make_sub_rlm_async_fn(parent, _rlm_cls=_capturing_cls)
        handle = fn("tarefa")
        handle.result(timeout_s=3.0)

        env_kwargs = captured_kwargs[0].get("environment_kwargs") or {}
        assert "_sibling_bus" in env_kwargs
        assert env_kwargs["_sibling_bus"] is parent._async_bus

    def test_branch_id_injected_into_child_env_kwargs(self):
        """_sibling_branch_id do filho deve estar nos env_kwargs."""
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock(depth=0, max_depth=3)
        captured_kwargs: list[dict] = []

        def _capturing_cls(**kwargs):
            captured_kwargs.append(dict(kwargs))
            mock_completion = MagicMock()
            mock_completion.response = "ok"
            inst = MagicMock()
            inst.completion.return_value = mock_completion
            return inst

        fn = make_sub_rlm_async_fn(parent, _rlm_cls=_capturing_cls)
        handle = fn("tarefa")
        handle.result(timeout_s=3.0)

        env_kwargs = captured_kwargs[0].get("environment_kwargs") or {}
        assert "_sibling_branch_id" in env_kwargs
        assert env_kwargs["_sibling_branch_id"] == handle.branch_id

    def test_bus_communication_between_two_children(self):
        """Dois filhos com mock manual conseguem se comunicar via bus."""
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        from rlm.core.sibling_bus import SiblingBus
        # SimpleNamespace garante que parent._async_bus será um SiblingBus real
        parent = _make_parent_simple(depth=0, max_depth=3)
        make_sub_rlm_async_fn(parent)  # inicializa o bus no pai
        bus: SiblingBus = parent._async_bus

        # Simula dois filhos que se comunicam
        received: list = []

        def child_a():
            bus.publish("teste/dado", {"valor": 42})

        def child_b():
            msg = bus.subscribe("teste/dado", timeout_s=2.0)
            if msg:
                received.append(msg)

        t_a = threading.Thread(target=child_a)
        t_b = threading.Thread(target=child_b)
        t_b.start()
        time.sleep(0.05)
        t_a.start()
        t_a.join(timeout=2.0)
        t_b.join(timeout=2.0)

        assert received == [{"valor": 42}]

    def test_parent_can_publish_to_bus(self):
        """O pai deve poder publicar no bus e os filhos lerão via sibling_subscribe."""
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        # SimpleNamespace garante bus real (MagicMock tornaria peek() um mock)
        parent = _make_parent_simple(depth=0, max_depth=3)
        make_sub_rlm_async_fn(parent)
        bus = parent._async_bus

        # Pai publica um comando
        bus.publish("control/stop", True)

        # Filho leria via sibling_subscribe — aqui verificamos via peek
        msgs = bus.peek("control/stop")
        assert msgs == [True]

    def test_repr_shows_branch_id(self):
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock(depth=0, max_depth=3)
        mock_cls, _ = _make_mock_rlm_cls("ok")
        fn = make_sub_rlm_async_fn(parent, _rlm_cls=mock_cls)
        handle = fn("tarefa")
        handle.result(timeout_s=3.0)
        assert "branch=" in repr(handle)

    def test_cancel_event_injected_into_child_env_kwargs(self):
        """_cancel_event deve estar nos env_kwargs do filho."""
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock(depth=0, max_depth=3)
        captured_kwargs: list[dict] = []

        def _capturing_cls(**kwargs):
            captured_kwargs.append(dict(kwargs))
            mock_completion = MagicMock()
            mock_completion.response = "ok"
            inst = MagicMock()
            inst.completion.return_value = mock_completion
            return inst

        fn = make_sub_rlm_async_fn(parent, _rlm_cls=_capturing_cls)
        handle = fn("tarefa")
        handle.result(timeout_s=3.0)

        env_kwargs = captured_kwargs[0].get("environment_kwargs") or {}
        assert "_cancel_event" in env_kwargs
        assert isinstance(env_kwargs["_cancel_event"], threading.Event)

    def test_cancel_event_shared_between_handle_and_child(self):
        """O evento no handle deve ser o mesmo passado ao filho."""
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock(depth=0, max_depth=3)
        captured_kwargs: list[dict] = []

        def _capturing_cls(**kwargs):
            captured_kwargs.append(dict(kwargs))
            mock_completion = MagicMock()
            mock_completion.response = "ok"
            inst = MagicMock()
            inst.completion.return_value = mock_completion
            return inst

        fn = make_sub_rlm_async_fn(parent, _rlm_cls=_capturing_cls)
        handle = fn("tarefa")
        handle.result(timeout_s=3.0)

        child_event = (captured_kwargs[0].get("environment_kwargs") or {}).get("_cancel_event")
        assert child_event is handle._cancel_event  # mesmo objeto


# ===========================================================================
# 3. parent_log — injeção no LocalREPL
# ===========================================================================

class TestParentLogInjection:

    def test_parent_log_injected_when_queue_provided(self):
        """Se _parent_log_queue passado, parent_log deve aparecer em globals."""
        from rlm.environments.local_repl import LocalREPL

        log_q: queue.Queue = queue.Queue()
        repl = LocalREPL.__new__(LocalREPL)
        repl._parent_log_queue = log_q
        repl._sibling_bus = None
        repl.globals = {}
        repl._compaction_history = []
        repl._compaction = False

        # Chama só o bloco de injeção do parent_log — sem inicializar o REPL inteiro
        if getattr(repl, "_parent_log_queue", None) is not None:
            _pq = repl._parent_log_queue
            def parent_log(msg: str) -> None:
                try:
                    _pq.put_nowait(str(msg))
                except Exception:
                    pass
            repl.globals["parent_log"] = parent_log

        assert "parent_log" in repl.globals
        repl.globals["parent_log"]("teste de log")
        assert log_q.get_nowait() == "teste de log"

    def test_parent_log_not_injected_when_no_queue(self):
        """Sem _parent_log_queue, parent_log NÃO deve estar em globals."""
        from rlm.environments.local_repl import LocalREPL

        repl = LocalREPL.__new__(LocalREPL)
        repl._parent_log_queue = None
        repl._sibling_bus = None
        repl.globals = {}

        # Bloco equivalente ao do setup()
        if getattr(repl, "_parent_log_queue", None) is not None:
            repl.globals["parent_log"] = lambda msg: None

        assert "parent_log" not in repl.globals

    def test_parent_log_never_raises(self):
        """parent_log não deve jamais propagar exceção (nunca trava o filho)."""
        full_q: queue.Queue = queue.Queue(maxsize=1)
        full_q.put("já cheio")

        from rlm.environments.local_repl import LocalREPL
        repl = LocalREPL.__new__(LocalREPL)
        repl._parent_log_queue = full_q
        repl.globals = {}

        _pq = full_q
        def parent_log(msg: str) -> None:
            try:
                _pq.put_nowait(str(msg))
            except Exception:
                pass
        repl.globals["parent_log"] = parent_log

        # Não deve levantar - fila cheia é silenciada
        repl.globals["parent_log"]("msg que nao cabe")


# ===========================================================================
# 3b. check_cancel — injeção no LocalREPL
# ===========================================================================

class TestCheckCancel:

    def _make_repl_with_event(self, event: threading.Event | None):
        """Simula a injeção de check_cancel no setup() sem construir o REPL inteiro."""
        globals_: dict = {}
        if event is not None:
            _ce = event
            def check_cancel() -> bool:
                return _ce.is_set()
            globals_["check_cancel"] = check_cancel
        return globals_

    def test_check_cancel_injected_when_event_provided(self):
        """check_cancel deve aparecer nos globals quando _cancel_event é dado."""
        event = threading.Event()
        globals_ = self._make_repl_with_event(event)
        assert "check_cancel" in globals_

    def test_check_cancel_not_injected_when_no_event(self):
        """Sem _cancel_event, check_cancel NÃO deve estar nos globals."""
        globals_ = self._make_repl_with_event(None)
        assert "check_cancel" not in globals_

    def test_check_cancel_returns_false_before_cancel(self):
        """Antes de cancel(), check_cancel() deve retornar False."""
        event = threading.Event()
        globals_ = self._make_repl_with_event(event)
        assert globals_["check_cancel"]() is False

    def test_check_cancel_returns_true_after_set(self):
        """Depois de event.set(), check_cancel() deve retornar True."""
        event = threading.Event()
        globals_ = self._make_repl_with_event(event)
        event.set()
        assert globals_["check_cancel"]() is True

    def test_cancel_handle_propagates_to_check_cancel(self):
        """handle.cancel() deve fazer check_cancel() retornar True no filho."""
        from rlm.core.sub_rlm import make_sub_rlm_async_fn
        parent = _make_parent_mock(depth=0, max_depth=3)
        captured_kwargs: list[dict] = []

        def _capturing_cls(**kwargs):
            captured_kwargs.append(dict(kwargs))
            mock_completion = MagicMock()
            mock_completion.response = "ok"
            inst = MagicMock()
            inst.completion.return_value = mock_completion
            return inst

        fn = make_sub_rlm_async_fn(parent, _rlm_cls=_capturing_cls)
        handle = fn("tarefa")
        handle.result(timeout_s=3.0)

        # Simula o que check_cancel() faz no REPL do filho
        cancel_event = (captured_kwargs[0].get("environment_kwargs") or {}).get("_cancel_event")
        assert cancel_event is not None
        assert not cancel_event.is_set()

        handle.cancel()  # pai chama cancel()
        assert cancel_event.is_set()  # evento propagado — filho veria True


# ===========================================================================
# 4. RLMSession.chat() — contexto entre turnos
# ===========================================================================

class TestRLMSessionChat:

    def _make_session(self, responses: list[str]):
        """Cria RLMSession com RLM mockado que retorna responses em sequência."""
        import rlm.core.rlm_context_mixin as rlm_module
        from rlm.session import RLMSession
        from rlm.core.types import ModelUsageSummary, UsageSummary

        mock_lm = MagicMock()
        mock_lm.completion.side_effect = list(responses)
        mock_lm.get_usage_summary.return_value = UsageSummary(
            model_usage_summaries={
                "mock": ModelUsageSummary(
                    total_calls=1, total_input_tokens=50, total_output_tokens=20
                )
            }
        )
        mock_lm.get_last_usage.return_value = mock_lm.get_usage_summary.return_value

        with patch.object(rlm_module, "get_client", return_value=mock_lm):
            session = RLMSession(
                backend="openai",
                backend_kwargs={"model_name": "gpt-5-mini"},
                max_hot_turns=4,
            )
        # mantém o mock acessível para reprogramar side_effect
        session._mock_lm = mock_lm
        return session

    def test_chat_returns_response(self):
        from rlm.session import RLMSession
        import rlm.core.rlm_context_mixin as rlm_module
        from rlm.core.types import ModelUsageSummary, UsageSummary

        mock_lm = MagicMock()
        mock_lm.completion.return_value = "FINAL(resposta teste)"
        mock_lm.get_usage_summary.return_value = UsageSummary(
            model_usage_summaries={
                "mock": ModelUsageSummary(total_calls=1, total_input_tokens=10, total_output_tokens=5)
            }
        )
        mock_lm.get_last_usage.return_value = mock_lm.get_usage_summary.return_value

        with patch.object(rlm_module, "get_client", return_value=mock_lm):
            session = RLMSession(
                backend="openai",
                backend_kwargs={"model_name": "gpt-5-mini"},
            )
            resp = session.chat("olá")

        assert isinstance(resp, str)
        assert len(resp) > 0

    def test_turns_accumulate(self):
        from rlm.session import RLMSession
        import rlm.core.rlm_context_mixin as rlm_module
        from rlm.core.types import ModelUsageSummary, UsageSummary

        mock_lm = MagicMock()
        mock_lm.completion.return_value = "FINAL(ok)"
        mock_lm.get_usage_summary.return_value = UsageSummary(
            model_usage_summaries={
                "mock": ModelUsageSummary(total_calls=1, total_input_tokens=10, total_output_tokens=5)
            }
        )
        mock_lm.get_last_usage.return_value = mock_lm.get_usage_summary.return_value

        with patch.object(rlm_module, "get_client", return_value=mock_lm):
            session = RLMSession(
                backend="openai",
                backend_kwargs={"model_name": "gpt-5-mini"},
            )
            session.chat("mensagem 1")
            session.chat("mensagem 2")
            session.chat("mensagem 3")

        assert session._state.total_turns == 3
        assert len(session.turns) == 3

    def test_build_prompt_injects_previous_turns(self):
        """_build_prompt deve incluir turnos anteriores no texto gerado."""
        from rlm.session import RLMSession, SessionTurn, SessionState
        import rlm.core.rlm_context_mixin as rlm_module
        from rlm.core.types import ModelUsageSummary, UsageSummary

        mock_lm = MagicMock()
        mock_lm.completion.return_value = "FINAL(ok)"
        mock_lm.get_usage_summary.return_value = UsageSummary(
            model_usage_summaries={
                "mock": ModelUsageSummary(total_calls=1, total_input_tokens=10, total_output_tokens=5)
            }
        )
        mock_lm.get_last_usage.return_value = mock_lm.get_usage_summary.return_value

        with patch.object(rlm_module, "get_client", return_value=mock_lm):
            session = RLMSession(
                backend="openai",
                backend_kwargs={"model_name": "gpt-5-mini"},
            )

        # Injeta turno manualmente
        session._state.turns.append(
            SessionTurn(user="olá mundo", assistant="oi", elapsed_s=0.1)
        )

        prompt = session._build_prompt("próxima mensagem")
        assert "olá mundo" in prompt
        assert "próxima mensagem" in prompt

    def test_build_prompt_includes_compacted_summary(self):
        """Resumo compactado deve aparecer no prompt quando existir."""
        from rlm.session import RLMSession
        import rlm.core.rlm_context_mixin as rlm_module
        from rlm.core.types import ModelUsageSummary, UsageSummary

        mock_lm = MagicMock()
        mock_lm.completion.return_value = "FINAL(ok)"
        mock_lm.get_usage_summary.return_value = UsageSummary(
            model_usage_summaries={
                "mock": ModelUsageSummary(total_calls=1, total_input_tokens=10, total_output_tokens=5)
            }
        )
        mock_lm.get_last_usage.return_value = mock_lm.get_usage_summary.return_value

        with patch.object(rlm_module, "get_client", return_value=mock_lm):
            session = RLMSession(
                backend="openai",
                backend_kwargs={"model_name": "gpt-5-mini"},
            )

        session._state.compacted_summary = "Resumo: discutimos X e Y"
        session._state.compacted_turn_count = 5

        prompt = session._build_prompt("nova pergunta")
        assert "Resumo: discutimos X e Y" in prompt
        assert "HISTÓRICO ANTERIOR COMPACTADO" in prompt

    def test_reset_clears_state(self):
        from rlm.session import RLMSession, SessionTurn
        import rlm.core.rlm_context_mixin as rlm_module
        from rlm.core.types import ModelUsageSummary, UsageSummary

        mock_lm = MagicMock()
        mock_lm.completion.return_value = "FINAL(ok)"
        mock_lm.get_usage_summary.return_value = UsageSummary(
            model_usage_summaries={
                "mock": ModelUsageSummary(total_calls=1, total_input_tokens=10, total_output_tokens=5)
            }
        )
        mock_lm.get_last_usage.return_value = mock_lm.get_usage_summary.return_value

        with patch.object(rlm_module, "get_client", return_value=mock_lm):
            session = RLMSession(
                backend="openai",
                backend_kwargs={"model_name": "gpt-5-mini"},
            )

        session._state.turns.append(SessionTurn("a", "b"))
        session._state.compacted_summary = "resumo"
        session.reset()

        assert session._state.turns == []
        assert session._state.compacted_summary == ""
        assert session._state.total_turns == 0


# ===========================================================================
# 5. SessionAsyncHandle — turno assíncrono
# ===========================================================================

class TestSessionAsyncHandle:

    def _make_session_mock(self, response: str = "ok", delay_s: float = 0.0):
        """
        Session com RLM.completion mockado diretamente na instância.

        Como get_client é chamado DENTRO de cada completion(), e não no __init__,
        a única forma segura de mockar sem manter um patch ativo é substituir
        session._rlm.completion diretamente — esse mock persiste na thread async.
        """
        from rlm.session import RLMSession
        from rlm.core.types import RLMChatCompletion

        session = RLMSession(
            backend="openai",
            backend_kwargs={"model_name": "gpt-5-mini"},
        )

        # MagicMock simples com .response — não precisa ser RLMChatCompletion real
        fake_completion = MagicMock()
        fake_completion.response = response

        def _mock_completion(prompt):
            if delay_s:
                time.sleep(delay_s)
            return fake_completion

        # Substitui o método na instância — persiste além de qualquer patch context
        session._rlm.completion = _mock_completion
        return session

    def test_chat_async_returns_handle_immediately(self):
        from rlm.session import SessionAsyncHandle

        session = self._make_session_mock(response="FINAL(ok)", delay_s=0.5)
        t_start = time.perf_counter()
        handle = session.chat_async("mensagem")
        elapsed = time.perf_counter() - t_start

        assert isinstance(handle, SessionAsyncHandle)
        assert elapsed < 0.2  # retornou antes do delay do filho

    def test_handle_result_returns_response(self):
        session = self._make_session_mock(response="FINAL(resposta da sessão)", delay_s=0.0)
        handle = session.chat_async("pergunta")
        result = handle.result(timeout_s=5.0)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_is_done_false_while_running(self):
        session = self._make_session_mock(response="FINAL(ok)", delay_s=5.0)
        handle = session.chat_async("msg lenta")
        assert handle.is_done is False

    def test_is_done_true_after_result(self):
        session = self._make_session_mock(response="FINAL(ok)", delay_s=0.0)
        handle = session.chat_async("msg")
        handle.result(timeout_s=5.0)
        assert handle.is_done is True

    def test_result_timeout_raises(self):
        from rlm.session import SessionAsyncHandle

        session = self._make_session_mock(response="FINAL(ok)", delay_s=10.0)
        handle = session.chat_async("msg muito lenta")
        with pytest.raises(TimeoutError):
            handle.result(timeout_s=0.05)

    def test_elapsed_s_positive(self):
        session = self._make_session_mock(response="FINAL(ok)", delay_s=0.0)
        handle = session.chat_async("msg")
        assert handle.elapsed_s >= 0.0

    def test_log_poll_empty_when_no_messages(self):
        session = self._make_session_mock(response="FINAL(ok)", delay_s=0.0)
        handle = session.chat_async("msg")
        handle.result(timeout_s=5.0)
        # Sem parent_log no mock — deve retornar lista vazia
        assert handle.log_poll() == []

    def test_repr_contains_message_preview(self):
        session = self._make_session_mock(response="FINAL(ok)", delay_s=0.0)
        handle = session.chat_async("mensagem de teste")
        handle.result(timeout_s=5.0)
        r = repr(handle)
        assert "mensagem de teste" in r or "done" in r

    def test_env_kwargs_restored_after_async_turn(self):
        """environment_kwargs do RLM deve ser restaurado após o turno async."""
        session = self._make_session_mock(response="ok")

        original_kwargs = dict(session._rlm.environment_kwargs or {})
        handle = session.chat_async("msg")
        handle.result(timeout_s=5.0)

        # Após o turno, kwargs NÃO deve conter _parent_log_queue
        assert "_parent_log_queue" not in (session._rlm.environment_kwargs or {})

    def test_chat_records_recursive_messages_when_persistent_env_exists(self):
        from rlm.environments.local_repl import LocalREPL

        session = self._make_session_mock(response="FINAL(ok)")
        repl = LocalREPL()
        session._rlm._persistent_env = repl

        result = session.chat("mensagem persistida")
        messages = session.recent_recursive_messages(limit=10)
        events = session.recent_recursive_events(limit=10)

        assert result == "FINAL(ok)"
        assert [entry["role"] for entry in messages] == ["user", "assistant"]
        assert messages[0]["content"] == "mensagem persistida"
        assert [entry["event_type"] for entry in events[:2]] == [
            "user_message_received",
            "assistant_message_emitted",
        ]
        repl.cleanup()

    def test_queue_recursive_command_delegates_to_persistent_env(self):
        from rlm.environments.local_repl import LocalREPL

        session = self._make_session_mock(response="FINAL(ok)")
        repl = LocalREPL()
        session._rlm._persistent_env = repl

        queued = session.queue_recursive_command(
            "branch.resume",
            {"branch_id": 5},
        )
        state = session.recursive_session_state()

        assert queued is not None
        assert queued["command_type"] == "branch.resume"
        assert state["queued_commands"] == 1
        repl.cleanup()


# ===========================================================================
# 6. poll_logs — agrega múltiplos handles
# ===========================================================================

class TestPollLogs:

    def test_poll_logs_aggregates_from_multiple_handles(self):
        from rlm.session import RLMSession, SessionAsyncHandle
        import rlm.core.rlm_context_mixin as rlm_module
        from rlm.core.types import ModelUsageSummary, UsageSummary

        mock_lm = MagicMock()
        mock_lm.completion.return_value = "FINAL(ok)"
        mock_lm.get_usage_summary.return_value = UsageSummary(
            model_usage_summaries={
                "mock": ModelUsageSummary(total_calls=1, total_input_tokens=10, total_output_tokens=5)
            }
        )
        mock_lm.get_last_usage.return_value = mock_lm.get_usage_summary.return_value

        with patch.object(rlm_module, "get_client", return_value=mock_lm):
            session = RLMSession(
                backend="openai",
                backend_kwargs={"model_name": "gpt-5-mini"},
            )

        h1 = session.chat_async("msg 1")
        h2 = session.chat_async("msg 2")

        # Injeta mensagens manualmente nos log queues
        h1._log_queue.put("log de h1")
        h2._log_queue.put("log de h2")

        all_msgs = session.poll_logs([h1, h2])
        assert "log de h1" in all_msgs
        assert "log de h2" in all_msgs

    def test_poll_logs_empty_handles(self):
        import rlm.core.rlm_context_mixin as rlm_module
        from rlm.session import RLMSession
        from rlm.core.types import ModelUsageSummary, UsageSummary

        mock_lm = MagicMock()
        mock_lm.completion.return_value = "FINAL(ok)"
        mock_lm.get_usage_summary.return_value = UsageSummary(
            model_usage_summaries={
                "mock": ModelUsageSummary(total_calls=1, total_input_tokens=10, total_output_tokens=5)
            }
        )
        mock_lm.get_last_usage.return_value = mock_lm.get_usage_summary.return_value

        with patch.object(rlm_module, "get_client", return_value=mock_lm):
            session = RLMSession(
                backend="openai",
                backend_kwargs={"model_name": "gpt-5-mini"},
            )

        assert session.poll_logs([]) == []
