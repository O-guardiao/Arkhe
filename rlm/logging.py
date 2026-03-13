"""
Fachada explícita para os três tipos de logging do projeto.

Responsabilidades:

1. TrajectoryLogger
   Persistência rica da execução do agente em JSONL por iteração.

2. RuntimeLogger
   Eventos operacionais curtos por subsistema para diagnóstico de runtime.

3. VerbosePrinter
   Observação humana da execução no terminal, com Rich quando disponível.

Recomendação de uso:

- Código novo de runtime, servidores, gateways, plugins e infraestrutura:
  use ``get_runtime_logger`` ou ``RuntimeLogger``.
- Código que precisa salvar trajetória completa do agente:
  use ``TrajectoryLogger``.
- Código que só quer renderização humana no terminal:
  use ``VerbosePrinter``.
"""

from rlm.core.structured_log import RuntimeLogger, get_runtime_logger
from rlm.logger import RLMLogger as TrajectoryLogger
from rlm.logger import VerbosePrinter

__all__ = [
    "RuntimeLogger",
    "TrajectoryLogger",
    "VerbosePrinter",
    "get_runtime_logger",
]