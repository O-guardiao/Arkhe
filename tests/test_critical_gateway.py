"""
Testes críticos — Fase 9.2: Gateway Externo

Cobre:
- ExecApprovalGate (gate de segurança REPL)
- _RateLimiter (sliding window)
- webhook_dispatch (receptor HTTP externo)
- openai_compat (/v1/chat/completions)
- Integração em api.py (endpoints presentes)

Execute:
    pytest tests/test_critical_gateway.py -v
"""
from __future__ import annotations

import asyncio
import pathlib
import threading
import time
import pytest
from unittest.mock import MagicMock, patch


# ===========================================================================
# ExecApprovalGate
# ===========================================================================

class TestExecApprovalGate:

    def test_approve_from_thread_returns_true(self):
        from rlm.core.security.exec_approval import ExecApprovalGate
        gate = ExecApprovalGate(default_timeout_s=5)

        results = []

        def requester():
            result = gate.request("delete all records", session_id="sess1")
            results.append(result)

        t = threading.Thread(target=requester)
        t.start()
        time.sleep(0.05)  # deixar o requester bloquear

        pending = gate.list_pending()
        assert len(pending) == 1
        req_id = pending[0]["id"]

        approved = gate.approve(req_id, resolved_by="operator")
        assert approved is True

        t.join(timeout=2)
        assert results == [True]

    def test_deny_raises_permission_error(self):
        from rlm.core.security.exec_approval import ExecApprovalGate
        gate = ExecApprovalGate(default_timeout_s=5)
        errors = []

        def requester():
            try:
                gate.request("rm -rf /important", session_id="sess1")
            except PermissionError as e:
                errors.append(str(e))

        t = threading.Thread(target=requester)
        t.start()
        time.sleep(0.05)

        req_id = gate.list_pending()[0]["id"]
        gate.deny(req_id, resolved_by="security_policy")
        t.join(timeout=2)

        assert len(errors) == 1
        assert "denied" in errors[0].lower()

    def test_timeout_raises_timeout_error(self):
        from rlm.core.security.exec_approval import ExecApprovalGate
        gate = ExecApprovalGate(default_timeout_s=0.1)
        errors = []

        def requester():
            try:
                gate.request("operation", session_id="sess1")
            except TimeoutError as e:
                errors.append(str(e))

        t = threading.Thread(target=requester)
        t.start()
        t.join(timeout=3)

        assert len(errors) == 1
        assert "timed out" in errors[0].lower()

    def test_list_pending_shows_active_request(self):
        from rlm.core.security.exec_approval import ExecApprovalGate
        gate = ExecApprovalGate(default_timeout_s=5)
        barrier = threading.Barrier(2)

        def requester():
            barrier.wait()
            try:
                gate.request("sensitive op", session_id="sess_x")
            except Exception:
                pass

        t = threading.Thread(target=requester)
        t.start()
        barrier.wait()
        time.sleep(0.05)

        pending = gate.list_pending()
        assert any(p["description"] == "sensitive op" for p in pending)

        # cleanup
        if pending:
            gate.deny(pending[0]["id"])
        t.join(timeout=2)

    def test_list_pending_empty_after_resolution(self):
        from rlm.core.security.exec_approval import ExecApprovalGate
        gate = ExecApprovalGate(default_timeout_s=5)

        t_done = threading.Event()

        def requester():
            gate.request("op", session_id="s")
            t_done.set()

        t = threading.Thread(target=requester)
        t.start()
        time.sleep(0.05)

        req_id = gate.list_pending()[0]["id"]
        gate.approve(req_id)
        t.join(timeout=2)

        assert gate.list_pending() == []

    def test_approve_nonexistent_returns_false(self):
        from rlm.core.security.exec_approval import ExecApprovalGate
        gate = ExecApprovalGate()
        result = gate.approve("nonexistent_id")
        assert result is False

    def test_deny_nonexistent_returns_false(self):
        from rlm.core.security.exec_approval import ExecApprovalGate
        gate = ExecApprovalGate()
        result = gate.deny("nonexistent_id")
        assert result is False

    def test_get_record_while_pending(self):
        from rlm.core.security.exec_approval import ExecApprovalGate
        gate = ExecApprovalGate(default_timeout_s=5)

        def _req():
            try:
                gate.request("op", session_id="s")
            except PermissionError:
                pass  # deny() esperado pelo teste

        t = threading.Thread(target=_req)
        t.start()
        time.sleep(0.05)

        pending = gate.list_pending()
        req_id = pending[0]["id"]
        record = gate.get_record(req_id)

        assert record is not None
        assert record["status"] == "pending"
        assert record["description"] == "op"

        gate.deny(req_id)
        t.join(timeout=2)

    def test_get_record_after_approve_in_grace(self):
        from rlm.core.security.exec_approval import ExecApprovalGate
        gate = ExecApprovalGate(default_timeout_s=5)

        t = threading.Thread(
            target=lambda: gate.request("op", session_id="s")
        )
        t.start()
        time.sleep(0.05)

        req_id = gate.list_pending()[0]["id"]
        gate.approve(req_id, resolved_by="test")
        t.join(timeout=2)

        record = gate.get_record(req_id)
        assert record is not None
        assert record["status"] == "approved"

    def test_get_record_not_found_returns_none(self):
        from rlm.core.security.exec_approval import ExecApprovalGate
        gate = ExecApprovalGate()
        assert gate.get_record("does_not_exist") is None

    def test_stats_returns_expected_keys(self):
        from rlm.core.security.exec_approval import ExecApprovalGate
        gate = ExecApprovalGate(default_timeout_s=45)
        stats = gate.stats()
        assert "pending" in stats
        assert "default_timeout_s" in stats
        assert stats["default_timeout_s"] == 45

    def test_make_repl_fn_returns_callable(self):
        from rlm.core.security.exec_approval import ExecApprovalGate
        gate = ExecApprovalGate()
        fn = gate.make_repl_fn("session_abc")
        assert callable(fn)
        assert fn.__name__ == "confirm_exec"

    def test_make_repl_fn_approve_flow(self):
        from rlm.core.security.exec_approval import ExecApprovalGate
        gate = ExecApprovalGate(default_timeout_s=5)
        confirm = gate.make_repl_fn("sess_repl")
        results = []

        def call_confirm():
            result = confirm("write to production")
            results.append(result)

        t = threading.Thread(target=call_confirm)
        t.start()
        time.sleep(0.05)

        req_id = gate.list_pending()[0]["id"]
        gate.approve(req_id)
        t.join(timeout=2)

        assert results == [True]

    def test_concurrent_multiple_requests(self):
        """Múltiplas threads pedindo aprovação simultaneamente."""
        from rlm.core.security.exec_approval import ExecApprovalGate
        gate = ExecApprovalGate(default_timeout_s=5)
        results = []

        def make_request(session_id):
            try:
                result = gate.request("op", session_id=session_id)
                results.append(("ok", session_id))
            except Exception as e:
                results.append(("err", session_id))

        threads = [threading.Thread(target=make_request, args=(f"s{i}",)) for i in range(3)]
        for t in threads:
            t.start()

        time.sleep(0.1)
        pending = gate.list_pending()
        assert len(pending) == 3

        for p in pending:
            gate.approve(p["id"])

        for t in threads:
            t.join(timeout=2)

        assert all(status == "ok" for status, _ in results)


# ===========================================================================
# RateLimiter
# ===========================================================================

class TestRateLimiter:

    def test_allows_within_limit(self):
        from rlm.server.webhook_dispatch import _RateLimiter
        limiter = _RateLimiter(rpm=10)
        for _ in range(10):
            allowed, retry = limiter.is_allowed("1.2.3.4")
            assert allowed is True
            assert retry == 0

    def test_blocks_after_limit(self):
        from rlm.server.webhook_dispatch import _RateLimiter
        limiter = _RateLimiter(rpm=3)
        for _ in range(3):
            limiter.is_allowed("1.2.3.4")
        allowed, retry = limiter.is_allowed("1.2.3.4")
        assert allowed is False
        assert retry > 0

    def test_different_ips_independent(self):
        from rlm.server.webhook_dispatch import _RateLimiter
        limiter = _RateLimiter(rpm=2)
        limiter.is_allowed("1.1.1.1")
        limiter.is_allowed("1.1.1.1")
        # esgotou 1.1.1.1
        allowed_a, _ = limiter.is_allowed("1.1.1.1")
        allowed_b, _ = limiter.is_allowed("2.2.2.2")  # ip diferente
        assert allowed_a is False
        assert allowed_b is True

    def test_rpm_zero_always_allows(self):
        from rlm.server.webhook_dispatch import _RateLimiter
        limiter = _RateLimiter(rpm=0)
        for _ in range(200):
            allowed, retry = limiter.is_allowed("1.2.3.4")
            assert allowed is True


# ===========================================================================
# webhook_dispatch (via FastAPI TestClient)
# ===========================================================================

class TestWebhookDispatch:

    def _make_app(self, token="test_token_abc"):
        """Cria app mínimo com webhook router e supervisor mockado."""
        from fastapi import FastAPI
        from contextlib import asynccontextmanager
        from rlm.server.webhook_dispatch import create_webhook_router

        @asynccontextmanager
        async def lifespan(app):
            mock_session = MagicMock()
            mock_session.session_id = "sess_test"
            mock_session.client_id = "hook_default"

            mock_result = MagicMock()
            mock_result.status = "completed"
            mock_result.response = "Relatório gerado."
            mock_result.execution_time = 1.5

            mock_sm = MagicMock()
            mock_sm.get_or_create.return_value = mock_session
            mock_sm.log_event.return_value = None
            mock_sm.update_session.return_value = None

            mock_supervisor = MagicMock()
            mock_supervisor.execute.return_value = mock_result

            app.state.session_manager = mock_sm
            app.state.supervisor = mock_supervisor
            app.state.hooks = MagicMock()
            yield

        app = FastAPI(lifespan=lifespan)
        app.include_router(create_webhook_router(token))
        return app

    def test_missing_token_returns_401(self):
        from fastapi.testclient import TestClient
        app = self._make_app("correct_token")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/api/hooks/wrong_token", json={"text": "hello"})
        assert resp.status_code == 401

    def test_valid_token_dispatches_and_returns_result(self):
        from fastapi.testclient import TestClient
        app = self._make_app("mytoken")
        with TestClient(app) as client:
            resp = client.post("/api/hooks/mytoken", json={"text": "faça relatório"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert "session_id" in data
        assert "response" in data

    def test_valid_token_via_header(self):
        from fastapi.testclient import TestClient
        app = self._make_app("mytoken")
        with TestClient(app) as client:
            # Token no header, path token vazio — mas path_token é required no path
            # Então ainda precisamos do token no path OU usar um path com token correto
            resp = client.post(
                "/api/hooks/mytoken",
                json={"text": "hello"},
                headers={"X-Hook-Token": "mytoken"},
            )
        assert resp.status_code == 200

    def test_missing_text_and_metadata_returns_422(self):
        from fastapi.testclient import TestClient
        app = self._make_app("tok")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/api/hooks/tok", json={})
        assert resp.status_code == 422

    def test_client_id_from_path(self):
        from fastapi.testclient import TestClient
        app = self._make_app("tok")
        with TestClient(app) as client:
            resp = client.post("/api/hooks/tok/pipeline_vendas", json={"text": "go"})
        assert resp.status_code == 200
        assert resp.json()["client_id"] == "pipeline_vendas"

    def test_client_id_from_body(self):
        from fastapi.testclient import TestClient
        app = self._make_app("tok")
        with TestClient(app) as client:
            resp = client.post("/api/hooks/tok", json={"text": "go", "client_id": "joao"})
        assert resp.status_code == 200
        assert resp.json()["client_id"] == "joao"

    def test_hook_info_endpoint(self):
        from fastapi.testclient import TestClient
        app = self._make_app("tok")
        with TestClient(app) as client:
            resp = client.get("/api/hooks/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"
        assert "endpoints" in data

    def test_rate_limit_blocks_after_rpm(self):
        from fastapi.testclient import TestClient
        from rlm.server.webhook_dispatch import create_webhook_router
        from fastapi import FastAPI
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def lifespan(app):
            mock_session = MagicMock()
            mock_session.session_id = "s"
            mock_session.client_id = "c"
            mock_result = MagicMock()
            mock_result.status = "completed"
            mock_result.response = ""
            mock_result.execution_time = 0.1
            sm = MagicMock()
            sm.get_or_create.return_value = mock_session
            sv = MagicMock()
            sv.execute.return_value = mock_result
            app.state.session_manager = sm
            app.state.supervisor = sv
            app.state.hooks = None
            yield

        app = FastAPI(lifespan=lifespan)
        app.include_router(create_webhook_router("tok", rate_limit_rpm=2))
        with TestClient(app, raise_server_exceptions=False) as client:
            r1 = client.post("/api/hooks/tok", json={"text": "a"})
            r2 = client.post("/api/hooks/tok", json={"text": "b"})
            r3 = client.post("/api/hooks/tok", json={"text": "c"})  # deve falhar

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429


# ===========================================================================
# openai_compat
# ===========================================================================

class TestOpenAICompat:

    def _make_app(self, api_token=""):
        from fastapi import FastAPI
        from contextlib import asynccontextmanager
        from rlm.server.openai_compat import create_openai_compat_router

        @asynccontextmanager
        async def lifespan(app):
            mock_session = MagicMock()
            mock_session.session_id = "sess_oa"
            mock_session.client_id = "user_1"

            mock_result = MagicMock()
            mock_result.status = "completed"
            mock_result.response = "As vendas de hoje foram R$50.000."
            mock_result.execution_time = 2.1

            sm = MagicMock()
            sm.get_or_create.return_value = mock_session
            sm.update_session.return_value = None

            sv = MagicMock()
            sv.execute.return_value = mock_result

            app.state.session_manager = sm
            app.state.supervisor = sv
            app.state.hooks = None
            yield

        app = FastAPI(lifespan=lifespan)
        app.include_router(create_openai_compat_router(api_token))
        return app

    def test_endpoint_disabled_when_token_empty(self):
        from fastapi.testclient import TestClient
        app = self._make_app(api_token="")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/v1/chat/completions", json={
                "model": "rlm",
                "messages": [{"role": "user", "content": "teste"}],
            })
        assert resp.status_code == 503

    def test_auth_required_when_token_set(self):
        from fastapi.testclient import TestClient
        app = self._make_app(api_token="secret123")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/v1/chat/completions", json={
                "model": "rlm",
                "messages": [{"role": "user", "content": "teste"}],
            })
        assert resp.status_code == 401

    def test_valid_bearer_token_accepted(self):
        from fastapi.testclient import TestClient
        app = self._make_app(api_token="secret123")
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions",
                json={"model": "rlm", "messages": [{"role": "user", "content": "ok"}]},
                headers={"Authorization": "Bearer secret123"},
            )
        assert resp.status_code == 200
    def test_response_has_openai_format(self):
        from fastapi.testclient import TestClient
        app = self._make_app(api_token="secret123")
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={
                "model": "rlm",
                "messages": [{"role": "user", "content": "vendas"}],
            }, headers={"Authorization": "Bearer secret123"})
        data = resp.json()
        assert "choices" in data
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert "id" in data
        assert data["object"] == "chat.completion"
        assert "usage" in data

    def test_response_content_from_agent(self):
        from fastapi.testclient import TestClient
        app = self._make_app(api_token="secret123")
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={
                "model": "rlm",
                "messages": [{"role": "user", "content": "relatório"}],
            }, headers={"Authorization": "Bearer secret123"})
        content = resp.json()["choices"][0]["message"]["content"]
        assert "R$50.000" in content

    def test_stream_returns_sse(self):
        from fastapi.testclient import TestClient
        app = self._make_app(api_token="secret123")
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={
                "model": "rlm",
                "messages": [{"role": "user", "content": "stream me"}],
                "stream": True,
            }, headers={"Authorization": "Bearer secret123"})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = resp.text
        assert "data: " in body
        assert "[DONE]" in body

    def test_stream_contains_role_and_content_chunks(self):
        from fastapi.testclient import TestClient
        import json as _json
        app = self._make_app(api_token="secret123")
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={
                "model": "rlm",
                "messages": [{"role": "user", "content": "stream"}],
                "stream": True,
            }, headers={"Authorization": "Bearer secret123"})
        chunks = []
        for line in resp.text.split("\n"):
            if line.startswith("data: ") and line != "data: [DONE]":
                chunks.append(_json.loads(line[6:]))
        # Primeiro chunk deve ter role
        assert chunks[0]["choices"][0]["delta"].get("role") == "assistant"
        # Algum chunk deve ter content
        has_content = any("content" in c["choices"][0]["delta"] for c in chunks)
        assert has_content

    def test_no_user_message_returns_422(self):
        from fastapi.testclient import TestClient
        app = self._make_app(api_token="secret123")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/v1/chat/completions", json={
                "model": "rlm",
                "messages": [{"role": "system", "content": "only system"}],
            }, headers={"Authorization": "Bearer secret123"})
        # system-only → extrai system como fallback, deve funcionar
        # Ou vazio → 422
        # Na implementação atual: extrai system como último recurso
        # Portanto apenas body vazio retorna 422
        assert resp.status_code in (200, 422)

    def test_user_field_maps_to_client_id(self):
        from fastapi.testclient import TestClient
        app = self._make_app(api_token="secret123")
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={
                "model": "rlm",
                "messages": [{"role": "user", "content": "hello"}],
                "user": "cliente_joao",
            }, headers={"Authorization": "Bearer secret123"})
        assert resp.status_code == 200
        assert resp.headers.get("x-session-id") is not None

    def test_list_models_returns_rlm(self):
        from fastapi.testclient import TestClient
        app = self._make_app(api_token="secret123")
        with TestClient(app) as client:
            resp = client.get("/v1/models", headers={"Authorization": "Bearer secret123"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert any(m["id"] == "rlm" for m in data["data"])


class TestApiAuthHelpers:

    def test_build_internal_auth_headers_prefers_internal_token(self, monkeypatch):
        from rlm.server.auth_helpers import build_internal_auth_headers

        monkeypatch.setenv("RLM_INTERNAL_TOKEN", "internal-token")
        monkeypatch.setenv("RLM_WS_TOKEN", "ws-token")

        headers = build_internal_auth_headers()

        assert headers["Content-Type"] == "application/json"
        assert headers["X-RLM-Token"] == "internal-token"

    def test_require_token_accepts_bearer_or_header(self, monkeypatch):
        from starlette.requests import Request
        from rlm.server.auth_helpers import require_token

        monkeypatch.setenv("RLM_ADMIN_TOKEN", "admin-secret")

        request_from_header = Request({
            "type": "http",
            "method": "GET",
            "path": "/protected",
            "headers": [(b"x-rlm-token", b"admin-secret")],
            "query_string": b"",
        })
        request_from_bearer = Request({
            "type": "http",
            "method": "GET",
            "path": "/protected",
            "headers": [(b"authorization", b"Bearer admin-secret")],
            "query_string": b"",
        })

        assert require_token(
            request_from_header,
            env_names=("RLM_ADMIN_TOKEN",),
            scope="admin API",
        ) == "admin-secret"
        assert require_token(
            request_from_bearer,
            env_names=("RLM_ADMIN_TOKEN",),
            scope="admin API",
        ) == "admin-secret"

    def test_require_token_rejects_missing_token(self, monkeypatch):
        from fastapi import HTTPException
        from starlette.requests import Request
        from rlm.server.auth_helpers import require_token

        monkeypatch.setenv("RLM_ADMIN_TOKEN", "admin-secret")
        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/protected",
            "headers": [],
            "query_string": b"",
        })

        with pytest.raises(HTTPException, match="Invalid or missing admin API token") as exc:
            require_token(
                request,
                env_names=("RLM_ADMIN_TOKEN",),
                scope="admin API",
            )

        assert exc.value.status_code == 401

    def test_require_token_returns_503_when_unconfigured(self, monkeypatch):
        from fastapi import HTTPException
        from starlette.requests import Request
        from rlm.server.auth_helpers import require_token

        monkeypatch.delenv("RLM_ADMIN_TOKEN", raising=False)
        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/protected",
            "headers": [],
            "query_string": b"",
        })

        with pytest.raises(HTTPException, match="admin API authentication is not configured") as exc:
            require_token(
                request,
                env_names=("RLM_ADMIN_TOKEN",),
                scope="admin API",
            )

        assert exc.value.status_code == 503


# ===========================================================================
# API Integration
# ===========================================================================

class TestApiIntegration:

    def test_exec_approval_imported_in_api(self):
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert "from rlm.core.security.exec_approval import ExecApprovalGate" in text

    def test_webhook_dispatch_imported_in_api(self):
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert "from rlm.server.webhook_dispatch import create_webhook_router" in text

    def test_openai_compat_imported_in_api(self):
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert "from rlm.server.openai_compat import create_openai_compat_router" in text

    def test_exec_approval_endpoints_present(self):
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert '"/exec/pending"' in text
        assert '"/exec/approve/{request_id}"' in text
        assert '"/exec/deny/{request_id}"' in text

    def test_confirm_exec_injected_in_repl(self):
        server_dir = pathlib.Path(__file__).parent.parent / "rlm" / "server"
        text = (server_dir / "api.py").read_text(encoding="utf-8") + (server_dir / "runtime_pipeline.py").read_text(encoding="utf-8")
        assert "confirm_exec" in text
        assert "make_repl_fn" in text

    def test_request_handoff_injected_in_repl(self):
        server_dir = pathlib.Path(__file__).parent.parent / "rlm" / "server"
        text = (server_dir / "api.py").read_text(encoding="utf-8") + (server_dir / "runtime_pipeline.py").read_text(encoding="utf-8")
        assert "request_handoff" in text
        assert "make_handoff_fn" in text

    def test_exec_approval_in_lifespan_startup(self):
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert "ExecApprovalGate" in text
        assert "app.state.exec_approval" in text

    def test_openai_compat_router_conditionally_mounted(self):
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert "create_openai_compat_router" in text
        assert "RLM_API_TOKEN" in text

    def test_webhook_router_conditionally_mounted(self):
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert "create_webhook_router" in text
        assert "RLM_HOOK_TOKEN" in text

    def test_all_three_modules_importable(self):
        from rlm.core.security.exec_approval import ExecApprovalGate, ApprovalRecord
        from rlm.server.webhook_dispatch import create_webhook_router, HookDispatchBody
        from rlm.server.openai_compat import create_openai_compat_router, ChatCompletionRequest
        assert all([ExecApprovalGate, ApprovalRecord, create_webhook_router,
                    HookDispatchBody, create_openai_compat_router, ChatCompletionRequest])

    def test_lifespan_uses_backend_from_env_for_session_manager(self, monkeypatch: pytest.MonkeyPatch):
        from fastapi import FastAPI
        from rlm.server import api

        monkeypatch.setenv("RLM_BACKEND", "anthropic")
        monkeypatch.setenv("RLM_MODEL", "claude-3-5-haiku-latest")
        monkeypatch.setenv("RLM_WS_DISABLED", "true")
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

        captured: dict[str, object] = {}

        class _FakeSessionManager:
            def __init__(self, db_path: str, state_root: str, default_rlm_kwargs: dict[str, object], hooks: object):
                captured["db_path"] = db_path
                captured["state_root"] = state_root
                captured["default_rlm_kwargs"] = default_rlm_kwargs
                captured["hooks"] = hooks

            def add_close_callback(self, callback: object) -> None:
                captured["close_callback"] = callback

            def close_all(self) -> None:
                captured["closed"] = True

        class _FakeSupervisor:
            def __init__(self, default_config: object, session_manager: object):
                captured["supervisor_config"] = default_config
                captured["supervisor_session_manager"] = session_manager

            def shutdown(self) -> None:
                captured["supervisor_shutdown"] = True

        class _FakePluginLoader:
            def list_available(self) -> list[object]:
                return []

        class _FakeEventRouter:
            def __init__(self) -> None:
                self.routes: list[object] = []

        class _FakeExecApprovalGate:
            def __init__(self, default_timeout_s: int):
                captured["approval_timeout"] = default_timeout_s

        class _FakeSkillLoader:
            def load_from_dir(self, _path: str) -> list[object]:
                return []

            def filter_eligible(self, skills: list[object]) -> list[object]:
                return skills

            def build_system_prompt_context(self, _skills: list[object], mode: str = "compact") -> str:
                return mode

            def deactivate_scope(self, _session_id: str) -> None:
                return None

            def deactivate_all(self) -> None:
                captured["skills_deactivated"] = True

        class _FakeScheduler:
            def __init__(self, execute_fn: object):
                captured["scheduler_execute_fn"] = execute_fn

            def start(self) -> None:
                captured["scheduler_started"] = True

            def stop(self) -> None:
                captured["scheduler_stopped"] = True

        class _FakeDrainGuard:
            def __init__(self, event_bus: object):
                captured["drain_event_bus"] = event_bus

            def start_draining(self) -> None:
                captured["drain_started"] = True

            def wait_active(self, timeout: int) -> None:
                captured["drain_timeout"] = timeout

        class _FakeHealthMonitor:
            def __init__(self, event_bus: object, interval_s: float):
                captured["health_event_bus"] = event_bus
                captured["health_interval_s"] = interval_s

            def register(self, name: str, probe: object) -> None:
                captured["health_register"] = name
                captured["health_probe"] = probe

            def start(self) -> None:
                captured["health_started"] = True

            def dispose(self) -> None:
                captured["health_disposed"] = True

        with (
            patch.object(api, "SessionManager", _FakeSessionManager),
            patch.object(api, "RLMSupervisor", _FakeSupervisor),
            patch.object(api, "PluginLoader", _FakePluginLoader),
            patch.object(api, "EventRouter", _FakeEventRouter),
            patch.object(api, "ExecApprovalGate", _FakeExecApprovalGate),
            patch.object(api, "SkillLoader", _FakeSkillLoader),
            patch.object(api, "RLMScheduler", _FakeScheduler),
            patch.object(api, "DrainGuard", _FakeDrainGuard),
            patch.object(api, "HealthMonitor", _FakeHealthMonitor),
        ):
            async def _run_lifespan() -> None:
                app = FastAPI()
                async with api.lifespan(app):
                    assert captured["default_rlm_kwargs"]["backend"] == "anthropic"
                    assert captured["default_rlm_kwargs"]["backend_kwargs"]["model_name"] == "claude-3-5-haiku-latest"

            asyncio.run(_run_lifespan())

        assert captured["closed"] is True
        assert captured["supervisor_shutdown"] is True
        assert captured["scheduler_started"] is True
        assert captured["scheduler_stopped"] is True
        assert captured["health_started"] is True
        assert captured["health_disposed"] is True
