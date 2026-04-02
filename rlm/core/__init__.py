
# Subpackages — importados aqui para que mock.patch("rlm.core.<pkg>.<mod>.X")
# funcione mesmo em workers xdist que ainda não importaram o subpacote.
from rlm.core import (  # noqa: F401
    comms,
    engine,
    integrations,
    lifecycle,
    memory,
    observability,
    optimized,
    orchestration,
    security,
    session,
    skillkit,
)

from rlm.core.orchestration import mcts
from rlm.core.orchestration import role_orchestrator

__all__ = ["mcts", "role_orchestrator"]
