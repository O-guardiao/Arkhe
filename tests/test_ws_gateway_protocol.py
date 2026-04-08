from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from rlm.gateway.ws_gateway_endpoint import router


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


class TestWsGatewayProtocol:
    def test_hello_returns_hello_ack(self, monkeypatch) -> None:
        monkeypatch.delenv("RLM_GATEWAY_TOKEN", raising=False)
        monkeypatch.delenv("RLM_WS_TOKEN", raising=False)
        monkeypatch.setenv("RLM_VERSION", "test-version")

        app = _build_app()
        with TestClient(app) as client:
            with client.websocket_connect("/ws/gateway") as ws:
                ws.send_json(
                    {
                        "type": "hello",
                        "data": {
                            "schema_version": "1",
                            "client": "gateway-ts",
                            "client_version": "0.1.0",
                            "capabilities": ["envelope.v1", "ack.v1"],
                        },
                    }
                )

                reply = ws.receive_json()
                assert reply["type"] == "hello_ack"
                assert reply["data"]["accepted"] is True
                assert reply["data"]["schema_version"] == "1"
                assert reply["data"]["server"] == "brain-python"
                assert "envelope.v1" in reply["data"]["capabilities"]

    def test_invalid_hello_returns_protocol_error(self, monkeypatch) -> None:
        monkeypatch.delenv("RLM_GATEWAY_TOKEN", raising=False)
        monkeypatch.delenv("RLM_WS_TOKEN", raising=False)

        app = _build_app()
        with TestClient(app) as client:
            with client.websocket_connect("/ws/gateway") as ws:
                ws.send_json({"type": "hello", "data": {"client": "gateway-ts"}})
                reply = ws.receive_json()
                assert reply["type"] == "error"
                assert reply["code"] == "invalid_hello"

    def test_legacy_health_dot_report_is_accepted(self, monkeypatch) -> None:
        monkeypatch.delenv("RLM_GATEWAY_TOKEN", raising=False)
        monkeypatch.delenv("RLM_WS_TOKEN", raising=False)

        app = _build_app()
        with TestClient(app) as client:
            with client.websocket_connect("/ws/gateway") as ws:
                ws.send_json({"type": "health.report", "data": {"channels": {}}})
                ws.send_json(
                    {
                        "type": "hello",
                        "data": {
                            "schema_version": "1",
                            "client": "gateway-ts",
                        },
                    }
                )

                reply = ws.receive_json()
                assert reply["type"] == "hello_ack"

    def test_token_is_still_enforced(self, monkeypatch) -> None:
        monkeypatch.setenv("RLM_GATEWAY_TOKEN", "segredo")
        monkeypatch.delenv("RLM_WS_TOKEN", raising=False)

        app = _build_app()
        with TestClient(app) as client:
            try:
                with client.websocket_connect("/ws/gateway"):
                    raise AssertionError("Conexão sem token não deveria ser aceita")
            except WebSocketDisconnect as exc:
                assert exc.code == 4401

        monkeypatch.delenv("RLM_GATEWAY_TOKEN", raising=False)

    def test_invalid_envelope_extra_property_returns_protocol_error(self, monkeypatch) -> None:
        monkeypatch.delenv("RLM_GATEWAY_TOKEN", raising=False)
        monkeypatch.delenv("RLM_WS_TOKEN", raising=False)

        app = _build_app()
        with TestClient(app) as client:
            with client.websocket_connect("/ws/gateway") as ws:
                ws.send_json(
                    {
                        "type": "envelope",
                        "data": {
                            "id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
                            "source_channel": "telegram",
                            "source_id": "123456789",
                            "source_client_id": "telegram:123456789",
                            "direction": "inbound",
                            "message_type": "text",
                            "text": "oi",
                            "timestamp": "2025-01-15T10:30:00.000Z",
                            "unexpected": True,
                        },
                    }
                )

                reply = ws.receive_json()
                assert reply["type"] == "error"
                assert reply["code"] == "invalid_envelope"