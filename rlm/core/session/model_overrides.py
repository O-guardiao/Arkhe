"""
model_overrides.py — Sobrescrita de modelo por sessão.

Porta fiel de packages/sessions/src/model-overrides.ts para Python.

Fornece:
- ``ModelOverride``     — dataclass com model, max_tokens, temperature, reason
- ``ModelOverrideMap``  — map in-memory O(1) de session_id → ModelOverride
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Type
# ---------------------------------------------------------------------------

@dataclass
class ModelOverride:
    """
    Configuração de modelo por sessão.

    Quando definida, o *model* nomeado é usado em vez do padrão do sistema
    para todos os turnos daquela sessão.

    Porta de ``ModelOverride`` em model-overrides.ts.
    """
    model: str
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    # Motivo legível armazenado para fins de auditoria
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------

class ModelOverrideMap:
    """
    Map in-memory de sobrescrita de modelo por sessão.

    Deve ser mantido como singleton dentro de um processo gateway/servidor.
    Todas as operações são O(1) e síncronas.

    Porta de ``ModelOverrideMap`` em model-overrides.ts.
    """

    def __init__(self) -> None:
        self._overrides: dict[str, ModelOverride] = {}

    def set(self, session_id: str, override: ModelOverride) -> None:
        """Registra ou substitui uma sobrescrita de modelo para *session_id*."""
        self._overrides[session_id] = override

    def get(self, session_id: str) -> ModelOverride | None:
        """Retorna a sobrescrita para *session_id*, ou ``None`` se não houver."""
        return self._overrides.get(session_id)

    def clear(self, session_id: str) -> None:
        """Remove qualquer sobrescrita de modelo para *session_id*."""
        self._overrides.pop(session_id, None)

    def get_or_default(self, session_id: str, default_model: str) -> str:
        """
        Retorna o nome do modelo para *session_id*.

        Usa *default_model* quando nenhuma sobrescrita está registrada.

        Porta de ``getOrDefault()`` em model-overrides.ts.
        """
        override = self._overrides.get(session_id)
        if override is not None:
            return override.model
        return default_model

    def size(self) -> int:
        """Número total de sobrescritas registradas."""
        return len(self._overrides)
