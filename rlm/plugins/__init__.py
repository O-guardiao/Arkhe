"""
RLM Plugin System — Fase 7.3

Sistema de plugins Pythonicos para o RLM. Plugins são módulos Python
que expõem funções injetáveis no REPL do RLM.

A diferença fundamental vs OpenClaw:
- OpenClaw: 27K LOC de plugin loader + schema validation + manifest registry
- RLM: ~150 LOC porque plugins são imports Python normais no REPL

O RLM não precisa descrever ferramentas no System Prompt. Ele simplesmente
faz `from rlm.plugins.telegram import send_message` no código REPL e usa.
"""
import os
import importlib
import importlib.util
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any


# ---------------------------------------------------------------------------
# Plugin Manifest
# ---------------------------------------------------------------------------

@dataclass
class PluginManifest:
    """Descreve um plugin disponível."""
    name: str
    version: str = "1.0.0"
    description: str = ""
    functions: list[str] = field(default_factory=list)
    author: str = ""
    requires: list[str] = field(default_factory=list)  # pip packages required


# ---------------------------------------------------------------------------
# Plugin Loader
# ---------------------------------------------------------------------------

class PluginLoader:
    """
    Descobre, carrega e injeta plugins no REPL do RLM.
    
    Plugins são módulos Python em rlm/plugins/ que expõem:
    - MANIFEST: PluginManifest  (metadata do plugin)
    - Funções que o RLM pode chamar no REPL
    
    Usage:
        loader = PluginLoader()
        available = loader.list_available()
        loader.inject_into_repl("telegram", repl_locals)
        # Now the RLM can do: telegram.send_message(123, "Hi!")
    """

    def __init__(self, plugins_dir: str | None = None):
        if plugins_dir is None:
            # Default: rlm/plugins/ directory (same package)
            plugins_dir = os.path.dirname(os.path.abspath(__file__))
        self.plugins_dir = plugins_dir
        self._loaded: dict[str, ModuleType] = {}
        self._manifests: dict[str, PluginManifest] = {}

    def list_available(self) -> list[PluginManifest]:
        """
        Scan the plugins directory and return manifests of all available plugins.
        
        A valid plugin is a .py file in rlm/plugins/ that:
        - Is not __init__.py
        - Does not start with _
        """
        manifests = []
        if not os.path.isdir(self.plugins_dir):
            return manifests

        for fname in sorted(os.listdir(self.plugins_dir)):
            if not fname.endswith(".py"):
                continue
            if fname.startswith("_"):
                continue

            name = fname[:-3]  # Remove .py
            try:
                manifest = self._get_manifest(name)
                manifests.append(manifest)
            except Exception:
                # Plugin has errors, skip it in listing
                manifests.append(PluginManifest(
                    name=name,
                    description="(failed to load manifest)",
                ))
        return manifests

    def load(self, name: str) -> ModuleType:
        """
        Load a plugin by name. Returns the module.
        
        Raises:
            ImportError: If the plugin module cannot be found or loaded.
        """
        if name in self._loaded:
            return self._loaded[name]

        module_path = os.path.join(self.plugins_dir, f"{name}.py")
        if os.path.exists(module_path):
            # Load from file path
            spec = importlib.util.spec_from_file_location(
                f"rlm.plugins.{name}", module_path
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot create module spec for plugin '{name}'")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        else:
            # Try as a package import
            try:
                module = importlib.import_module(f"rlm.plugins.{name}")
            except ImportError:
                raise ImportError(
                    f"Plugin '{name}' not found. Looked in: {module_path}"
                )

        self._loaded[name] = module
        return module

    def inject_into_repl(self, name: str, repl_locals: dict) -> list[str]:
        """
        Load a plugin and inject its public functions into the REPL namespace.
        
        Returns list of injected function names.
        
        After injection, the RLM can call plugin functions directly:
            send_message(123, "Hello!")  # Instead of: telegram.send_message(...)
            
        Or access the module:
            telegram.send_message(123, "Hello!")
        """
        module = self.load(name)
        injected = []

        # Inject the module itself (e.g., `telegram`)
        repl_locals[name] = module
        injected.append(name)

        # Inject all public functions/classes from the module
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue
            if attr_name == "MANIFEST":
                continue  # Don't pollute namespace with metadata

            attr = getattr(module, attr_name)
            if callable(attr):
                repl_locals[attr_name] = attr
                injected.append(attr_name)

        return injected

    def inject_multiple(self, names: list[str], repl_locals: dict) -> dict[str, list[str]]:
        """
        Inject multiple plugins at once.
        Returns a dict of {plugin_name: [injected_functions]}.
        """
        results = {}
        for name in names:
            try:
                results[name] = self.inject_into_repl(name, repl_locals)
            except ImportError as e:
                results[name] = [f"ERROR: {e}"]
        return results

    def unload(self, name: str) -> bool:
        """Remove a loaded plugin from cache."""
        if name in self._loaded:
            del self._loaded[name]
            if name in self._manifests:
                del self._manifests[name]
            return True
        return False

    def _get_manifest(self, name: str) -> PluginManifest:
        """Get or create manifest for a plugin."""
        if name in self._manifests:
            return self._manifests[name]

        module = self.load(name)

        # Try to read MANIFEST from the module
        if hasattr(module, "MANIFEST") and isinstance(module.MANIFEST, PluginManifest):
            manifest = module.MANIFEST
        else:
            # Auto-generate manifest from module contents
            functions = [
                attr for attr in dir(module)
                if not attr.startswith("_")
                and attr != "MANIFEST"
                and callable(getattr(module, attr))
            ]
            manifest = PluginManifest(
                name=name,
                description=getattr(module, "__doc__", "") or "",
                functions=functions,
            )

        self._manifests[name] = manifest
        return manifest

    def manifest_to_dict(self, manifest: PluginManifest) -> dict:
        """Convert manifest to JSON-safe dict for API responses."""
        return {
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "functions": manifest.functions,
            "author": manifest.author,
            "requires": manifest.requires,
        }


def __getattr__(name: str):
    if name == "mcp":
        return importlib.import_module(".mcp", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
