"""tui/ — subpacote do workbench TUI Rich do Arkhe.

Re-exporta a API pública para que ``from rlm.cli.tui import X`` funcione.
"""

from rlm.cli.tui.runtime_factory import (
    WorkbenchRuntime as WorkbenchRuntime,
    build_local_workbench_runtime as build_local_workbench_runtime,
)
from rlm.cli.commands.workbench import (
    RuntimeWorkbench as RuntimeWorkbench,
    run_workbench as run_workbench,
)
