"""
ExecutionFence — barreira de segurança de execução.

Envolve ToolDispatcher + PermissionPolicy em uma única interface limpa.
É a última linha de defesa antes de qualquer ferramenta ser executada.

Responsabilidades:
  - authorize(): consulta apenas a política, sem executar
  - enforce(): como authorize, mas lança PermissionDeniedError se negado
  - execute(): autoriza + executa numa chamada só
"""

from __future__ import annotations

import logging
from typing import Any

from rlm.core.engine.permission_policy import PermissionPolicy, PolicyAction
from rlm.core.tools.dispatcher import DispatchResult, ToolDispatcher
from rlm.core.tools.registry import get_registry
from rlm.core.tools.specs import PermissionMode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Excepção
# ---------------------------------------------------------------------------

class PermissionDeniedError(PermissionError):
    """Ferramenta bloqueada pela política de segurança."""

    def __init__(self, tool_name: str, action: PolicyAction) -> None:
        self.tool_name = tool_name
        self.action = action
        super().__init__(
            f"Ferramenta '{tool_name}' bloqueada: {action.value}"
        )


class ApprovalRequiredError(Exception):
    """Ferramenta requer aprovação explícita do usuário antes de prosseguir."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(
            f"Ferramenta '{tool_name}' aguardando aprovação do usuário."
        )


# ---------------------------------------------------------------------------
# ExecutionFence
# ---------------------------------------------------------------------------

class ExecutionFence:
    """Camada de segurança que une política de permissão e execução de ferramentas.

    Args:
        policy: PermissionPolicy. Se None, usa PermissionPolicy.default().
        dispatcher: ToolDispatcher. Se None, cria um com o registry singleton.
    """

    def __init__(
        self,
        policy: PermissionPolicy | None = None,
        dispatcher: ToolDispatcher | None = None,
    ) -> None:
        self._policy = policy or PermissionPolicy.default()
        self._dispatcher = dispatcher or ToolDispatcher(
            registry=get_registry(),
            policy=self._policy,
        )

    # ------------------------------------------------------------------
    # Autorização simples
    # ------------------------------------------------------------------

    def authorize(
        self,
        tool_name: str,
        required_permission: PermissionMode,
    ) -> PolicyAction:
        """Consulta a política sem executar nada."""
        return self._policy.authorize(tool_name, required_permission)

    def is_allowed(self, tool_name: str, required_permission: PermissionMode) -> bool:
        """Atalho booleano para authorize."""
        return self._policy.is_allowed(tool_name, required_permission)

    # ------------------------------------------------------------------
    # Autorização com exceção
    # ------------------------------------------------------------------

    def enforce(
        self,
        tool_name: str,
        required_permission: PermissionMode,
    ) -> None:
        """Como authorize, mas lança exceção se não for Allow/Audit.

        Raises:
            PermissionDeniedError: Quando a política retorna Deny.
            ApprovalRequiredError: Quando a política retorna RequireApproval.
        """
        action = self._policy.authorize(tool_name, required_permission)
        match action:
            case PolicyAction.DENY:
                raise PermissionDeniedError(tool_name, action)
            case PolicyAction.REQUIRE_APPROVAL:
                raise ApprovalRequiredError(tool_name)
            case _:
                pass  # Allow | Audit — continua

    # ------------------------------------------------------------------
    # Execução completa
    # ------------------------------------------------------------------

    async def execute(
        self,
        tool_name: str,
        inputs: dict[str, Any],
        actor: str = "system",
    ) -> DispatchResult:
        """Autoriza + executa ferramenta de forma assíncrona.

        Delega inteiramente ao ToolDispatcher (que já consulta a política).
        Este método existe para conveniência e clareza semântica.
        """
        return await self._dispatcher.dispatch(tool_name, inputs, actor)

    def execute_sync(
        self,
        tool_name: str,
        inputs: dict[str, Any],
        actor: str = "system",
    ) -> DispatchResult:
        """Versão síncrona de execute."""
        return self._dispatcher.dispatch_sync(tool_name, inputs, actor)

    # ------------------------------------------------------------------
    # Acesso a componentes internos
    # ------------------------------------------------------------------

    @property
    def policy(self) -> PermissionPolicy:
        return self._policy

    @policy.setter
    def policy(self, value: PermissionPolicy) -> None:
        self._policy = value
        # Atualiza reference no dispatcher também
        self._dispatcher = ToolDispatcher(
            registry=self._dispatcher._registry,
            policy=value,
            audit_callback=self._dispatcher._audit_callback,
        )
