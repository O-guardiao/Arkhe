"""
Enums compartilhados do motor RLM.

Módulo standalone sem dependências de projeto — importado livremente por
permission_policy, tools.specs e qualquer outro módulo sem risco de ciclo.
"""

from __future__ import annotations

from enum import Enum


class PermissionMode(str, Enum):
    """Nível de permissão exigido pela ferramenta.
    Mapeado 1:1 com claw-code PermissionMode.
    """
    READ_ONLY = "ReadOnly"
    WORKSPACE_WRITE = "WorkspaceWrite"
    DANGER_FULL_ACCESS = "DangerFullAccess"
    PROMPT = "Prompt"
    ALLOW = "Allow"


__all__ = ["PermissionMode"]
