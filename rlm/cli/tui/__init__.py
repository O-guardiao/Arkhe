"""tui/ — subpacote do workbench TUI Rich do Arkhe.

Re-exporta a API pública para que ``from rlm.cli.tui import X`` funcione.

RuntimeWorkbench e run_workbench vivem em rlm.cli.commands.workbench,
que importa deste pacote (runtime_factory).  Para evitar import circular
eles são carregados sob demanda via __getattr__.
"""

from rlm.cli.tui.runtime_factory import (
    WorkbenchRuntime as WorkbenchRuntime,
    build_local_workbench_runtime as build_local_workbench_runtime,
)

_LAZY = {
    "RuntimeWorkbench": "rlm.cli.commands.workbench",
    "run_workbench": "rlm.cli.commands.workbench",
}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib
        mod = importlib.import_module(_LAZY[name])
        value = getattr(mod, name)
        globals()[name] = value  # cache para chamadas futuras
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
