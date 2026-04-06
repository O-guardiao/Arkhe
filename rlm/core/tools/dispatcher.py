"""
ToolDispatcher — executa ferramentas com verificação de permissão e auditoria.

Responsabilidades:
  1. Verificar PermissionPolicy antes de executar
  2. Delegar ao ToolRegistry para execução
  3. Registrar resultado em log de auditoria
  4. Retornar DispatchResult estruturado (nunca lança excepções ao caller)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from rlm.core.engine.permission_policy import PermissionPolicy, PolicyAction
from rlm.core.tools.registry import ToolRegistry, get_registry
from rlm.core.tools.specs import PermissionMode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DispatchResult
# ---------------------------------------------------------------------------

@dataclass
class DispatchResult:
    """Resultado imutável de uma chamada de ferramenta."""

    success: bool
    tool_name: str
    elapsed_ms: float = 0.0
    result: Any = None
    error: str | None = None
    denied: bool = False
    requires_approval: bool = False
    audit_entry: dict[str, Any] = field(default_factory=dict)

    def is_denied(self) -> bool:
        return self.denied or not self.success and self.error == "access_denied"

    def as_text(self) -> str:
        """Representação textual adequada para o LLM consumir."""
        if self.denied:
            return f"[DENIED] Ferramenta '{self.tool_name}' bloqueada pela política de segurança."
        if self.requires_approval:
            return f"[APPROVAL_REQUIRED] Ferramenta '{self.tool_name}' requer aprovação do usuário."
        if not self.success:
            return f"[ERROR] {self.error or 'erro desconhecido'}"
        if isinstance(self.result, str):
            return self.result
        if self.result is None:
            return ""
        import json as _json
        return _json.dumps(self.result, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# ToolDispatcher
# ---------------------------------------------------------------------------

class ToolDispatcher:
    """Despacha chamadas de ferramentas passando por permissão e auditoria.

    Args:
        registry: ToolRegistry; se None, usa o singleton via get_registry().
        policy: PermissionPolicy; se None, usa PermissionPolicy.default().
        audit_callback: Callable[dict] opcional — chamado após cada dispatch
                        com o dict de auditoria (útil para integrar AuditChain).
    """

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        policy: PermissionPolicy | None = None,
        audit_callback: Any | None = None,
    ) -> None:
        self._registry = registry or get_registry()
        self._policy = policy or PermissionPolicy.default()
        self._audit_callback = audit_callback

    # ------------------------------------------------------------------
    # Dispatch assíncrono (interface principal)
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        name: str,
        inputs: dict[str, Any],
        actor: str = "system",
    ) -> DispatchResult:
        """Despacha ferramenta de forma assíncrona.

        Fluxo:
          1. Busca ToolSpec — falha se não encontrada
          2. Avalia PermissionPolicy
          3. Executa handler (suporte a sync e async)
          4. Captura qualquer excepção → error no DispatchResult
          5. Dispara audit_callback se configurado
        """
        t0 = time.monotonic()

        item = self._registry.get(name)
        if item is None:
            result = DispatchResult(
                success=False,
                tool_name=name,
                error=f"ferramenta '{name}' não encontrada",
                elapsed_ms=0.0,
            )
            await self._audit(actor, name, result)
            return result

        spec, handler = item
        action = self._policy.authorize(name, spec.required_permission)

        if action == PolicyAction.DENY:
            result = DispatchResult(
                success=False,
                tool_name=name,
                denied=True,
                error="access_denied",
                elapsed_ms=_elapsed_ms(t0),
            )
            await self._audit(actor, name, result)
            return result

        if action == PolicyAction.REQUIRE_APPROVAL:
            result = DispatchResult(
                success=False,
                tool_name=name,
                requires_approval=True,
                error="requires_approval",
                elapsed_ms=_elapsed_ms(t0),
            )
            await self._audit(actor, name, result)
            return result

        # Executar
        try:
            timeout_s = spec.timeout_ms / 1000.0 if spec.timeout_ms > 0 else None

            if inspect.iscoroutinefunction(handler):
                coro = handler(inputs)
                output = await (asyncio.wait_for(coro, timeout_s) if timeout_s else coro)
            else:
                loop = asyncio.get_event_loop()
                if timeout_s:
                    fut = loop.run_in_executor(None, handler, inputs)
                    output = await asyncio.wait_for(fut, timeout_s)
                else:
                    output = await loop.run_in_executor(None, handler, inputs)

            result = DispatchResult(
                success=True,
                tool_name=name,
                result=output,
                elapsed_ms=_elapsed_ms(t0),
            )

        except TimeoutError:
            result = DispatchResult(
                success=False,
                tool_name=name,
                error=f"timeout após {spec.timeout_ms}ms",
                elapsed_ms=_elapsed_ms(t0),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro executando ferramenta %s", name)
            result = DispatchResult(
                success=False,
                tool_name=name,
                error=str(exc),
                elapsed_ms=_elapsed_ms(t0),
            )

        await self._audit(actor, name, result)
        return result

    # ------------------------------------------------------------------
    # Dispatch síncrono (para contextos sem event loop)
    # ------------------------------------------------------------------

    def dispatch_sync(
        self,
        name: str,
        inputs: dict[str, Any],
        actor: str = "system",
    ) -> DispatchResult:
        """Versão síncrona de dispatch. Cria event loop temporário se necessário."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Dentro de um event loop existente: usa thread separada
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(asyncio.run, self.dispatch(name, inputs, actor))
                    return fut.result()
            return loop.run_until_complete(self.dispatch(name, inputs, actor))
        except RuntimeError:
            return asyncio.run(self.dispatch(name, inputs, actor))

    # ------------------------------------------------------------------
    # Auditoria interna
    # ------------------------------------------------------------------

    async def _audit(self, actor: str, tool_name: str, result: DispatchResult) -> None:
        entry: dict[str, Any] = {
            "actor": actor,
            "tool": tool_name,
            "success": result.success,
            "elapsed_ms": result.elapsed_ms,
            "denied": result.denied,
            "error": result.error,
        }
        result.audit_entry.update(entry)

        if self._audit_callback is not None:
            try:
                if inspect.iscoroutinefunction(self._audit_callback):
                    await self._audit_callback(entry)
                else:
                    self._audit_callback(entry)
            except Exception:  # noqa: BLE001
                logger.warning("audit_callback falhou silenciosamente", exc_info=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _elapsed_ms(t0: float) -> float:
    return round((time.monotonic() - t0) * 1000, 2)
