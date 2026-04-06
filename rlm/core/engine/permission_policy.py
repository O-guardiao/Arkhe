"""
PermissionPolicy — motor de autorização baseado em PolicyRules.

Espelha o sistema PolicyRule/PolicyCondition/PolicyAction do claw-code.
Carrega configuração a partir de JSON (schemas/permission-policy.v1.json).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from rlm.core.tools.specs import PermissionMode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums de política
# ---------------------------------------------------------------------------

class PolicyAction(str, Enum):
    ALLOW = "Allow"
    DENY = "Deny"
    REQUIRE_APPROVAL = "RequireApproval"
    AUDIT = "Audit"


@dataclass(frozen=True)
class PolicyRule:
    """Uma regra de política — espelha claw-code PolicyRule.

    Attributes:
        name: Identificador legível da regra.
        condition_type: Tipo da condição: "tool_name", "tool_pattern", "permission", "always".
        condition_value: Valor da condição (nome de ferramenta, regex, ou PermissionMode).
        action: Ação a tomar quando a condição é satisfeita.
        priority: Regras com maior priority são avaliadas primeiro.
    """
    name: str
    condition_type: str
    condition_value: str
    action: PolicyAction
    priority: int = 0

    def matches(self, tool_name: str, required_permission: PermissionMode) -> bool:
        """Avalia se esta regra se aplica ao contexto dado."""
        match self.condition_type:
            case "tool_name":
                return tool_name == self.condition_value
            case "tool_pattern":
                return bool(re.match(self.condition_value, tool_name))
            case "permission":
                return required_permission.value == self.condition_value
            case "always":
                return True
            case _:
                logger.warning("Unknown condition_type: %s", self.condition_type)
                return False


# ---------------------------------------------------------------------------
# PermissionPolicy
# ---------------------------------------------------------------------------

class PermissionPolicy:
    """Motor de avaliação de políticas.

    Avalia regras em ordem decrescente de prioridade.
    A primeira regra que satisfaz a condição determina a ação.
    Se nenhuma regra se aplica, usa default_mode.

    Exemplo de uso:
        policy = PermissionPolicy.default()
        action = policy.authorize("bash", PermissionMode.DANGER_FULL_ACCESS)
        if action == PolicyAction.DENY:
            raise PermissionError("bash não autorizado")
    """

    def __init__(
        self,
        rules: list[PolicyRule],
        default_mode: PolicyAction = PolicyAction.ALLOW,
        allowed_tools: list[str] | None = None,
        blocked_tools: list[str] | None = None,
    ) -> None:
        self._rules = sorted(rules, key=lambda r: r.priority, reverse=True)
        self._default = default_mode
        self._allowed = set(allowed_tools or [])
        self._blocked = set(blocked_tools or [])

    # ------------------------------------------------------------------
    # Autorização
    # ------------------------------------------------------------------

    def authorize(self, tool_name: str, required_permission: PermissionMode) -> PolicyAction:
        """Determina a ação para dado tool_name + permission.

        Ordem de avaliação:
          1. Lista de ferramentas bloqueadas explicitamente → Deny
          2. Lista de ferramentas permitidas explicitamente → Allow
          3. Regras em ordem de prioridade (primeira que bate ganha)
          4. Modo padrão
        """
        if tool_name in self._blocked:
            return PolicyAction.DENY

        if self._allowed and tool_name in self._allowed:
            return PolicyAction.ALLOW

        for rule in self._rules:
            if rule.matches(tool_name, required_permission):
                logger.debug(
                    "PolicyRule %s matched for %s → %s",
                    rule.name,
                    tool_name,
                    rule.action.value,
                )
                return rule.action

        return self._default

    def is_allowed(self, tool_name: str, required_permission: PermissionMode) -> bool:
        """Atalho: retorna True se a ação for Allow ou Audit."""
        action = self.authorize(tool_name, required_permission)
        return action in (PolicyAction.ALLOW, PolicyAction.AUDIT)

    # ------------------------------------------------------------------
    # Construtores
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> "PermissionPolicy":
        """Política padrão: permite tudo exceto DangerFullAccess que requer aprovação."""
        return cls(
            rules=[
                PolicyRule(
                    name="require_approval_for_danger",
                    condition_type="permission",
                    condition_value=PermissionMode.DANGER_FULL_ACCESS.value,
                    action=PolicyAction.REQUIRE_APPROVAL,
                    priority=10,
                ),
            ],
            default_mode=PolicyAction.ALLOW,
        )

    @classmethod
    def from_file(cls, path: Path) -> "PermissionPolicy":
        """Carrega política a partir de arquivo JSON (schemas/permission-policy.v1.json)."""
        with path.open(encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PermissionPolicy":
        """Cria política a partir de dict (já parseado do JSON)."""
        raw_rules: list[dict[str, Any]] = data.get("rules", [])
        rules: list[PolicyRule] = []

        for r in raw_rules:
            condition = r.get("condition", {})
            ctype = next(iter(condition), "always")
            cvalue = condition.get(ctype, "")

            rules.append(
                PolicyRule(
                    name=r.get("name", "unnamed"),
                    condition_type=ctype,
                    condition_value=str(cvalue),
                    action=PolicyAction(r.get("action", "Allow")),
                    priority=int(r.get("priority", 0)),
                )
            )

        default_str = data.get("default_mode", "Allow")
        default_mode = PolicyAction(default_str)

        return cls(
            rules=rules,
            default_mode=default_mode,
            allowed_tools=data.get("allowed_tools"),
            blocked_tools=data.get("blocked_tools"),
        )
