"""
Testes — Módulos de infraestrutura de gateway (T1, T2, T3).

Cobre:
- backoff.py     (BackoffPolicy, compute_backoff, sleep_sync, sleep_async)
- dedup.py       (MessageDedup)
- heartbeat.py   (SyncHeartbeat, AsyncHeartbeat)
- gateway_state.py (GatewayStateMachine)
- message_envelope.py (InboundMessage, normalizers)
- health_monitor.py (HealthMonitor)
- drain.py       (DrainGuard)
- backpressure.py (SyncGate, AsyncGate, ConcurrencyExceeded)
- chunker.py     (smart_chunk)

Execute:
    pytest tests/test_gateway_infra.py -v
"""
from __future__ import annotations

import asyncio
import threading
import time
import pytest


# ===========================================================================
# backoff.py
# ===========================================================================

class TestBackoffPolicy:

    def test_compute_backoff_attempt_zero_returns_zero(self):
        from rlm.server.backoff import BackoffPolicy, compute_backoff
        p = BackoffPolicy(initial_s=5.0, max_s=300.0, factor=2.0)
        assert compute_backoff(p, 0) == 0.0

    def test_compute_backoff_attempt_one_near_initial(self):
        from rlm.server.backoff import BackoffPolicy, compute_backoff
        p = BackoffPolicy(initial_s=5.0, max_s=300.0, factor=2.0, jitter_fraction=0.0)
        assert compute_backoff(p, 1) == 5.0

    def test_compute_backoff_grows_exponentially(self):
        from rlm.server.backoff import BackoffPolicy, compute_backoff
        p = BackoffPolicy(initial_s=1.0, max_s=1000.0, factor=2.0, jitter_fraction=0.0)
        assert compute_backoff(p, 1) == 1.0
        assert compute_backoff(p, 2) == 2.0
        assert compute_backoff(p, 3) == 4.0
        assert compute_backoff(p, 4) == 8.0

    def test_compute_backoff_capped_at_max(self):
        from rlm.server.backoff import BackoffPolicy, compute_backoff
        p = BackoffPolicy(initial_s=100.0, max_s=200.0, factor=3.0, jitter_fraction=0.0)
        assert compute_backoff(p, 5) == 200.0

    def test_compute_backoff_jitter_is_positive(self):
        from rlm.server.backoff import BackoffPolicy, compute_backoff
        p = BackoffPolicy(initial_s=10.0, max_s=1000.0, factor=2.0, jitter_fraction=0.5)
        for _ in range(50):
            val = compute_backoff(p, 1)
            assert val >= 10.0  # jitter é sempre positivo

    def test_predefined_policies_exist(self):
        from rlm.server.backoff import GATEWAY_RECONNECT, HTTP_RETRY, HEALTH_CHECK
        assert GATEWAY_RECONNECT.initial_s == 5.0
        assert HTTP_RETRY.max_attempts == 5
        assert HEALTH_CHECK.factor == 1.5


class TestSleepSync:

    def test_sleep_sync_completes_on_zero(self):
        from rlm.server.backoff import sleep_sync
        assert sleep_sync(0.0) is True

    def test_sleep_sync_completes_short_duration(self):
        from rlm.server.backoff import sleep_sync
        t0 = time.monotonic()
        result = sleep_sync(0.05)
        elapsed = time.monotonic() - t0
        assert result is True
        assert elapsed >= 0.04

    def test_sleep_sync_cancelled_by_token(self):
        from rlm.server.backoff import sleep_sync
        from rlm.core.cancellation import CancellationTokenSource

        cts = CancellationTokenSource()
        # Cancel after 50ms from another thread
        threading.Timer(0.05, cts.cancel).start()

        t0 = time.monotonic()
        result = sleep_sync(5.0, cancel_token=cts.token)
        elapsed = time.monotonic() - t0

        assert result is False  # cancelado
        assert elapsed < 1.0    # não esperou 5s
        cts.dispose()

    def test_sleep_sync_already_cancelled(self):
        from rlm.server.backoff import sleep_sync
        from rlm.core.cancellation import CancellationToken
        assert sleep_sync(5.0, cancel_token=CancellationToken.CANCELLED) is False


class TestSleepAsync:

    def test_sleep_async_completes(self):
        from rlm.server.backoff import sleep_async

        async def _run():
            return await sleep_async(0.05)

        assert asyncio.run(_run()) is True

    def test_sleep_async_cancelled(self):
        from rlm.server.backoff import sleep_async
        from rlm.core.cancellation import CancellationTokenSource

        async def _run():
            cts = CancellationTokenSource()
            loop = asyncio.get_running_loop()
            loop.call_later(0.05, cts.cancel)
            result = await sleep_async(5.0, cancel_token=cts.token)
            cts.dispose()
            return result

        assert asyncio.run(_run()) is False


# ===========================================================================
# dedup.py
# ===========================================================================

class TestMessageDedup:

    def test_first_message_not_duplicate(self):
        from rlm.server.dedup import MessageDedup
        d = MessageDedup(ttl_s=10.0)
        assert d.is_duplicate("msg1") is False

    def test_same_message_is_duplicate(self):
        from rlm.server.dedup import MessageDedup
        d = MessageDedup(ttl_s=10.0)
        assert d.is_duplicate("msg1") is False
        assert d.is_duplicate("msg1") is True

    def test_different_messages_not_duplicate(self):
        from rlm.server.dedup import MessageDedup
        d = MessageDedup(ttl_s=10.0)
        assert d.is_duplicate("msg1") is False
        assert d.is_duplicate("msg2") is False

    def test_empty_id_never_duplicate(self):
        from rlm.server.dedup import MessageDedup
        d = MessageDedup(ttl_s=10.0)
        assert d.is_duplicate("") is False
        assert d.is_duplicate("") is False

    def test_capacity_eviction(self):
        from rlm.server.dedup import MessageDedup
        d = MessageDedup(ttl_s=60.0, max_entries=3)
        d.is_duplicate("a")
        d.is_duplicate("b")
        d.is_duplicate("c")
        assert d.seen_count() == 3
        d.is_duplicate("d")  # evicts "a"
        assert d.seen_count() == 3
        assert d.is_duplicate("a") is False  # "a" foi evicted

    def test_ttl_eviction(self):
        from rlm.server.dedup import MessageDedup
        d = MessageDedup(ttl_s=0.05, max_entries=100)
        d.is_duplicate("msg1")
        time.sleep(0.1)  # esperar TTL expirar
        assert d.is_duplicate("msg1") is False  # não é mais dupe

    def test_dispose_clears_cache(self):
        from rlm.server.dedup import MessageDedup
        d = MessageDedup()
        d.is_duplicate("msg1")
        d.dispose()
        assert d.seen_count() == 0

    def test_thread_safety(self):
        from rlm.server.dedup import MessageDedup
        d = MessageDedup(ttl_s=60.0, max_entries=10_000)
        results = []

        def worker(start: int):
            for i in range(100):
                results.append(d.is_duplicate(f"msg-{start + i}"))

        threads = [threading.Thread(target=worker, args=(i * 100,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 1000 mensagens únicas → nenhum é duplicata
        assert all(r is False for r in results)
        assert d.seen_count() == 1000


# ===========================================================================
# heartbeat.py
# ===========================================================================

class TestSyncHeartbeat:

    def test_calls_action_periodically(self):
        from rlm.server.heartbeat import SyncHeartbeat
        calls = []
        hb = SyncHeartbeat(action=lambda: calls.append(1), interval_s=0.05)
        hb.start()
        time.sleep(0.2)
        hb.dispose()
        assert len(calls) >= 2

    def test_stop_stops_calling(self):
        from rlm.server.heartbeat import SyncHeartbeat
        calls = []
        hb = SyncHeartbeat(action=lambda: calls.append(1), interval_s=0.05)
        hb.start()
        time.sleep(0.1)
        hb.stop()
        count_at_stop = len(calls)
        time.sleep(0.15)
        assert len(calls) - count_at_stop <= 1  # no máximo 1 call extra

    def test_dispose_idempotent(self):
        from rlm.server.heartbeat import SyncHeartbeat
        hb = SyncHeartbeat(action=lambda: None)
        hb.dispose()
        hb.dispose()  # não deve lançar

    def test_cancelled_token_prevents_start(self):
        from rlm.server.heartbeat import SyncHeartbeat
        from rlm.core.cancellation import CancellationToken
        calls = []
        hb = SyncHeartbeat(
            action=lambda: calls.append(1),
            cancel_token=CancellationToken.CANCELLED,
        )
        hb.start()
        time.sleep(0.1)
        assert len(calls) == 0


class TestAsyncHeartbeat:

    def test_calls_action_periodically(self):
        from rlm.server.heartbeat import AsyncHeartbeat

        async def _run():
            calls = []
            hb = AsyncHeartbeat(action=lambda: calls.append(1), interval_s=0.05)
            await hb.start()
            await asyncio.sleep(0.2)
            hb.dispose()
            return len(calls)

        assert asyncio.run(_run()) >= 2

    def test_context_manager(self):
        from rlm.server.heartbeat import AsyncHeartbeat

        async def _run():
            calls = []
            async with AsyncHeartbeat(action=lambda: calls.append(1), interval_s=0.05):
                await asyncio.sleep(0.15)
            return len(calls)

        assert asyncio.run(_run()) >= 2


# ===========================================================================
# gateway_state.py
# ===========================================================================

class TestGatewayStateMachine:

    def test_initial_state_is_idle(self):
        from rlm.server.gateway_state import GatewayStateMachine, GatewayState
        sm = GatewayStateMachine("test")
        assert sm.state == GatewayState.IDLE

    def test_valid_transition_succeeds(self):
        from rlm.server.gateway_state import GatewayStateMachine, GatewayState
        sm = GatewayStateMachine("test")
        assert sm.transition(GatewayState.CONNECTING) is True
        assert sm.state == GatewayState.CONNECTING

    def test_invalid_transition_fails(self):
        from rlm.server.gateway_state import GatewayStateMachine, GatewayState
        sm = GatewayStateMachine("test")
        # IDLE → RUNNING é inválido (deve passar por CONNECTING)
        assert sm.transition(GatewayState.RUNNING) is False
        assert sm.state == GatewayState.IDLE

    def test_full_lifecycle(self):
        from rlm.server.gateway_state import GatewayStateMachine, GatewayState
        sm = GatewayStateMachine("test")
        assert sm.transition(GatewayState.CONNECTING)
        assert sm.transition(GatewayState.RUNNING)
        assert sm.transition(GatewayState.DRAINING)
        assert sm.transition(GatewayState.STOPPED)
        assert sm.is_terminal

    def test_error_and_reconnect_cycle(self):
        from rlm.server.gateway_state import GatewayStateMachine, GatewayState
        sm = GatewayStateMachine("test")
        sm.transition(GatewayState.CONNECTING)
        sm.transition(GatewayState.RUNNING)
        sm.transition(GatewayState.ERROR, reason="timeout")
        assert sm.state == GatewayState.ERROR
        sm.transition(GatewayState.RECONNECTING)
        sm.transition(GatewayState.CONNECTING)
        sm.transition(GatewayState.RUNNING)
        assert sm.state == GatewayState.RUNNING

    def test_idempotent_same_state(self):
        from rlm.server.gateway_state import GatewayStateMachine, GatewayState
        sm = GatewayStateMachine("test")
        assert sm.transition(GatewayState.IDLE) is True  # already there

    def test_emits_to_event_bus(self):
        from rlm.server.gateway_state import GatewayStateMachine, GatewayState
        from unittest.mock import MagicMock
        bus = MagicMock()
        sm = GatewayStateMachine("test", event_bus=bus)
        sm.transition(GatewayState.CONNECTING)
        bus.emit.assert_called_once()
        args = bus.emit.call_args
        assert args[0][0] == "gateway_state"

    def test_on_change_listener(self):
        from rlm.server.gateway_state import GatewayStateMachine, GatewayState
        transitions = []
        sm = GatewayStateMachine("test")
        sm.on_change(lambda old, new, reason: transitions.append((old, new, reason)))
        sm.transition(GatewayState.CONNECTING, reason="startup")
        assert len(transitions) == 1
        assert transitions[0] == (GatewayState.IDLE, GatewayState.CONNECTING, "startup")

    def test_dispose_moves_to_stopped(self):
        from rlm.server.gateway_state import GatewayStateMachine, GatewayState
        sm = GatewayStateMachine("test")
        sm.transition(GatewayState.CONNECTING)
        sm.transition(GatewayState.RUNNING)
        sm.dispose()
        assert sm.state == GatewayState.STOPPED

    def test_to_dict(self):
        from rlm.server.gateway_state import GatewayStateMachine, GatewayState
        sm = GatewayStateMachine("telegram")
        d = sm.to_dict()
        assert d["gateway"] == "telegram"
        assert d["state"] == "idle"
        assert "time_in_state_s" in d


# ===========================================================================
# message_envelope.py
# ===========================================================================

class TestInboundMessage:

    def test_create_minimal(self):
        from rlm.server.message_envelope import InboundMessage
        msg = InboundMessage(channel="test", client_id="test:1", text="hello")
        assert msg.channel == "test"
        assert msg.text == "hello"
        assert msg.timestamp > 0

    def test_frozen(self):
        from rlm.server.message_envelope import InboundMessage
        msg = InboundMessage(channel="test", client_id="test:1", text="hello")
        with pytest.raises(AttributeError):
            msg.text = "changed"  # type: ignore[misc]

    def test_round_trip_serialization(self):
        from rlm.server.message_envelope import InboundMessage
        msg = InboundMessage(
            channel="whatsapp", client_id="whatsapp:5511",
            text="oi", msg_id="wamid.123", from_user="User",
        )
        d = msg.to_dict()
        msg2 = InboundMessage.from_dict(d)
        assert msg2.channel == msg.channel
        assert msg2.text == msg.text
        assert msg2.msg_id == msg.msg_id

    def test_from_dict_ignores_unknown_fields(self):
        from rlm.server.message_envelope import InboundMessage
        d = {"channel": "test", "client_id": "t:1", "text": "hi", "unknown_field": 42}
        msg = InboundMessage.from_dict(d)
        assert msg.text == "hi"


class TestNormalizersTelegram:

    def test_normalize_telegram(self):
        from rlm.server.message_envelope import normalize_telegram
        msg = normalize_telegram(12345, "hello", username="user1", msg_id=99)
        assert msg.channel == "telegram"
        assert msg.client_id == "tg:12345"
        assert msg.text == "hello"
        assert msg.msg_id == "99"


class TestNormalizersWhatsapp:

    def test_normalize_text(self):
        from rlm.server.message_envelope import normalize_whatsapp
        msg = normalize_whatsapp({
            "type": "text", "from": "5511999", "id": "wamid.1",
            "text": {"body": "oi"},
        })
        assert msg is not None
        assert msg.channel == "whatsapp"
        assert msg.text == "oi"

    def test_normalize_status_returns_none(self):
        from rlm.server.message_envelope import normalize_whatsapp
        msg = normalize_whatsapp({"type": "status", "from": "5511", "id": "x"})
        assert msg is None

    def test_normalize_image(self):
        from rlm.server.message_envelope import normalize_whatsapp
        msg = normalize_whatsapp({
            "type": "image", "from": "5511999", "id": "wamid.2",
            "image": {"id": "media123", "mime_type": "image/jpeg", "caption": "foto"},
        })
        assert msg is not None
        assert "IMAGE" in msg.text
        assert msg.content_type == "image"


class TestNormalizersSlack:

    def test_normalize_basic(self):
        from rlm.server.message_envelope import normalize_slack
        msg = normalize_slack(
            {"user": "U123", "text": "<@U999> deploy now", "channel": "C456"},
            team_id="T789",
        )
        assert msg is not None
        assert msg.text == "deploy now"
        assert msg.client_id == "slack:T789:C456"

    def test_empty_after_mention_strip(self):
        from rlm.server.message_envelope import normalize_slack
        msg = normalize_slack(
            {"user": "U123", "text": "<@U999>", "channel": "C456"},
        )
        assert msg is None


class TestNormalizersDiscord:

    def test_normalize_command(self):
        from rlm.server.message_envelope import normalize_discord
        msg = normalize_discord({
            "type": "command", "command": "ask",
            "args": {"q": "hello"}, "user_id": "U1", "guild_id": "G1",
        })
        assert msg is not None
        assert "/ask" in msg.text

    def test_empty_content_returns_none(self):
        from rlm.server.message_envelope import normalize_discord
        msg = normalize_discord({"type": "message", "content": "", "user_id": "U1"})
        assert msg is None


# ===========================================================================
# health_monitor.py
# ===========================================================================

class TestHealthMonitor:

    def test_register_and_check(self):
        from rlm.server.health_monitor import HealthMonitor
        m = HealthMonitor()
        m.register("test", lambda: True)
        results = m.check_all()
        assert results["test"]["healthy"] is True

    def test_failing_check(self):
        from rlm.server.health_monitor import HealthMonitor
        m = HealthMonitor(max_failures=2)
        m.register("bad", lambda: False)
        results = m.check_all()
        assert results["bad"]["healthy"] is False
        assert results["bad"]["consecutive_failures"] == 1

    def test_exception_counts_as_failure(self):
        from rlm.server.health_monitor import HealthMonitor
        m = HealthMonitor()
        m.register("err", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        results = m.check_all()
        assert results["err"]["healthy"] is False

    def test_health_report_status(self):
        from rlm.server.health_monitor import HealthMonitor
        m = HealthMonitor()
        m.register("ok", lambda: True)
        m.register("bad", lambda: False)
        m.check_all()
        report = m.get_health_report()
        assert report["status"] == "degraded"

    def test_health_report_all_healthy(self):
        from rlm.server.health_monitor import HealthMonitor
        m = HealthMonitor()
        m.register("a", lambda: True)
        m.register("b", lambda: True)
        m.check_all()
        report = m.get_health_report()
        assert report["status"] == "healthy"

    def test_dispose_clears(self):
        from rlm.server.health_monitor import HealthMonitor
        m = HealthMonitor()
        m.register("test", lambda: True)
        m.dispose()
        report = m.get_health_report()
        assert len(report["components"]) == 0


# ===========================================================================
# drain.py
# ===========================================================================

class TestDrainGuard:

    def test_initial_state(self):
        from rlm.server.drain import DrainGuard
        g = DrainGuard()
        assert g.is_draining is False
        assert g.active_count == 0

    def test_enter_exit_request(self):
        from rlm.server.drain import DrainGuard
        g = DrainGuard()
        assert g.enter_request() is True
        assert g.active_count == 1
        g.exit_request()
        assert g.active_count == 0

    def test_draining_rejects_new(self):
        from rlm.server.drain import DrainGuard
        g = DrainGuard()
        g.start_draining()
        assert g.enter_request() is False

    def test_wait_active_returns_immediately_when_empty(self):
        from rlm.server.drain import DrainGuard
        g = DrainGuard()
        g.start_draining()
        assert g.wait_active(timeout=1.0) is True

    def test_wait_active_waits_for_completion(self):
        from rlm.server.drain import DrainGuard
        g = DrainGuard()
        g.enter_request()
        g.start_draining()

        def finish():
            time.sleep(0.05)
            g.exit_request()

        threading.Thread(target=finish, daemon=True).start()
        assert g.wait_active(timeout=1.0) is True

    def test_wait_active_timeout(self):
        from rlm.server.drain import DrainGuard
        g = DrainGuard()
        g.enter_request()
        g.start_draining()
        assert g.wait_active(timeout=0.05) is False


# ===========================================================================
# backpressure.py
# ===========================================================================

class TestSyncGate:

    def test_basic_acquire_release(self):
        from rlm.server.backpressure import SyncGate
        gate = SyncGate(max_concurrent=2)
        with gate.acquire():
            assert gate.active == 1
        assert gate.active == 0

    def test_concurrent_limit(self):
        from rlm.server.backpressure import SyncGate, ConcurrencyExceeded
        gate = SyncGate(max_concurrent=1)
        barrier = threading.Event()
        started = threading.Event()

        def hold():
            with gate.acquire():
                started.set()
                barrier.wait()

        t = threading.Thread(target=hold, daemon=True)
        t.start()
        started.wait()  # garante que a thread está dentro do gate

        with pytest.raises(ConcurrencyExceeded):
            with gate.acquire(timeout=0.1):
                pass

        barrier.set()
        t.join()


class TestAsyncGate:

    def test_basic_acquire_release(self):
        from rlm.server.backpressure import AsyncGate

        async def _run():
            gate = AsyncGate(max_concurrent=2)
            async with gate.acquire():
                assert gate.active == 1
            assert gate.active == 0

        asyncio.run(_run())

    def test_concurrent_limit(self):
        from rlm.server.backpressure import AsyncGate, ConcurrencyExceeded

        async def _run():
            gate = AsyncGate(max_concurrent=1)
            held = asyncio.Event()

            async def hold():
                async with gate.acquire():
                    held.set()
                    await asyncio.sleep(1.0)

            task = asyncio.create_task(hold())
            await held.wait()

            with pytest.raises(ConcurrencyExceeded):
                async with gate.acquire(timeout=0.1):
                    pass

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())


# ===========================================================================
# chunker.py
# ===========================================================================

class TestSmartChunk:

    def test_short_text_single_chunk(self):
        from rlm.server.chunker import smart_chunk
        result = smart_chunk("hello world", max_chars=100)
        assert result == ["hello world"]

    def test_splits_by_paragraphs(self):
        from rlm.server.chunker import smart_chunk
        text = "Parágrafo 1.\n\nParágrafo 2.\n\nParágrafo 3."
        result = smart_chunk(text, max_chars=25)
        assert len(result) >= 2
        for chunk in result:
            assert len(chunk) <= 25

    def test_splits_by_lines_fallback(self):
        from rlm.server.chunker import smart_chunk
        text = "Linha 1\nLinha 2\nLinha 3\nLinha 4"
        result = smart_chunk(text, max_chars=15)
        assert len(result) >= 2

    def test_preserves_code_blocks(self):
        from rlm.server.chunker import smart_chunk
        text = "Antes\n\n```python\nprint('hello')\n```\n\nDepois"
        result = smart_chunk(text, max_chars=100)
        # Deve manter o bloco de código inteiro
        full = "\n".join(result)
        assert "```python" in full
        assert "print('hello')" in full

    def test_empty_text(self):
        from rlm.server.chunker import smart_chunk
        assert smart_chunk("") == [""]
        assert smart_chunk("", max_chars=10) == [""]

    def test_very_long_word_hard_split(self):
        from rlm.server.chunker import smart_chunk
        text = "a" * 100
        result = smart_chunk(text, max_chars=30)
        assert all(len(c) <= 30 for c in result)
        assert "".join(result) == text

    def test_headers_split(self):
        from rlm.server.chunker import smart_chunk
        text = "# Section 1\nContent 1\n\n# Section 2\nContent 2\n\n# Section 3\nContent 3"
        result = smart_chunk(text, max_chars=35)
        assert len(result) >= 2

    def test_channel_limits_exist(self):
        from rlm.server.chunker import TELEGRAM_LIMIT, WHATSAPP_LIMIT, DISCORD_LIMIT, SLACK_LIMIT
        assert TELEGRAM_LIMIT == 4000
        assert WHATSAPP_LIMIT == 4000
        assert DISCORD_LIMIT == 1900
        assert SLACK_LIMIT == 3900
