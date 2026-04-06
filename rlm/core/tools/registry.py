"""
ToolRegistry — registro global de ferramentas em 3 camadas.

Espelha GlobalToolRegistry do claw-code com padrão OnceLock:
  - builtins: ferramentas internas carregadas na inicialização
  - plugins:  ferramentas de plugins externos
  - runtime:  ferramentas registradas dinamicamente em tempo de execução

Ordem de resolução: runtime → plugins → builtins (última camada ganha).
"""

from __future__ import annotations

import threading
import logging
from typing import Any, Callable, Iterable

from rlm.core.tools.specs import PermissionMode, ToolLayer, ToolSpec

logger = logging.getLogger(__name__)

# Callable que executa a ferramenta: (input_dict) -> str | dict
ToolHandler = Callable[[dict[str, Any]], Any]


class ToolRegistry:
    """Registro thread-safe de ferramentas em 3 camadas.

    Uso típico (singleton via get_registry()):

        registry = get_registry()
        registry.register_builtin(spec, handler)
        result = registry.execute("bash", {"command": "ls"})
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Dicionários por camada: nome → (ToolSpec, ToolHandler)
        self._builtins: dict[str, tuple[ToolSpec, ToolHandler]] = {}
        self._plugins: dict[str, tuple[ToolSpec, ToolHandler]] = {}
        self._runtime: dict[str, tuple[ToolSpec, ToolHandler]] = {}

    # ------------------------------------------------------------------
    # Registro
    # ------------------------------------------------------------------

    def register_builtin(self, spec: ToolSpec, handler: ToolHandler) -> None:
        """Registra ferramenta na camada builtin."""
        self._register(self._builtins, spec, handler, ToolLayer.BUILTIN)

    def register_plugin(self, spec: ToolSpec, handler: ToolHandler) -> None:
        """Registra ferramenta como plugin externo."""
        self._register(self._plugins, spec, handler, ToolLayer.PLUGIN)

    def register_runtime(self, spec: ToolSpec, handler: ToolHandler) -> None:
        """Registra ferramenta em tempo de execução (maior prioridade)."""
        self._register(self._runtime, spec, handler, ToolLayer.RUNTIME)

    def unregister_runtime(self, name: str) -> bool:
        """Remove ferramenta runtime. Retorna True se foi removida."""
        with self._lock:
            return self._runtime.pop(name, None) is not None

    def _register(
        self,
        layer: dict[str, tuple[ToolSpec, ToolHandler]],
        spec: ToolSpec,
        handler: ToolHandler,
        layer_name: ToolLayer,
    ) -> None:
        with self._lock:
            if spec.name in layer:
                logger.warning("Overwriting tool %s in layer %s", spec.name, layer_name.value)
            layer[spec.name] = (spec, handler)
            logger.debug("Registered tool %s in layer %s", spec.name, layer_name.value)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> tuple[ToolSpec, ToolHandler] | None:
        """Resolve ferramenta por nome (runtime → plugins → builtins)."""
        with self._lock:
            return (
                self._runtime.get(name)
                or self._plugins.get(name)
                or self._builtins.get(name)
            )

    def get_spec(self, name: str) -> ToolSpec | None:
        """Retorna apenas o ToolSpec, sem o handler."""
        result = self.get(name)
        return result[0] if result else None

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    # ------------------------------------------------------------------
    # Listagem
    # ------------------------------------------------------------------

    def all_specs(self) -> list[ToolSpec]:
        """Retorna todos os specs sem duplicatas (runtime > plugins > builtins)."""
        with self._lock:
            seen: set[str] = set()
            specs: list[ToolSpec] = []
            for layer in (self._runtime, self._plugins, self._builtins):
                for name, (spec, _) in layer.items():
                    if name not in seen:
                        specs.append(spec)
                        seen.add(name)
            return specs

    def llm_definitions(self) -> list[dict[str, Any]]:
        """Retorna definições de ferramentas no formato OpenAI functions."""
        return [spec.to_llm_definition() for spec in self.all_specs()]

    def count(self) -> dict[str, int]:
        with self._lock:
            return {
                "builtins": len(self._builtins),
                "plugins": len(self._plugins),
                "runtime": len(self._runtime),
            }

    # ------------------------------------------------------------------
    # Execução direta (sem verificação de permissão — use dispatcher)
    # ------------------------------------------------------------------

    def execute(self, name: str, inputs: dict[str, Any]) -> Any:
        """Executa ferramenta pelo nome. Lança KeyError se não encontrada."""
        result = self.get(name)
        if result is None:
            raise KeyError(f"Tool not found: {name!r}")
        _, handler = result
        return handler(inputs)


# ---------------------------------------------------------------------------
# Singleton global (padrão OnceLock do claw-code)
# ---------------------------------------------------------------------------

_REGISTRY: ToolRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def get_registry() -> ToolRegistry:
    """Retorna o registro global de ferramentas (inicializado uma vez)."""
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = ToolRegistry()
                logger.debug("Global ToolRegistry initialized")
    return _REGISTRY


def reset_registry_for_testing() -> None:
    """Reseta o registro global — USE APENAS EM TESTES."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = None
