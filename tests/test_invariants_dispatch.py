"""
test_invariants_dispatch — Invariante III: rotas determinísticas vs LLM.

Garante que o pipeline de dispatch é previsível:
  - RuntimeDispatchServices é construível com mocks
  - SessionIdentity é propagável pelo pipeline
  - Operações puras de session_key não dependem de IO/LLM
  - Webhook info endpoint funciona sem LLM
"""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest


class TestDispatchDeterministic:
    """Pipeline de dispatch tem contrato previsível."""

    def test_runtime_dispatch_services_constructible(self):
        """RuntimeDispatchServices aceita construção com mocks."""
        from rlm.server.runtime_pipeline import RuntimeDispatchServices

        services = RuntimeDispatchServices(
            session_manager=MagicMock(),
            supervisor=MagicMock(),
            plugin_loader=MagicMock(),
            event_router=MagicMock(),
            hooks=MagicMock(),
            skill_loader=MagicMock(),
            runtime_guard=None,
            eligible_skills=[],
            skill_context="",
            exec_approval=None,
            exec_approval_required=False,
            recursion_daemon=None,
        )
        assert services.session_manager is not None
        assert services.recursion_daemon is None

    def test_runtime_dispatch_services_defaults(self):
        """RuntimeDispatchServices aplica defaults corretos."""
        from rlm.server.runtime_pipeline import RuntimeDispatchServices

        services = RuntimeDispatchServices(
            session_manager=MagicMock(),
            supervisor=MagicMock(),
            plugin_loader=MagicMock(),
            event_router=MagicMock(),
            hooks=MagicMock(),
            skill_loader=MagicMock(),
        )
        assert services.runtime_guard is None
        assert services.eligible_skills == []
        assert services.skill_context == ""
        assert services.exec_approval is None
        assert services.exec_approval_required is False
        assert services.recursion_daemon is None

    def test_session_identity_usable_as_dispatch_context(self):
        """SessionIdentity pode ser construído de dados típicos de dispatch."""
        from rlm.core.session.session_key import SessionIdentity, create_session_id

        sid = create_session_id()
        identity = SessionIdentity(
            session_id=sid,
            client_id="telegram:12345",
            user_id="user_abc",
            channel="telegram",
            device_id=None,
        )
        assert identity.channel == "telegram"
        assert "12345" in identity.client_id


class TestDeterministicRoutes:
    """Rotas que não precisam de LLM funcionam sem modelo."""

    def test_webhook_info_endpoint_exists(self):
        """Endpoint /api/hooks/info é acessível sem LLM."""
        from rlm.server.webhook_dispatch import create_webhook_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def lifespan(app):
            yield

        app = FastAPI(lifespan=lifespan)
        app.include_router(create_webhook_router("test_token"))

        with TestClient(app) as client:
            resp = client.get("/api/hooks/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"

    def test_session_key_operations_are_pure(self):
        """Operações de SessionKey são puras — sem IO, sem LLM."""
        from rlm.core.session.session_key import (
            create_session_id,
            make_session_id,
            is_session_id,
            SessionKey,
        )

        sid = create_session_id()
        assert is_session_id(sid)
        assert make_session_id(sid) == sid

        key = SessionKey(
            session_id=sid,
            channel_type="telegram",
            channel_id="chat_42",
            user_id="user_1",
        )
        assert key.session_id == sid
        assert key.channel_type == "telegram"
        assert key.channel_id == "chat_42"

    def test_make_session_id_idempotent(self):
        """make_session_id sobre um ID válido retorna o mesmo valor."""
        from rlm.core.session.session_key import create_session_id, make_session_id

        sid = create_session_id()
        assert make_session_id(sid) == sid
        assert make_session_id(make_session_id(sid)) == sid
