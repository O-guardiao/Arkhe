"""Cliente HTTP do workbench vivo para o servidor Arkhe."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib import error as uerror
from urllib import request as urequest

from rlm.cli.context import CliContext


class LiveWorkbenchError(RuntimeError):
    """Erro operacional ao conversar com o backend vivo do workbench."""


def _internal_token(env: dict[str, str]) -> str:
    for name in ("RLM_INTERNAL_TOKEN", "RLM_WS_TOKEN", "RLM_API_TOKEN"):
        token = env.get(name, "").strip()
        if token:
            return token
    return ""


def _build_headers(env: dict[str, str]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = _internal_token(env)
    if token:
        headers["X-RLM-Token"] = token
    return headers


@dataclass(frozen=True, slots=True)
class LiveSessionInfo:
    session_id: str
    client_id: str
    status: str
    state_dir: str
    metadata: dict[str, Any] = field(default_factory=dict)


class LiveWorkbenchAPI:
    def __init__(self, context: CliContext) -> None:
        self._context = context
        self._base_url = context.env.get("RLM_INTERNAL_HOST", context.api_base_url()).rstrip("/")
        self._headers = _build_headers(dict(context.env))

    @property
    def base_url(self) -> str:
        return self._base_url

    def probe(self, *, timeout: int = 3) -> bool:
        """Verifica se o servidor vivo esta acessivel via /health."""
        url = f"{self._base_url}/health"
        req = urequest.Request(url, headers=self._headers, method="GET")
        try:
            with urequest.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def ensure_session(self, client_id: str) -> LiveSessionInfo:
        payload = self._request_json("POST", "/operator/session", {"client_id": client_id})
        return LiveSessionInfo(
            session_id=str(payload.get("session_id", "")),
            client_id=str(payload.get("client_id", client_id)),
            status=str(payload.get("status", "idle")),
            state_dir=str(payload.get("state_dir", "")),
            metadata=dict(payload.get("metadata") or {}),
        )

    def fetch_activity(self, session_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/operator/session/{session_id}/activity")

    def dispatch_prompt(self, session_id: str, client_id: str, text: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/operator/session/{session_id}/message",
            {"client_id": client_id, "text": text},
            timeout=15,
        )

    def apply_command(
        self,
        session_id: str,
        *,
        client_id: str,
        command_type: str,
        payload: dict[str, Any],
        branch_id: int | None,
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/operator/session/{session_id}/commands",
            {
                "client_id": client_id,
                "command_type": command_type,
                "payload": payload,
                "branch_id": branch_id,
            },
        )

    def _request_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: int = 10,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urequest.Request(url, data=data, headers=self._headers, method=method)
        try:
            with urequest.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except uerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LiveWorkbenchError(f"HTTP {exc.code} em {path}: {detail[:300]}") from exc
        except uerror.URLError as exc:
            raise LiveWorkbenchError(
                f"Servidor vivo indisponivel em {self._base_url}. Rode 'arkhe start' e tente novamente."
            ) from exc
        except Exception as exc:
            raise LiveWorkbenchError(f"Falha ao chamar backend vivo {path}: {exc}") from exc

        if not isinstance(payload, dict):
            raise LiveWorkbenchError(f"Resposta invalida do backend vivo em {path}")
        return payload

    # ── Channel status endpoints ──────────────────────────────────────

    def fetch_channels_status(self) -> dict[str, Any]:
        """GET /api/channels/status — retorna snapshot de todos os canais."""
        return self._request_json("GET", "/api/channels/status")

    def probe_channel(self, channel_id: str) -> dict[str, Any]:
        """POST /api/channels/{channel_id}/probe — probe sob demanda."""
        return self._request_json("POST", f"/api/channels/{channel_id}/probe")

    def cross_channel_send(self, target_client_id: str, message: str) -> dict[str, Any]:
        """POST /api/channels/send — envia mensagem cross-channel."""
        return self._request_json(
            "POST",
            "/api/channels/send",
            {"target_client_id": target_client_id, "message": message},
        )