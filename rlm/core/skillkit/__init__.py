"""
Skill framework: loader, SIF injection, telemetry.

Módulos deste pacote
--------------------
skill_loader  — SkillLoader, SkillDef e dataclasses de metadados (sem efeitos colaterais).
sif           — SIFEntry, SIFFactory, SIFTableBuilder (importa skill_telemetry de forma lazy
                após este fix; antes disparava I/O de disco no import).
skill_telemetry — SkillTelemetryStore, get_skill_telemetry (singleton — lê disco uma vez
                  em _rehydrate_from_disk; importar este módulo sempre produz I/O).

Convenção de importação
-----------------------
Este __init__ exporta apenas os símbolos de ``skill_loader`` porque ele é o único
sub-módulo sem efeitos colaterais no nível de módulo.  Para acessar sif ou
skill_telemetry importe diretamente do submódulo:

    from rlm.core.skillkit.sif import SIFEntry, SIFFactory
    from rlm.core.skillkit.skill_telemetry import get_skill_telemetry
"""

from rlm.core.skillkit.skill_loader import (
    SkillAvailability,
    SkillDef,
    SkillLoader,
    SkillPromptPlan,
    SkillQualityMeta,
    SkillRank,
    SkillRetrievalMeta,
    SkillRuntimeMeta,
    _parse_skill_file,
)

__all__ = [
    # Dataclasses de metadados
    "SkillDef",
    "SkillRank",
    "SkillPromptPlan",
    "SkillAvailability",
    "SkillRuntimeMeta",
    "SkillQualityMeta",
    "SkillRetrievalMeta",
    # Loader principal
    "SkillLoader",
    # Função de módulo (usada em testes e por ferramentas estáticas)
    "_parse_skill_file",
]
