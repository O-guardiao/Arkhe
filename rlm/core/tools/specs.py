"""
ToolSpec — especificação formal de ferramentas.

Espelha o struct ToolSpec do claw-code (Rust), agora em Python para uso pelo Brain.
Definições correspondem ao schema schemas/tool-spec.v1.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# PermissionMode vive em engine.enums para evitar importação circular
# (permission_policy → tools → dispatcher → permission_policy).
# Re-exportado aqui para backward-compat de todo código que importa de specs.
from rlm.core.engine.enums import PermissionMode  # noqa: F401


class ToolLayer(str, Enum):
    """Camada de registro da ferramenta."""
    BUILTIN = "builtin"
    PLUGIN = "plugin"
    RUNTIME = "runtime"


@dataclass(frozen=True)
class ToolSpec:
    """Especificação de uma ferramenta do ecossistema RLM.

    Attributes:
        name: Identificador snake_case único (ex.: "bash", "read_file").
        description: Descrição legível do que a ferramenta faz.
        input_schema: JSON Schema (type:object) descrevendo os parâmetros.
        required_permission: Nível mínimo de permissão para executar.
        layer: Camada de origem da ferramenta.
        timeout_ms: Timeout máximo de execução em milissegundos (0 = sem limite).
    """
    name: str
    description: str
    input_schema: dict[str, Any]
    required_permission: PermissionMode = PermissionMode.READ_ONLY
    layer: ToolLayer = ToolLayer.BUILTIN
    timeout_ms: int = 0

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError(f"ToolSpec.name must be snake_case alphanumeric: {self.name!r}")
        if self.input_schema.get("type") != "object":
            raise ValueError("ToolSpec.input_schema must have type='object'")

    def to_llm_definition(self) -> dict[str, Any]:
        """Serializa como definição de função para a API do LLM (OpenAI-compat)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
