"""
temporal_decay.py — Decaimento temporal de scores de memória.

Porta directa de packages/memory/src/temporal-decay.ts do @arkhe/memory TS.

As funções aqui são STANDALONE — não dependem de SQLite nem de OpenAI.
Já existem implementações inline em memory_manager.py e knowledge_base.py;
este módulo expõe as mesmas fórmulas como funções reutilizáveis de primeira
classe, seguindo o mesmo padrão que foi feito para os outros módulos.

Uso típico:
    from rlm.core.memory.temporal_decay import apply_temporal_decay, age_in_days

    results = apply_temporal_decay(search_results, half_life_days=7.0)
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from rlm.core.memory.memory_types import SearchResult


# ln(2): constante usada no cálculo de meia-vida exponencial.
_LN2 = math.log(2)


# ---------------------------------------------------------------------------
# age_in_days
# ---------------------------------------------------------------------------

def age_in_days(created_at: str) -> float:
    """
    Calcula a idade de um registro em dias fracionários relativos a *agora*.

    Porta de ``ageInDays`` em temporal-decay.ts.

    Parâmetros
    ----------
    created_at:
        String ISO-8601 (ex.: "2026-04-01T12:00:00Z").

    Retorna
    -------
    Idade em dias (ex.: 0.5 = 12 horas). Nunca negativo.
    Se o timestamp for inválido, retorna 0.
    """
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.0
    now = datetime.now(tz=timezone.utc)
    diff_seconds = (now - created).total_seconds()
    return max(0.0, diff_seconds / 86_400.0)


# ---------------------------------------------------------------------------
# apply_temporal_decay
# ---------------------------------------------------------------------------

def apply_temporal_decay(
    results: list[SearchResult],
    half_life_days: float,
) -> list[SearchResult]:
    """
    Aplica decaimento temporal exponencial nos scores de busca.

    Fórmula:
        score' = score × exp(−λ × age_days)
        λ = ln(2) / half_life_days

    Entradas sem campo ``created_at`` válido são mantidas inalteradas.
    A lista retornada é re-ordenada pelo score decaído.

    Porta de ``applyTemporalDecay`` em temporal-decay.ts.

    Parâmetros
    ----------
    results:
        Resultados de busca ranqueados.
    half_life_days:
        Dias após os quais a relevância cai à metade. Deve ser positivo.

    Levanta
    -------
    ValueError se ``half_life_days`` ≤ 0.
    """
    if half_life_days <= 0:
        raise ValueError(f"half_life_days deve ser positivo, recebeu {half_life_days}")

    lam = _LN2 / half_life_days

    import dataclasses

    decayed: list[SearchResult] = []
    for r in results:
        age = age_in_days(r.entry.created_at)
        decay_factor = math.exp(-lam * age)
        decayed.append(dataclasses.replace(r, score=r.score * decay_factor))

    decayed.sort(key=lambda r: r.score, reverse=True)
    return decayed
