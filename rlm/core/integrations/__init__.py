"""Superfície pública das integrações externas.

Este pacote agrega integrações opcionais que não devem poluir o import de
``rlm.core`` com detalhes de implementação:
- clientes MCP síncronos sobre transports stdio e HTTP/SSE;
- ponte event-driven entre knowledge base e vault Obsidian;
- utilitários legados de espelhamento/importação de notas no vault.

Os símbolos são resolvidos sob demanda para manter o pacote raiz estável e
evitar eager imports desnecessários no carregamento do core.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from rlm.core.integrations.mcp_client import (
		BaseSyncMCPClient,
		SyncMCPClient,
		SyncMCPHttpClient,
	)
	from rlm.core.integrations.obsidian_bridge import ObsidianBridge
	from rlm.core.integrations.obsidian_mirror import (
		export_all_to_vault,
		export_document_to_vault,
		import_conceitos_from_vault,
	)

_LAZY_MODULES: dict[str, str] = {
	"mcp_client": "rlm.core.integrations.mcp_client",
	"obsidian_bridge": "rlm.core.integrations.obsidian_bridge",
	"obsidian_mirror": "rlm.core.integrations.obsidian_mirror",
}

_LAZY_ATTRS: dict[str, str] = {
	"BaseSyncMCPClient": "rlm.core.integrations.mcp_client",
	"SyncMCPClient": "rlm.core.integrations.mcp_client",
	"SyncMCPHttpClient": "rlm.core.integrations.mcp_client",
	"ObsidianBridge": "rlm.core.integrations.obsidian_bridge",
	"export_document_to_vault": "rlm.core.integrations.obsidian_mirror",
	"export_all_to_vault": "rlm.core.integrations.obsidian_mirror",
	"import_conceitos_from_vault": "rlm.core.integrations.obsidian_mirror",
}

__all__ = [
	"BaseSyncMCPClient",
	"SyncMCPClient",
	"SyncMCPHttpClient",
	"ObsidianBridge",
	"export_document_to_vault",
	"export_all_to_vault",
	"import_conceitos_from_vault",
]


def __getattr__(name: str):
	if name in _LAZY_MODULES:
		module = importlib.import_module(_LAZY_MODULES[name])
		globals()[name] = module
		return module
	if name in _LAZY_ATTRS:
		module = importlib.import_module(_LAZY_ATTRS[name])
		value = getattr(module, name)
		globals()[name] = value
		return value
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
	return sorted(set(globals()) | set(__all__) | set(_LAZY_MODULES))
