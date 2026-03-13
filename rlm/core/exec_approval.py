"""
RLM Exec Approval Gate — Fase 9.2

Gate de segurança para execução de código no REPL Python.

Análogo ao ExecApprovalManager do OpenClaw, mas adaptado para:
- RLM usa asyncio+FastAPI (sem WebSocket de controle)
- REPL é Python direto (não bash via shell tool)
- Gate é opcional (RLM_EXEC_APPROVAL_REQUIRED=true)

Fluxo:
    LLM decide executar algo destrutivo no REPL
           ↓
    confirm_exec("rm -rf data/") no REPL   ← função injetada
           ↓
    ExecApprovalGate.request() → threading.Event.wait(timeout)
           ↓
    Humano chama POST /exec/approve/{id} ou POST /exec/deny/{id}
           ↓
    confirm_exec retorna True (aprovado) ou lança PermissionError (negado)

Uso no REPL (injeta automaticamente via api.py):
    # LLM antes de operação destrutiva:
    confirm_exec("delete all records from orders table")
    # ... código destrutivo aqui ...
    
    # Ou no modo seguro:
    if confirm_exec("write to production DB"):
        connection.execute(sql)
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from rlm.core.structured_log import get_logger

approval_log = get_logger("exec_approval")

DEFAULT_APPROVAL_TIMEOUT_S = 60
RESOLVED_ENTRY_GRACE_S = 15  # manter entradas resolvidas por N segundos


# ---------------------------------------------------------------------------
# ApprovalRecord
# ---------------------------------------------------------------------------

@dataclass
class ApprovalRecord:
    """Estado de uma solicitação de aprovação pendente."""

    id: str
    session_id: str
    description: str                      # descrição legível do que será executado
    code_preview: str                     # primeiros 200 chars do código (opcional)
    created_at: float = field(default_factory=time.monotonic)
    expires_at: float = 0.0
    status: Literal["pending", "approved", "denied", "expired"] = "pending"
    resolved_at: float | None = None
    resolved_by: str | None = None        # "human" | "auto" | sistema

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.created_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "description": self.description,
            "code_preview": self.code_preview,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "age_seconds": round(self.age_seconds, 1),
            "resolved_by": self.resolved_by,
        }


# ---------------------------------------------------------------------------
# ExecApprovalGate
# ---------------------------------------------------------------------------

class ExecApprovalGate:
    """
    Gate de aprovação para execução de código no REPL.

    Thread-safe: o REPL roda em thread separada do FastAPI.
    Usa threading.Event para bloquear o REPL até decisão humana.

    Usage:
        gate = ExecApprovalGate()

        # No REPL (via confirm_exec injetado):
        ok = gate.request("delete all orders", code="orders.clear()")
        # → bloqueia até POST /exec/approve/{id} ou timeout

        # No handler HTTP:
        gate.approve(id, resolved_by="operator@example.com")
        gate.deny(id, resolved_by="operator@example.com")
    """

    def __init__(self, default_timeout_s: float = DEFAULT_APPROVAL_TIMEOUT_S):
        self._default_timeout_s = default_timeout_s
        self._pending: dict[str, tuple[ApprovalRecord, threading.Event]] = {}
        self._resolved: dict[str, ApprovalRecord] = {}
        self._lock = threading.Lock()

    # --- Public API ---

    def request(
        self,
        description: str,
        code: str = "",
        session_id: str = "unknown",
        timeout_s: float | None = None,
        request_id: str | None = None,
    ) -> bool:
        """
        Bloqueia o REPL até aprovação humana (ou timeout/negação).

        Returns True se aprovado.
        Raises PermissionError se negado.
        Raises TimeoutError se expirou sem decisão.
        """
        timeout = timeout_s if timeout_s is not None else self._default_timeout_s
        rec_id = request_id or str(uuid.uuid4())[:8]
        now = time.monotonic()

        record = ApprovalRecord(
            id=rec_id,
            session_id=session_id,
            description=description,
            code_preview=code[:200],
            created_at=now,
            expires_at=now + timeout,
        )
        event = threading.Event()

        with self._lock:
            self._pending[rec_id] = (record, event)

        approval_log.info(
            f"[exec_approval] PENDING id={rec_id} session={session_id} "
            f"desc={description!r} timeout={timeout}s"
        )

        # Bloqueia o REPL até decisão ou timeout
        resolved_in_time = event.wait(timeout=timeout)

        with self._lock:
            entry = self._pending.pop(rec_id, None)
            if entry:
                rec = entry[0]
                if not resolved_in_time:
                    rec.status = "expired"
                    rec.resolved_at = time.monotonic()
                self._resolved[rec_id] = rec
                self._prune_resolved()
                record = rec

        if record.status == "approved":
            approval_log.info(f"[exec_approval] APPROVED id={rec_id} by={record.resolved_by}")
            return True
        elif record.status == "denied":
            approval_log.warn(f"[exec_approval] DENIED id={rec_id} by={record.resolved_by}")
            raise PermissionError(
                f"Exec denied by {record.resolved_by!r}: {description!r}"
            )
        else:  # expired
            approval_log.warn(f"[exec_approval] EXPIRED id={rec_id} after {timeout}s")
            raise TimeoutError(
                f"Exec approval timed out after {timeout}s: {description!r}"
            )

    def approve(self, request_id: str, resolved_by: str = "human") -> bool:
        """Aprova uma solicitação pendente. Retorna True se encontrada."""
        with self._lock:
            entry = self._pending.get(request_id)
            if not entry:
                return False
            rec, event = entry
            rec.status = "approved"
            rec.resolved_at = time.monotonic()
            rec.resolved_by = resolved_by
            event.set()
            return True

    def deny(self, request_id: str, resolved_by: str = "human") -> bool:
        """Nega uma solicitação pendente. Retorna True se encontrada."""
        with self._lock:
            entry = self._pending.get(request_id)
            if not entry:
                return False
            rec, event = entry
            rec.status = "denied"
            rec.resolved_at = time.monotonic()
            rec.resolved_by = resolved_by
            event.set()
            return True

    def list_pending(self) -> list[dict]:
        """Retorna todas as solicitações pendentes (para GET /exec/pending)."""
        with self._lock:
            return [rec.to_dict() for rec, _ in self._pending.values()]

    def get_record(self, request_id: str) -> dict | None:
        """Retorna um record pelo ID (pending ou resolved)."""
        with self._lock:
            entry = self._pending.get(request_id)
            if entry:
                return entry[0].to_dict()
            rec = self._resolved.get(request_id)
            return rec.to_dict() if rec else None

    def stats(self) -> dict:
        with self._lock:
            return {
                "pending": len(self._pending),
                "resolved_cached": len(self._resolved),
                "default_timeout_s": self._default_timeout_s,
            }

    # --- Internal ---

    def _prune_resolved(self) -> None:
        """Remove entradas resolvidas antigas (chamado com lock held)."""
        cutoff = time.monotonic() - RESOLVED_ENTRY_GRACE_S
        stale = [
            k for k, v in self._resolved.items()
            if (v.resolved_at or 0) < cutoff
        ]
        for k in stale:
            del self._resolved[k]

    def make_repl_fn(self, session_id: str):
        """
        Retorna uma função `confirm_exec(description, code='')` pronta
        para injetar no REPL locals de uma sessão específica.

        O LLM chama esta função antes de código destrutivo:
            confirm_exec("apagar todos os registros de pedidos")
        """
        gate = self

        def confirm_exec(description: str, code: str = "") -> bool:
            """
            Solicita aprovação humana antes de executar uma operação.

            Args:
                description: Descreva o que o código irá fazer (legível).
                code:         Trecho do código a ser executado (opcional, para contexto).

            Returns True se aprovado. Lança PermissionError se negado ou
            TimeoutError se nenhuma resposta em 60 segundos.

            Exemplo:
                confirm_exec("deletar todos os registros de pedidos cancelados")
                orders.delete_where(status="cancelled")
            """
            return gate.request(description=description, code=code, session_id=session_id)

        return confirm_exec
