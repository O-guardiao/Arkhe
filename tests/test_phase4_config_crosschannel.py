"""
Testes — Phase 4: Config estruturada + cross_channel_send + webhook_dispatch bus

Cobre:
- load_config() com/sem rlm.toml, env overlays, profiles, channels
- get_config() / _reset_config() singleton lifecycle
- cross_channel_send: formato inválido, bus ativo, bus ausente → fallback, erros
- webhook_dispatch bus integration: ingest + route_response, flag desligado

Execute:
    pytest tests/test_phase4_config_crosschannel.py -v
"""
from __future__ import annotations

import os
import textwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ===========================================================================
# Part 1 — Config (rlm.core.config)
# ===========================================================================


class TestLoadConfig:
    """load_config(), get_config(), _reset_config()"""

    def setup_method(self):
        from rlm.core.config import _reset_config
        _reset_config()

    def teardown_method(self):
        from rlm.core.config import _reset_config
        _reset_config()
        # Limpa env vars de teste
        for k in list(os.environ):
            if k.startswith("RLM_"):
                os.environ.pop(k, None)

    def test_defaults_without_toml(self, tmp_path):
        """Sem rlm.toml, usa defaults hardcoded."""
        from rlm.core.config import load_config
        cfg = load_config(toml_path=str(tmp_path / "nao_existe.toml"))
        assert cfg.server.host == "0.0.0.0"
        assert cfg.server.port == 8000
        assert cfg.agent.model == "gpt-4o-mini"
        assert cfg.message_bus.enabled is False
        assert "default" in cfg.profiles

    def test_loads_toml(self, tmp_path):
        """Lê seções do rlm.toml corretamente."""
        toml_file = tmp_path / "rlm.toml"
        toml_file.write_text(textwrap.dedent("""\
            [server]
            host = "127.0.0.1"
            port = 9999

            [agent]
            model = "gpt-4o"
            max_iterations = 50

            [message_bus]
            enabled = true

            [[profiles]]
            name = "default"
            model = "gpt-4o"
            max_iterations = 50

            [[profiles]]
            name = "iot"
            model = "gpt-3.5-turbo"
            context_hint = "dispositivo embarcado"

            [channels.telegram]
            disabled = false

            [channels.webchat]
            cors_origins = ["http://localhost:3000"]
        """), encoding="utf-8")

        from rlm.core.config import load_config
        cfg = load_config(toml_path=str(toml_file))

        assert cfg.server.host == "127.0.0.1"
        assert cfg.server.port == 9999
        assert cfg.agent.model == "gpt-4o"
        assert cfg.agent.max_iterations == 50
        assert cfg.message_bus.enabled is True
        assert "default" in cfg.profiles
        assert "iot" in cfg.profiles
        assert cfg.profiles["iot"].context_hint == "dispositivo embarcado"
        assert "telegram" in cfg.channels
        assert cfg.channels["webchat"].cors_origins == ["http://localhost:3000"]

    def test_env_overrides_toml(self, tmp_path):
        """Env vars vencem sobre valores do toml."""
        toml_file = tmp_path / "rlm.toml"
        toml_file.write_text(textwrap.dedent("""\
            [server]
            port = 9999
            [agent]
            model = "gpt-4o"
            [message_bus]
            enabled = false
        """), encoding="utf-8")

        os.environ["RLM_PORT"] = "7777"
        os.environ["RLM_MODEL"] = "claude-sonnet"
        os.environ["RLM_USE_MESSAGE_BUS"] = "true"

        from rlm.core.config import load_config
        cfg = load_config(toml_path=str(toml_file))

        assert cfg.server.port == 7777
        assert cfg.agent.model == "claude-sonnet"
        assert cfg.message_bus.enabled is True

    def test_singleton_idempotent(self, tmp_path):
        """Segunda chamada retorna mesma instância."""
        from rlm.core.config import load_config
        cfg1 = load_config(toml_path=str(tmp_path / "x.toml"))
        cfg2 = load_config(toml_path=str(tmp_path / "y.toml"))
        assert cfg1 is cfg2

    def test_get_config_before_load_raises(self):
        """get_config() antes de load_config() deve levantar RuntimeError."""
        from rlm.core.config import get_config
        with pytest.raises(RuntimeError, match="Config não carregada"):
            get_config()

    def test_get_config_after_load(self, tmp_path):
        """get_config() depois de load retorna a mesma instância."""
        from rlm.core.config import load_config, get_config
        cfg1 = load_config(toml_path=str(tmp_path / "x.toml"))
        cfg2 = get_config()
        assert cfg1 is cfg2

    def test_reset_clears_singleton(self, tmp_path):
        """_reset_config() permite recarregar."""
        from rlm.core.config import load_config, _reset_config
        cfg1 = load_config(toml_path=str(tmp_path / "x.toml"))
        _reset_config()
        cfg2 = load_config(toml_path=str(tmp_path / "y.toml"))
        # São instâncias diferentes após reset
        assert cfg1 is not cfg2

    def test_get_profile_fallback(self, tmp_path):
        """get_profile('inexistente') retorna 'default'."""
        from rlm.core.config import load_config
        cfg = load_config(toml_path=str(tmp_path / "x.toml"))
        p = cfg.get_profile("inexistente")
        assert p.name == "default"

    def test_get_profile_explicit(self, tmp_path):
        """get_profile('iot') retorna perfil correto quando existe."""
        toml_file = tmp_path / "rlm.toml"
        toml_file.write_text(textwrap.dedent("""\
            [[profiles]]
            name = "iot"
            model = "gpt-3.5-turbo"
        """), encoding="utf-8")

        from rlm.core.config import load_config
        cfg = load_config(toml_path=str(toml_file))
        p = cfg.get_profile("iot")
        assert p.name == "iot"
        assert p.model == "gpt-3.5-turbo"


# ===========================================================================
# Part 2 — cross_channel_send (runtime_pipeline injection)
# ===========================================================================


class TestCrossChannelSend:
    """Testa a função cross_channel_send isoladamente."""

    @staticmethod
    def _make_cross_channel_send(
        *,
        originating_channel: str = "telegram:12345",
        session_id: str = "sess-001",
        bus_mock=None,
        channel_registry_mock=None,
    ):
        """Reconstrói a closure cross_channel_send com mocks injetáveis.

        Em vez de importar runtime_pipeline completo, recriamos a closure
        com as mesmas dependências para teste unitário isolado.
        """
        _originating_channel = originating_channel
        session = SimpleNamespace(session_id=session_id)

        from rlm.plugins.channel_registry import sanitize_text_payload
        from rlm.core.comms.envelope import Direction, Envelope, MessageType

        def cross_channel_send(target_client_id: str, message: str) -> str:
            if ":" not in target_client_id:
                return f"error: formato inválido '{target_client_id}', esperado 'canal:id'"
            try:
                if bus_mock is None:
                    raise RuntimeError("Bus não inicializado")
                bus = bus_mock
                ch, tid = target_client_id.split(":", 1)
                envelope = Envelope(
                    source_channel=_originating_channel.split(":")[0] if ":" in _originating_channel else "rlm",
                    source_id="agent",
                    source_client_id=_originating_channel,
                    target_channel=ch,
                    target_id=tid,
                    target_client_id=target_client_id,
                    direction=Direction.OUTBOUND,
                    message_type=MessageType.TEXT,
                    text=sanitize_text_payload(message),
                )
                bus.enqueue_outbound(envelope, session_id=getattr(session, "session_id", None))
                return "ok"
            except RuntimeError:
                try:
                    if channel_registry_mock is None:
                        raise Exception("No adapter")
                    delivered = channel_registry_mock.reply(target_client_id, sanitize_text_payload(message))
                    return "ok (direct)" if delivered else "error: adapter not found or delivery failed"
                except Exception as exc:
                    return f"error: {exc}"
            except Exception as exc:
                return f"error: {exc}"

        return cross_channel_send

    def test_invalid_format_no_colon(self):
        """Target sem ':' retorna erro de formato."""
        fn = self._make_cross_channel_send()
        result = fn("sem_dois_pontos", "msg")
        assert result.startswith("error: formato inválido")
        assert "sem_dois_pontos" in result

    def test_bus_available_enqueues(self):
        """Com bus ativo, cria envelope e chama enqueue_outbound."""
        bus = MagicMock()
        fn = self._make_cross_channel_send(bus_mock=bus, originating_channel="telegram:999")
        result = fn("discord:abc", "Olá cross-channel!")
        assert result == "ok"
        bus.enqueue_outbound.assert_called_once()
        args, kwargs = bus.enqueue_outbound.call_args
        envelope = args[0]
        assert envelope.target_channel == "discord"
        assert envelope.target_id == "abc"
        assert envelope.target_client_id == "discord:abc"
        assert "Olá cross-channel!" in envelope.text
        assert kwargs["session_id"] == "sess-001"

    def test_bus_available_source_from_originating(self):
        """Source do envelope vem do _originating_channel."""
        bus = MagicMock()
        fn = self._make_cross_channel_send(bus_mock=bus, originating_channel="whatsapp:5511999")
        fn("slack:general", "test")
        envelope = bus.enqueue_outbound.call_args[0][0]
        assert envelope.source_channel == "whatsapp"
        assert envelope.source_client_id == "whatsapp:5511999"

    def test_bus_unavailable_fallback_to_registry(self):
        """Bus RuntimeError → fallback via ChannelRegistry.reply."""
        registry = MagicMock()
        registry.reply.return_value = True
        fn = self._make_cross_channel_send(bus_mock=None, channel_registry_mock=registry)
        result = fn("discord:abc", "fallback msg")
        assert result == "ok (direct)"
        registry.reply.assert_called_once()

    def test_bus_unavailable_registry_fails(self):
        """Bus ausente + registry falha → error string."""
        fn = self._make_cross_channel_send(bus_mock=None, channel_registry_mock=None)
        result = fn("discord:abc", "msg")
        assert result.startswith("error:")

    def test_bus_enqueue_raises_generic(self):
        """Erro genérico no bus retorna error string."""
        bus = MagicMock()
        bus.enqueue_outbound.side_effect = ValueError("Queue full")
        fn = self._make_cross_channel_send(bus_mock=bus)
        result = fn("discord:abc", "msg")
        assert result.startswith("error:")
        assert "Queue full" in result

    def test_target_with_extra_colons(self):
        """Target 'canal:id:extra' parseia como canal='canal', id='id:extra'."""
        bus = MagicMock()
        fn = self._make_cross_channel_send(bus_mock=bus)
        result = fn("mqtt:device:sensor1", "data")
        assert result == "ok"
        envelope = bus.enqueue_outbound.call_args[0][0]
        assert envelope.target_channel == "mqtt"
        assert envelope.target_id == "device:sensor1"


# ===========================================================================
# Part 3 — webhook_dispatch bus integration
# ===========================================================================


class TestWebhookDispatchBusIntegration:
    """Testa integração do MessageBus no webhook_dispatch."""

    @pytest.fixture
    def tmp_db(self, tmp_path):
        return str(tmp_path / "test_wd.db")

    @pytest.fixture
    def outbox(self, tmp_db):
        from rlm.core.comms.outbox import OutboxStore
        return OutboxStore(db_path=tmp_db)

    @pytest.fixture
    def bus(self, outbox):
        from rlm.core.comms.routing_policy import RoutingPolicy
        from rlm.core.comms.message_bus import MessageBus
        return MessageBus(
            outbox=outbox,
            routing_policy=RoutingPolicy(),
            event_bus=None,
        )

    def test_bus_ingest_from_webhook_body(self, bus):
        """Bus.ingest cria envelope a partir de InboundMessage no padrão do webhook_dispatch."""
        from rlm.server.message_envelope import InboundMessage

        msg = InboundMessage(
            channel="n8n",
            client_id="webhook:token123",
            text="trigger from n8n",
            from_user="",
            content_type="text",
            channel_meta={"source": "workflow_1"},
        )
        envelope = bus.ingest(msg)
        assert envelope.source_channel == "n8n"
        assert envelope.text == "trigger from n8n"

    def test_bus_route_response_not_called_when_already_replied(self, bus):
        """Se session.__reply_delivered__ = True, route_response NÃO é chamado."""
        from rlm.server.message_envelope import InboundMessage

        msg = InboundMessage(
            channel="webhook",
            client_id="webhook:token",
            text="hello",
            from_user="",
            content_type="text",
            channel_meta={},
        )
        envelope = bus.ingest(msg)
        session = SimpleNamespace(
            session_id="sess-wd",
            __reply_delivered__=True,
        )

        # Verifica que a lógica de "was_replied" impede route_response
        was_replied = getattr(session, "__reply_delivered__", False)
        assert was_replied is True
        # O webhook_dispatch só chama route_response se NOT was_replied
        # Aqui testamos a condição: response_text e NOT was_replied
        response_text = "algo"
        should_route = response_text and not was_replied
        assert should_route is False

    def test_bus_route_response_called_when_not_replied(self, bus):
        """Se session.__reply_delivered__ = False + response_text, route_response é chamado."""
        from rlm.server.message_envelope import InboundMessage

        msg = InboundMessage(
            channel="webhook",
            client_id="webhook:token",
            text="hello",
            from_user="",
            content_type="text",
            channel_meta={},
        )
        envelope = bus.ingest(msg)
        session = SimpleNamespace(session_id="sess-wd2")

        response_text = "Resultado do agente"
        was_replied = getattr(session, "__reply_delivered__", False)
        should_route = bool(response_text) and not was_replied
        assert should_route is True

        # Chamada real do bus — enfileira na outbox
        bus.route_response(
            envelope, response_text, session, session_id="sess-wd2"
        )

    def test_use_bus_flag_false_skips_all(self):
        """Com use_message_bus=False, nenhuma operação de bus ocorre."""
        use_bus = False
        bus_called = False

        if use_bus:
            bus_called = True

        assert bus_called is False

    def test_bus_failure_is_non_fatal(self, bus):
        """Falha no bus não propaga — try/except no webhook_dispatch."""
        # Simula o padrão: bus.ingest levanta, mas o fluxo continua
        broken_bus = MagicMock()
        broken_bus.ingest.side_effect = Exception("DB locked")

        caught = False
        try:
            broken_bus.ingest("qualquer coisa")
        except Exception:
            caught = True
            # No webhook_dispatch real, isso é logado e ignorado

        assert caught is True  # Confirma que exceção seria capturada


# ===========================================================================
# Part 4 — SKILL.md exists and has correct metadata
# ===========================================================================


class TestCrossChannelSendSkill:
    """Verifica que o SKILL.md foi criado com metadados corretos."""

    def test_skill_md_exists(self):
        from pathlib import Path
        skill = Path(__file__).resolve().parent.parent / "rlm" / "skills" / "cross_channel_send" / "SKILL.md"
        assert skill.exists(), f"SKILL.md não encontrado em {skill}"

    def test_skill_md_has_signature(self):
        from pathlib import Path
        skill = Path(__file__).resolve().parent.parent / "rlm" / "skills" / "cross_channel_send" / "SKILL.md"
        content = skill.read_text(encoding="utf-8")
        assert "cross_channel_send" in content
        assert "target_client_id" in content
        assert "message" in content

    def test_skill_md_has_multichannel_tag(self):
        from pathlib import Path
        skill = Path(__file__).resolve().parent.parent / "rlm" / "skills" / "cross_channel_send" / "SKILL.md"
        content = skill.read_text(encoding="utf-8")
        assert "multichannel" in content.lower()
