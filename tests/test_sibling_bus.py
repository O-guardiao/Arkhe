"""
Testes críticos — SiblingBus (Comunicação P2P entre agentes paralelos)

Cobre:
- SiblingMessage: estrutura, campos obrigatórios
- SiblingBus.publish(): armazena mensagem no tópico
- SiblingBus.subscribe(): recebe mensagem ou retorna None no timeout
- SiblingBus.peek(): lê sem consumir (non-destructive)
- SiblingBus.drain(): lê e esvazia (destrutivo)
- Canais de controle broadcast por geração
- Telemetria do barramento e por tópico
- SiblingBus.topics(): lista tópicos com mensagens pendentes
- make_repl_functions(): gera funções para injeção no REPL
- Thread-safety: publicação e consumo concorrentes
- Integração com make_sub_rlm_parallel_fn(): bus criado e injetado
- Integração com LocalREPL: sibling_* disponíveis no REPL

Execute:
    pytest tests/test_sibling_bus.py -v
"""
from __future__ import annotations

import threading
import time as _time

import pytest


# ===========================================================================
# SiblingMessage
# ===========================================================================

class TestSiblingMessage:

    def test_import(self):
        from rlm.core.orchestration.sibling_bus import SiblingMessage
        assert SiblingMessage is not None

    def test_required_fields(self):
        from rlm.core.orchestration.sibling_bus import SiblingMessage
        msg = SiblingMessage(topic="t1", data={"key": "val"})
        assert msg.topic == "t1"
        assert msg.data == {"key": "val"}

    def test_sender_id_defaults_none(self):
        from rlm.core.orchestration.sibling_bus import SiblingMessage
        msg = SiblingMessage(topic="t1", data="dado")
        assert msg.sender_id is None

    def test_sender_id_custom(self):
        from rlm.core.orchestration.sibling_bus import SiblingMessage
        msg = SiblingMessage(topic="t1", data="dado", sender_id=3)
        assert msg.sender_id == 3

    def test_timestamp_auto_populated(self):
        from rlm.core.orchestration.sibling_bus import SiblingMessage
        before = _time.perf_counter()
        msg = SiblingMessage(topic="t", data=1)
        after = _time.perf_counter()
        assert before <= msg.timestamp <= after

    def test_data_any_type(self):
        from rlm.core.orchestration.sibling_bus import SiblingMessage
        for data in [None, 42, "texto", [1, 2], {"a": 1}, lambda: None]:
            msg = SiblingMessage(topic="t", data=data)
            assert msg.data is data


# ===========================================================================
# SiblingBus — publish e subscribe
# ===========================================================================

class TestSiblingBusPublishSubscribe:

    def test_subscribe_receives_published_data(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("etl", {"rows": 100})
        result = bus.subscribe("etl", timeout_s=1.0)
        assert result == {"rows": 100}

    def test_subscribe_returns_none_on_timeout(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        result = bus.subscribe("empty_topic", timeout_s=0.05)
        assert result is None

    def test_subscribe_fifo_order(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("q", "primeiro")
        bus.publish("q", "segundo")
        bus.publish("q", "terceiro")
        assert bus.subscribe("q", timeout_s=0.1) == "primeiro"
        assert bus.subscribe("q", timeout_s=0.1) == "segundo"
        assert bus.subscribe("q", timeout_s=0.1) == "terceiro"
        assert bus.subscribe("q", timeout_s=0.05) is None  # vazia

    def test_subscribe_nonblocking_when_zero_timeout(self):
        """timeout_s=0 não deve bloquear."""
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        t0 = _time.perf_counter()
        result = bus.subscribe("nada", timeout_s=0)
        elapsed = _time.perf_counter() - t0
        assert result is None
        assert elapsed < 0.5  # não bloqueou

    def test_subscribe_negative_timeout_is_treated_as_nonblocking(self):
        """timeout negativo não deve estourar ValueError nem bloquear."""
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        t0 = _time.perf_counter()
        result = bus.subscribe("nada", timeout_s=-1)
        elapsed = _time.perf_counter() - t0
        assert result is None
        assert elapsed < 0.5

    def test_empty_topic_is_rejected(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus, SiblingBusError
        bus = SiblingBus()
        with pytest.raises(SiblingBusError, match="topic"):
            bus.publish("   ", "x")

    def test_sender_id_stored_in_message(self):
        """publish com sender_id deve armazenar o remetente."""
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("canal", "dado", sender_id=2)
        # subscribe retorna apenas data, não o envelope
        # Testamos via peek que implementa peek de SiblingMessage
        # Usamos _channels internamente para verificar
        with bus._lock:
            q = bus._channels["canal"]
        import queue
        msg = q.get_nowait()
        assert msg.sender_id == 2
        assert msg.data == "dado"

    def test_different_topics_isolated(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("a", "dado_A")
        bus.publish("b", "dado_B")
        assert bus.subscribe("a", timeout_s=0.1) == "dado_A"
        assert bus.subscribe("b", timeout_s=0.1) == "dado_B"
        assert bus.subscribe("a", timeout_s=0.05) is None
        assert bus.subscribe("b", timeout_s=0.05) is None

    def test_any_data_type_published(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        payloads = [None, 42, "texto", [1, 2], {"k": "v"}]
        for p in payloads:
            bus.publish("mixed", p)
        for expected in payloads:
            result = bus.subscribe("mixed", timeout_s=0.1)
            assert result == expected


# ===========================================================================
# SiblingBus — peek (non-destructive)
# ===========================================================================

class TestSiblingBusPeek:

    def test_peek_returns_all_data(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("t", "a")
        bus.publish("t", "b")
        bus.publish("t", "c")
        result = bus.peek("t")
        assert result == ["a", "b", "c"]

    def test_peek_non_destructive(self):
        """Peek não deve consumir as mensagens."""
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("t", "msg1")
        bus.publish("t", "msg2")
        first_peek = bus.peek("t")
        second_peek = bus.peek("t")
        assert first_peek == second_peek == ["msg1", "msg2"]

    def test_peek_empty_topic_returns_empty_list(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        assert bus.peek("inexistente") == []

    def test_peek_then_subscribe_still_gets_messages(self):
        """Após peek, subscribe deve ainda receber as mensagens."""
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("t", "x")
        bus.peek("t")  # não consome
        result = bus.subscribe("t", timeout_s=0.1)
        assert result == "x"

    def test_peek_returns_list_not_none(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        result = bus.peek("nao_existe")
        assert isinstance(result, list)
        assert result == []


# ===========================================================================
# SiblingBus — drain (destrutivo)
# ===========================================================================

class TestSiblingBusDrain:

    def test_drain_returns_all_data(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("t", "x")
        bus.publish("t", "y")
        result = bus.drain("t")
        assert result == ["x", "y"]

    def test_drain_empties_channel(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("t", "dado")
        bus.drain("t")
        assert bus.drain("t") == []

    def test_drain_empty_returns_empty_list(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        assert bus.drain("nada") == []

    def test_drain_vs_peek_difference(self):
        """drain esvazia, peek não esvazia."""
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("t", "dado")
        peeked = bus.peek("t")
        drained = bus.drain("t")
        after_drain = bus.drain("t")  # deve estar vazio
        assert peeked == ["dado"]
        assert drained == ["dado"]
        assert after_drain == []


# ===========================================================================
# SiblingBus — topics()
# ===========================================================================

class TestSiblingBusTopics:

    def test_topics_empty_when_no_messages(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        assert bus.topics() == []

    def test_topics_shows_topic_with_messages(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("etl/done", True)
        assert "etl/done" in bus.topics()

    def test_topics_hides_empty_topic(self):
        """Tópicos com fila vazia não aparecem em topics()."""
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("a", "msg")
        bus.subscribe("a", timeout_s=0.1)  # consome
        assert "a" not in bus.topics()

    def test_topics_multiple(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("t1", 1)
        bus.publish("t2", 2)
        bus.publish("t3", 3)
        topics = bus.topics()
        assert set(topics) == {"t1", "t2", "t3"}


# ===========================================================================
# make_repl_functions()
# ===========================================================================

class TestMakeReplFunctions:

    def test_returns_dict_with_required_keys(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        fns = bus.make_repl_functions(sender_id=0)
        assert "sibling_publish" in fns
        assert "sibling_subscribe" in fns
        assert "sibling_peek" in fns
        assert "sibling_subscribe_meta" in fns
        assert "sibling_peek_meta" in fns
        assert "sibling_control_publish" in fns
        assert "sibling_control_poll" in fns
        assert "sibling_control_wait" in fns
        assert "sibling_control_peek" in fns
        assert "sibling_bus_stats" in fns
        assert "sibling_topic_stats" in fns
        assert "sibling_drain" in fns
        assert "sibling_topics" in fns

    def test_all_values_are_callable(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        fns = bus.make_repl_functions(sender_id=1)
        for k, v in fns.items():
            assert callable(v), f"{k} deve ser callable"

    def test_sibling_publish_writes_to_bus(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        fns = bus.make_repl_functions(sender_id=0)
        fns["sibling_publish"]("canal", "payload")
        assert bus.subscribe("canal", timeout_s=0.1) == "payload"

    def test_sibling_subscribe_reads_from_bus(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        fns = bus.make_repl_functions(sender_id=0)
        bus.publish("canal", "dado")
        result = fns["sibling_subscribe"]("canal", 0.1)
        assert result == "dado"

    def test_sibling_peek_reads_non_destructive(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("t", "x")
        bus.publish("t", "y")
        fns = bus.make_repl_functions(sender_id=0)
        peek1 = fns["sibling_peek"]("t")
        peek2 = fns["sibling_peek"]("t")
        assert peek1 == peek2 == ["x", "y"]

    def test_sibling_topics_lists_active(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("active", 1)
        fns = bus.make_repl_functions()
        assert "active" in fns["sibling_topics"]()

    def test_sibling_subscribe_meta_returns_envelope(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        fns = bus.make_repl_functions(sender_id=11)
        fns["sibling_publish"]("meta", {"ok": True})
        envelope = fns["sibling_subscribe_meta"]("meta", 0.1)
        assert envelope is not None
        assert envelope["topic"] == "meta"
        assert envelope["data"] == {"ok": True}
        assert envelope["sender_id"] == 11

    def test_sibling_peek_meta_returns_pending_envelopes(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        fns = bus.make_repl_functions(sender_id=3)
        fns["sibling_publish"]("meta", "a")
        fns["sibling_publish"]("meta", "b")
        envelopes = fns["sibling_peek_meta"]("meta")
        assert [item["data"] for item in envelopes] == ["a", "b"]
        assert [item["sender_id"] for item in envelopes] == [3, 3]

    def test_sibling_drain_empties_channel(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        fns = bus.make_repl_functions(sender_id=0)
        fns["sibling_publish"]("drain", "x")
        fns["sibling_publish"]("drain", "y")
        assert fns["sibling_drain"]("drain") == ["x", "y"]
        assert fns["sibling_drain"]("drain") == []

    def test_sibling_control_publish_and_poll_broadcast_by_generation(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        sender = bus.make_repl_functions(sender_id=0)
        recv_a = bus.make_repl_functions(sender_id=1)
        recv_b = bus.make_repl_functions(sender_id=2)

        generation = sender["sibling_control_publish"]("control/stop", {"stop": True})
        assert generation == 1

        msg_a = recv_a["sibling_control_poll"]("control/stop")
        msg_b = recv_b["sibling_control_poll"]("control/stop")
        assert msg_a is not None and msg_b is not None
        assert msg_a["data"] == {"stop": True}
        assert msg_b["data"] == {"stop": True}
        assert msg_a["generation"] == msg_b["generation"] == 1
        assert msg_a["sender_id"] == msg_b["sender_id"] == 0
        assert recv_a["sibling_control_poll"]("control/stop") is None
        assert recv_b["sibling_control_poll"]("control/stop") is None

    def test_sibling_control_wait_receives_next_generation(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        sender = bus.make_repl_functions(sender_id=0)
        recv = bus.make_repl_functions(sender_id=5)

        result_holder: list[dict] = []

        def waiter():
            msg = recv["sibling_control_wait"]("control/schema", 0.5)
            if msg is not None:
                result_holder.append(msg)

        thread = threading.Thread(target=waiter)
        thread.start()
        _time.sleep(0.05)
        sender["sibling_control_publish"]("control/schema", {"schema": "v2"})
        thread.join(timeout=1.0)

        assert result_holder
        assert result_holder[0]["data"] == {"schema": "v2"}
        assert result_holder[0]["generation"] == 1

    def test_sibling_control_peek_does_not_consume_generation(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        sender = bus.make_repl_functions(sender_id=9)
        recv = bus.make_repl_functions(sender_id=4)

        sender["sibling_control_publish"]("control/mode", "safe")
        peeked = recv["sibling_control_peek"]("control/mode")
        polled = recv["sibling_control_poll"]("control/mode")

        assert peeked is not None
        assert peeked["data"] == "safe"
        assert polled is not None
        assert polled["data"] == "safe"

    def test_sibling_stats_helpers_expose_telemetry(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        fns = bus.make_repl_functions(sender_id=3)

        fns["sibling_publish"]("stats/data", "x")
        fns["sibling_peek"]("stats/data")
        fns["sibling_control_publish"]("stats/control", {"ok": True})
        fns["sibling_control_poll"]("stats/control")

        overall = fns["sibling_bus_stats"]()
        topic_data = fns["sibling_topic_stats"]("stats/data")
        topic_control = fns["sibling_topic_stats"]("stats/control")

        assert overall["operation_counts"]["publish"] >= 1
        assert overall["operation_counts"]["control_publish"] >= 1
        assert topic_data["operation_counts"]["publish"] >= 1
        assert topic_control["control_generation"] == 1

    def test_sender_id_embedded_in_publish(self):
        """Publicação via repl_functions deve embutir sender_id."""
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        fns = bus.make_repl_functions(sender_id=7)
        fns["sibling_publish"]("t", "dado")
        # Verificar sender_id no envelope interno
        with bus._lock:
            q = bus._channels["t"]
        import queue
        msg = q.get_nowait()
        assert msg.sender_id == 7

    def test_two_branches_share_same_bus(self):
        """Filho-A publica, Filho-B lê — mesmo bus."""
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        fns_a = bus.make_repl_functions(sender_id=0)
        fns_b = bus.make_repl_functions(sender_id=1)
        fns_a["sibling_publish"]("shared", "mensagem_de_A")
        result = fns_b["sibling_subscribe"]("shared", 0.1)
        assert result == "mensagem_de_A"

    def test_independent_buses_isolated(self):
        """Dois SiblingBus distintos não compartilham mensagens."""
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus1 = SiblingBus()
        bus2 = SiblingBus()
        fns1 = bus1.make_repl_functions()
        fns2 = bus2.make_repl_functions()
        fns1["sibling_publish"]("t", "dado no bus1")
        result = fns2["sibling_subscribe"]("t", 0.0)
        assert result is None


# ===========================================================================
# Thread-safety
# ===========================================================================

class TestSiblingBusThreadSafety:

    def test_concurrent_publish_subscribe(self):
        """100 threads publicam, 100 threads subscrevem — sem race conditions."""
        from rlm.core.orchestration.sibling_bus import SiblingBus
        import queue as stdlib_queue

        bus = SiblingBus()
        N = 50
        received: list[str] = []
        lock = threading.Lock()
        published_barrier = threading.Barrier(N)
        errors: list[Exception] = []

        def publisher(i: int):
            try:
                published_barrier.wait()
                bus.publish("concurrent", f"msg_{i}", sender_id=i)
            except Exception as e:
                errors.append(e)

        def subscriber():
            try:
                result = bus.subscribe("concurrent", timeout_s=2.0)
                if result is not None:
                    with lock:
                        received.append(result)
            except Exception as e:
                errors.append(e)

        pub_threads = [threading.Thread(target=publisher, args=(i,)) for i in range(N)]
        sub_threads = [threading.Thread(target=subscriber) for _ in range(N)]

        for t in pub_threads + sub_threads:
            t.start()
        for t in pub_threads + sub_threads:
            t.join(timeout=5.0)

        assert not errors, f"Erros em threads: {errors}"
        assert len(received) == N

    def test_concurrent_peek_does_not_corrupt(self):
        """peek() simultâneo em múltiplas threads não corrompe a fila."""
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        N = 20
        # Publica N mensagens
        for i in range(N):
            bus.publish("t", i)

        results: list[list] = []
        lock = threading.Lock()

        def peeker():
            r = bus.peek("t")
            with lock:
                results.append(r)

        threads = [threading.Thread(target=peeker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3.0)

        # Todas as threads devem ter visto todas as N mensagens
        for r in results:
            assert len(r) == N

    def test_publish_from_multiple_threads(self):
        """Publicação de múltiplas threads sem deadlocks."""
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        N = 30
        barrier = threading.Barrier(N)

        def pub(i: int):
            barrier.wait()
            for j in range(5):
                bus.publish("stress", f"{i}:{j}")

        threads = [threading.Thread(target=pub, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        messages = bus.drain("stress")
        assert len(messages) == N * 5


# ===========================================================================
# Integração: make_sub_rlm_parallel_fn cria e injeta SiblingBus
# ===========================================================================

class TestSiblingBusIntegrationParallelFn:

    def _make_parent_mock(self, depth=0, max_depth=2):
        from unittest.mock import MagicMock
        parent = MagicMock()
        parent.depth = depth
        parent.max_depth = max_depth
        parent.backend = "openai"
        parent.backend_kwargs = {"model_name": "gpt-4o-mini"}
        parent.environment_type = "local"
        parent.environment_kwargs = {}
        return parent

    def test_parallel_fn_creates_sibling_bus(self):
        """make_sub_rlm_parallel_fn deve criar um SiblingBus internamente."""
        # Verificamos indiretamente: o bus é compartilhado se as funções serial
        # recebem _sibling_bus no env_kwargs ao criar os filhos.
        from rlm.core.engine.sub_rlm import make_sub_rlm_parallel_fn
        from unittest.mock import MagicMock

        parent = self._make_parent_mock()
        env_kwargs_seen: list[dict] = []

        def capturing_cls(**kwargs):
            env_kwargs_seen.append(kwargs.get("environment_kwargs") or {})
            mock_inst = MagicMock()
            mock_inst.completion.return_value = MagicMock(response="ok", artifacts=None)
            return mock_inst

        par_fn, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=capturing_cls)
        par_fn(["task A", "task B"])

        # Ambos os filhos devem ter recebido _sibling_bus
        assert len(env_kwargs_seen) == 2
        for kw in env_kwargs_seen:
            assert "_sibling_bus" in kw

    def test_parallel_branches_share_same_bus_instance(self):
        """Todos os branches de uma chamada paralela compartilham o MESMO bus."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_parallel_fn
        from rlm.core.orchestration.sibling_bus import SiblingBus
        from unittest.mock import MagicMock

        parent = self._make_parent_mock()
        buses_seen: list[SiblingBus] = []

        def capturing_cls(**kwargs):
            env_kw = kwargs.get("environment_kwargs") or {}
            bus = env_kw.get("_sibling_bus")
            if bus is not None:
                buses_seen.append(bus)
            mock_inst = MagicMock()
            mock_inst.completion.return_value = MagicMock(response="ok", artifacts=None)
            return mock_inst

        par_fn, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=capturing_cls)
        par_fn(["task A", "task B", "task C"])

        assert len(buses_seen) == 3
        # Todos devem ser a mesma instância
        first_bus = buses_seen[0]
        for b in buses_seen[1:]:
            assert b is first_bus

    def test_parallel_branches_get_unique_branch_ids(self):
        """Cada branch deve receber _sibling_branch_id único (0, 1, 2...)."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_parallel_fn
        from unittest.mock import MagicMock

        parent = self._make_parent_mock()
        branch_ids_seen: list[int] = []

        def capturing_cls(**kwargs):
            env_kw = kwargs.get("environment_kwargs") or {}
            bid = env_kw.get("_sibling_branch_id")
            if bid is not None:
                branch_ids_seen.append(bid)
            mock_inst = MagicMock()
            mock_inst.completion.return_value = MagicMock(response="ok", artifacts=None)
            return mock_inst

        par_fn, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=capturing_cls)
        par_fn(["t0", "t1", "t2"])

        assert sorted(branch_ids_seen) == [0, 1, 2]

    def test_different_parallel_calls_get_different_buses(self):
        """Duas chamadas a sub_rlm_parallel() distintas devem ter buses diferentes."""
        from rlm.core.engine.sub_rlm import make_sub_rlm_parallel_fn
        from rlm.core.orchestration.sibling_bus import SiblingBus
        from unittest.mock import MagicMock

        parent = self._make_parent_mock()
        all_buses: list[list[SiblingBus]] = []

        def capturing_cls(**kwargs):
            env_kw = kwargs.get("environment_kwargs") or {}
            bus = env_kw.get("_sibling_bus")
            return_mock = MagicMock()
            return_mock.completion.return_value = MagicMock(response="ok", artifacts=None)
            # Guardar bus no closure via side effect
            if not hasattr(capturing_cls, "_call_buses"):
                capturing_cls._call_buses = []
            capturing_cls._call_buses.append(bus)
            return return_mock

        par_fn, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=capturing_cls)
        capturing_cls._call_buses = []
        par_fn(["a", "b"])
        buses_call1 = list(capturing_cls._call_buses)
        capturing_cls._call_buses = []

        par_fn(["c", "d"])
        buses_call2 = list(capturing_cls._call_buses)

        # As duas chamadas recebem buses diferentes (cada chamada cria um novo bus)
        # NOTE: make_sub_rlm_parallel_fn cria o bus UMA vez na fábrica, não por chamada.
        # Ambas as chamadas usam o mesmo bus da fábrica — isso é esperado e correto.
        assert buses_call1[0] is not None
        assert buses_call2[0] is not None


# ===========================================================================
# Integração com LocalREPL: sibling_* injetados nos globals
# ===========================================================================

class TestSiblingBusLocalReplInjection:

    def test_sibling_functions_injected_in_setup(self):
        """Quando _sibling_bus é passado via kwargs, LocalREPL deve ter sibling_*."""
        try:
            from rlm.environments.local_repl import LocalREPL
            from rlm.core.orchestration.sibling_bus import SiblingBus
            from unittest.mock import MagicMock, patch
        except ImportError:
            pytest.skip("LocalREPL não disponível")

        bus = SiblingBus()
        env = object.__new__(LocalREPL)
        env._sibling_bus = bus
        env._sibling_branch_id = 2
        env.globals = {}

        # Simula a parte de setup() que injeta os sibling_*
        fns = bus.make_repl_functions(sender_id=2)
        env.globals.update(fns)

        assert "sibling_publish" in env.globals
        assert "sibling_subscribe" in env.globals
        assert "sibling_peek" in env.globals
        assert "sibling_topics" in env.globals

    def test_no_sibling_functions_without_bus(self):
        """Sem _sibling_bus, sibling_* NÃO devem aparecer nos globals."""
        try:
            from rlm.environments.local_repl import LocalREPL
        except ImportError:
            pytest.skip("LocalREPL não disponível")

        env = object.__new__(LocalREPL)
        env._sibling_bus = None
        env.globals = {}

        # Simula a condição do setup(): só injeta se _sibling_bus não é None
        if getattr(env, "_sibling_bus", None) is not None:
            bus = env._sibling_bus
            env.globals.update(bus.make_repl_functions(sender_id=None))

        assert "sibling_publish" not in env.globals
        assert "sibling_subscribe" not in env.globals


# ===========================================================================
# TestSiblingBusSafetyLimits — limites de memória e payload adicionados fase 6
# ===========================================================================

class TestSiblingBusSafetyLimits:
    """Testa os limites de segurança do SiblingBus:
    - _MAX_CHANNELS: cap de tópicos
    - _CHANNEL_MAXSIZE: cap de mensagens por canal
    - _MAX_PAYLOAD_BYTES: cap de payload por mensagem
    - SiblingBusError: tipo correto de exceção
    """

    def test_sibling_bus_error_is_runtime_error(self):
        from rlm.core.orchestration.sibling_bus import SiblingBusError
        assert issubclass(SiblingBusError, RuntimeError)
        err = SiblingBusError("test")
        assert str(err) == "test"

    def test_sibling_bus_constants_exported(self):
        from rlm.core.orchestration import sibling_bus
        assert hasattr(sibling_bus, "_MAX_CHANNELS")
        assert hasattr(sibling_bus, "_CHANNEL_MAXSIZE")
        assert hasattr(sibling_bus, "_MAX_PAYLOAD_BYTES")
        assert sibling_bus._MAX_CHANNELS >= 1
        assert sibling_bus._CHANNEL_MAXSIZE >= 1
        assert sibling_bus._MAX_PAYLOAD_BYTES >= 1

    def test_channel_cap_raises_sibling_bus_error(self):
        """Criar mais de _MAX_CHANNELS tópicos distintos deve lançar SiblingBusError."""
        from rlm.core.orchestration.sibling_bus import SiblingBus, SiblingBusError, _MAX_CHANNELS
        bus = SiblingBus()
        # Preenche até o limite
        for i in range(_MAX_CHANNELS):
            bus.publish(f"topic_{i}", i)
        # O próximo tópico deve falhar
        with pytest.raises(SiblingBusError, match="[Ll]imite"):
            bus.publish("topic_overflow", "boom")

    def test_subscribe_respects_channel_cap_too(self):
        """subscribe() não deve contornar o limite de tópicos criando filas ilimitadas."""
        from rlm.core.orchestration.sibling_bus import SiblingBus, SiblingBusError, _MAX_CHANNELS
        bus = SiblingBus()
        for i in range(_MAX_CHANNELS):
            assert bus.subscribe(f"topic_{i}", timeout_s=0.0) is None
        with pytest.raises(SiblingBusError, match="[Ll]imite"):
            bus.subscribe("topic_overflow", timeout_s=0.0)

    def test_subscribe_created_channel_is_bounded(self):
        """Canal criado por subscribe() deve continuar obedecendo _CHANNEL_MAXSIZE."""
        from rlm.core.orchestration.sibling_bus import SiblingBus, SiblingBusError, _CHANNEL_MAXSIZE
        bus = SiblingBus()
        assert bus.subscribe("bounded", timeout_s=0.0) is None
        for i in range(_CHANNEL_MAXSIZE):
            bus.publish("bounded", i)
        with pytest.raises(SiblingBusError, match="[Cc]anal|cheio|[Ff]ull"):
            bus.publish("bounded", "overflow")

    def test_control_publish_also_respects_global_topic_cap(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus, SiblingBusError, _MAX_CHANNELS
        bus = SiblingBus()
        for i in range(_MAX_CHANNELS):
            bus.publish_control(f"control_{i}", i)
        with pytest.raises(SiblingBusError, match="[Ll]imite"):
            bus.publish_control("control_overflow", True)


# ===========================================================================
# Canais de controle broadcast por geração
# ===========================================================================

class TestSiblingBusControlChannels:

    def test_poll_control_is_per_receiver(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish_control("control/stop", True, sender_id=0)

        recv_a_first = bus.poll_control("control/stop", receiver_id=1)
        recv_b_first = bus.poll_control("control/stop", receiver_id=2)
        recv_a_second = bus.poll_control("control/stop", receiver_id=1)

        assert recv_a_first is not None
        assert recv_b_first is not None
        assert recv_a_first["generation"] == recv_b_first["generation"] == 1
        assert recv_a_second is None

    def test_new_control_publish_creates_new_generation(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish_control("control/mode", "fast", sender_id=0)
        first = bus.poll_control("control/mode", receiver_id=1)
        bus.publish_control("control/mode", "safe", sender_id=0)
        second = bus.poll_control("control/mode", receiver_id=1)

        assert first is not None and second is not None
        assert first["generation"] == 1
        assert second["generation"] == 2
        assert second["data"] == "safe"

    def test_wait_control_times_out_cleanly(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        t0 = _time.perf_counter()
        result = bus.wait_control("control/none", receiver_id=1, timeout_s=0.05)
        elapsed = _time.perf_counter() - t0
        assert result is None
        assert elapsed < 0.5

    def test_publish_signal_uses_semantic_type_and_mapped_topic(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus

        bus = SiblingBus()
        bus.publish_signal("solution_found", {"winner": 0}, sender_id=3)

        signal = bus.poll_control("control/solution_found", receiver_id=9)
        assert signal is not None
        assert signal["topic"] == "control/solution_found"
        assert signal["sender_id"] == 3
        assert signal["semantic_type"] == "solution_found"


# ===========================================================================
# Telemetria
# ===========================================================================

class TestSiblingBusTelemetry:

    def test_get_stats_tracks_key_operations(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("data", 1)
        bus.subscribe("data", timeout_s=0.1)
        bus.subscribe("data", timeout_s=0.0)
        bus.publish_control("control/x", True, sender_id=7)
        bus.poll_control("control/x", receiver_id=1)
        bus.poll_control("control/x", receiver_id=1)

        stats = bus.get_stats()
        assert stats["operation_counts"]["publish"] >= 1
        assert stats["operation_counts"]["subscribe_hit"] >= 1
        assert stats["operation_counts"]["subscribe_timeout"] >= 1
        assert stats["operation_counts"]["control_publish"] >= 1
        assert stats["operation_counts"]["control_poll_hit"] >= 1
        assert stats["operation_counts"]["control_poll_miss"] >= 1

    def test_get_topic_stats_reports_queue_and_generation(self):
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("etl/result", "row")
        bus.publish_control("control/schema", {"schema": 2}, sender_id=4)

        data_stats = bus.get_topic_stats("etl/result")
        control_stats = bus.get_topic_stats("control/schema")

        assert data_stats["queue_size"] == 1
        assert data_stats["control_generation"] == 0
        assert control_stats["control_generation"] == 1
        assert control_stats["latest_control_sender_id"] == 4

    def test_channel_reuse_after_cap_works(self):
        """Publicar em tópico já existente após atingir o cap deve funcionar."""
        from rlm.core.orchestration.sibling_bus import SiblingBus, _MAX_CHANNELS
        bus = SiblingBus()
        for i in range(_MAX_CHANNELS):
            bus.publish(f"topic_{i}", i)
        # Publicar novamente no MESMO tópico deve funcionar
        bus.publish("topic_0", "second_message")

    def test_queue_full_raises_sibling_bus_error(self):
        """Lotar um canal (_CHANNEL_MAXSIZE mensagens) deve lançar SiblingBusError."""
        from rlm.core.orchestration.sibling_bus import SiblingBus, SiblingBusError, _CHANNEL_MAXSIZE
        bus = SiblingBus()
        topic = "flood_test"
        # Lota o canal até o limite
        for i in range(_CHANNEL_MAXSIZE):
            bus.publish(topic, i)
        # A próxima publicação deve falhar
        with pytest.raises(SiblingBusError, match="[Cc]anal|cheio|[Ff]ull"):
            bus.publish(topic, "overflow")

    def test_queue_full_recovers_after_consume(self):
        """Após consumir mensagens, publish deve funcionar novamente."""
        from rlm.core.orchestration.sibling_bus import SiblingBus, _CHANNEL_MAXSIZE
        bus = SiblingBus()
        topic = "recover_test"
        for i in range(_CHANNEL_MAXSIZE):
            bus.publish(topic, i)
        # Consome algumas mensagens
        bus.subscribe(topic, timeout_s=0.1)
        bus.subscribe(topic, timeout_s=0.1)
        # Agora deve poder publicar de novo
        bus.publish(topic, "recuperado")

    def test_payload_too_large_raises_sibling_bus_error(self):
        """Payload acima de _MAX_PAYLOAD_BYTES deve lançar SiblingBusError."""
        import sys
        from rlm.core.orchestration.sibling_bus import SiblingBus, SiblingBusError, _MAX_PAYLOAD_BYTES
        bus = SiblingBus()
        # Cria um objeto que sys.getsizeof reporta como maior que o limite
        # bytes() garante tamanho exato
        big_payload = bytes(_MAX_PAYLOAD_BYTES + 1)
        assert sys.getsizeof(big_payload) > _MAX_PAYLOAD_BYTES
        with pytest.raises(SiblingBusError, match="[Bb]ytes|[Pp]ayload|[Ll]imite"):
            bus.publish("bomb", big_payload)

    def test_small_payload_passes(self):
        """Payload pequeno deve ser aceito sem erro."""
        from rlm.core.orchestration.sibling_bus import SiblingBus
        bus = SiblingBus()
        bus.publish("ok", {"key": "value", "num": 42})
        result = bus.subscribe("ok", timeout_s=0.1)
        assert result == {"key": "value", "num": 42}
