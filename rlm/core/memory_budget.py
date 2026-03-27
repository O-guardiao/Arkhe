"""
memory_budget.py — Phase 2: Budget Gate Tripartito para Injeção de Memória

Responsabilidade única: decidir QUAIS e QUANTOS chunks de memória injetar
no prompt de um turno, respeitando um orçamento de tokens e usando a
pontuação tripartita (recência × importância × relevância).

Motivação acadêmica:
  Generative Agents (Park et al., 2023) usa:
      score = α·recency + β·importance + γ·relevance
  Este módulo implementa essa fórmula com pesos ajustáveis e adiciona
  um gate de orçamento de tokens (Context Budget, inspirado em MemGPT).

Fluxo de inject_memory_with_budget():
  1. Busca os top-N chunks via search_hybrid() (retrieval amplo)
  2. Recalcula o score tripartito (recência + importância + relevância cosine)
  3. Ordena por score tripartito (substitui o hybrid_score básico do DB)
  4. Aplica o budget gate: injeta chunks até o limite de tokens ser atingido
  5. Retorna (chunks_selecionados, tokens_utilizados)

Constantes de configuração:
  MEMORY_BUDGET_PCT   = 0.30  → 30% dos tokens disponíveis para memória
  SCORE_THRESHOLD     = 0.35  → score mínimo para entrar no prompt
  IMPORTANCE_WEIGHT   = 0.35  → β (importância estratégica)
  RECENCY_WEIGHT      = 0.25  → α (recência — penaliza memórias antigas)
  RELEVANCE_WEIGHT    = 0.40  → γ (relevância semântica à query atual)
  RETRIEVAL_LIMIT     = 20    → chunks buscados antes do gate (amplo)
  TOKENS_PER_CHAR     ≈ 0.25  → estimativa: 1 token ≈ 4 caracteres em pt-BR
"""
from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

from rlm.core.structured_log import get_logger

if TYPE_CHECKING:
    from rlm.core.memory_manager import MultiVectorMemory

_log = get_logger("memory_budget")

# ---------------------------------------------------------------------------
# Constantes de configuração
# ---------------------------------------------------------------------------

MEMORY_BUDGET_PCT: float = 0.30
"""Fração dos tokens disponíveis reservada para memórias injetadas (30%)."""

SCORE_THRESHOLD: float = 0.35
"""Score tripartito mínimo para um chunk entrar no prompt."""

IMPORTANCE_WEIGHT: float = 0.35   # β
"""Peso da importância estratégica (assigned_importance do mini agent)."""

RECENCY_WEIGHT: float = 0.25      # α
"""Peso da recência — chunks mais recentes recebem score maior."""

RELEVANCE_WEIGHT: float = 0.40    # γ
"""Peso da relevância semântica (cosine + BM25 via hybrid_score do DB)."""

RETRIEVAL_LIMIT: int = 20
"""Número de chunks buscados no search_hybrid antes do gate de budget."""

TOKENS_PER_CHAR: float = 0.25
"""Estimativa de tokens por caractere (1 token ≈ 4 chars em pt-BR/en)."""

RECENCY_HALF_LIFE_DAYS: float = 7.0
"""Meia-vida em dias para o componente de recência (α). Após 7 dias, score cai 50%."""


# ---------------------------------------------------------------------------
# Pontuação tripartita
# ---------------------------------------------------------------------------

def score_tripartite(
    chunk: dict[str, Any],
    *,
    now_ts: float | None = None,
    importance_weight: float = IMPORTANCE_WEIGHT,
    recency_weight: float = RECENCY_WEIGHT,
    relevance_weight: float = RELEVANCE_WEIGHT,
    half_life_days: float = RECENCY_HALF_LIFE_DAYS,
) -> float:
    """
    Calcula o score combinado (α·recency + β·importance + γ·relevance).

    O `hybrid_score` retornado por search_hybrid() é usado diretamente como
    o componente de relevância γ, pois já incorpora BM25 + cosine + temporal decay
    do banco. O score tripartito aqui repondera com os pesos do budget gate.

    Args:
        chunk: Dict retornado por search_hybrid() — deve ter:
               'hybrid_score' (float), 'importance_score' (float), 'timestamp' (str).
        now_ts: Epoch seconds para cálculo de recência (None = time.time()).
        importance_weight: β — peso da importância.
        recency_weight: α — peso da recência.
        relevance_weight: γ — peso da relevância (hybrid_score).
        half_life_days: Meia-vida para o decaimento exponencial de recência.

    Returns:
        float entre 0.0 e 1.0 (normalizado, mas pode ultrapassar 1.0 no extremo).
    """
    if not chunk:
        return 0.0

    # γ — relevância: hybrid_score já está no range [0, ~0.05] após RRF
    # Normalizamos para [0, 1] usando uma escala heurística (RRF máximo ≈ 1/60 ≈ 0.0167)
    raw_relevance = float(chunk.get("hybrid_score", 0.0))
    # Normalização: satura em 1.0 quando score ≥ 0.05 (dois primeiros resultados em RRF)
    relevance = min(1.0, raw_relevance / 0.05) if raw_relevance > 0 else 0.0

    # β — importância: já está no range [0, 1] (atribuído pelo mini agent)
    importance = float(chunk.get("importance_score", 0.5))
    importance = max(0.0, min(1.0, importance))

    # α — recência: decaimento exponencial baseado na idade do chunk
    if now_ts is None:
        now_ts = time.time()
    recency = 1.0  # padrão para chunks sem timestamp
    ts_str = chunk.get("timestamp", "")
    if ts_str:
        try:
            import datetime as _dt
            ts_dt = _dt.datetime.fromisoformat(ts_str.replace(" ", "T"))
            age_days = (now_ts - ts_dt.timestamp()) / 86400.0
            decay_lambda = math.log(2) / max(half_life_days, 0.1)
            recency = math.exp(-decay_lambda * max(age_days, 0.0))
        except Exception:
            recency = 1.0  # timestamp inválido → recência máxima (seguro)

    score = (
        recency_weight * recency
        + importance_weight * importance
        + relevance_weight * relevance
    )
    return round(score, 6)


# ---------------------------------------------------------------------------
# Budget gate
# ---------------------------------------------------------------------------

def inject_memory_with_budget(
    query: str,
    session_id: str,
    memory_manager: "MultiVectorMemory",
    available_tokens: int,
    budget_pct: float = MEMORY_BUDGET_PCT,
    score_threshold: float = SCORE_THRESHOLD,
    retrieval_limit: int = RETRIEVAL_LIMIT,
) -> tuple[list[dict[str, Any]], int]:
    """
    Seleciona chunks de memória para injeção respeitando o orçamento de tokens.

    Fluxo:
      1. Busca retrieval_limit chunks via search_hybrid() (busca ampla)
      2. Recalcula score tripartito para cada chunk
      3. Filtra pelo score_threshold
      4. Ordena por score tripartito (melhor primeiro)
      5. Injeta chunks um por um até atingir o budget de tokens
      6. Retorna (chunks_selecionados, tokens_utilizados)

    Args:
        query: Mensagem do usuário neste turno (usada para busca de relevância).
        session_id: ID da sessão —  isola memórias por sessão.
        memory_manager: Instância de MultiVectorMemory.
        available_tokens: Tokens totais estimados disponíveis para o prompt.
        budget_pct: Fração de available_tokens reservada para memórias (padrão 30%).
        score_threshold: Score tripartito mínimo para injetar (padrão 0.35).
        retrieval_limit: Quantos chunks buscar antes do gate (padrão 20).

    Returns:
        Tupla (chunks, tokens_used) onde:
          - chunks: lista de dicts do search_hybrid() que passaram no gate,
                    ordenados por score tripartito decrescente.
          - tokens_used: total de tokens estimados consumidos pelos chunks.

    Falha silenciosa: em exceção, retorna ([], 0) para nunca bloquear um turno.
    """
    try:
        token_budget = int(available_tokens * budget_pct)
        if token_budget <= 0:
            return [], 0

        # 1. Busca ampla
        candidates = memory_manager.search_hybrid(
            query,
            limit=retrieval_limit,
            session_id=session_id,
            temporal_decay=True,
            half_life_days=30.0,  # decay suave no retrieval; recência recalculada no gate
        )

        if not candidates:
            return [], 0

        # 2. Recalcula score tripartito (sobrescreve o hybrid_score básico do DB)
        now_ts = time.time()
        scored = []
        for chunk in candidates:
            ts = score_tripartite(chunk, now_ts=now_ts)
            if ts >= score_threshold:
                scored.append((ts, chunk))

        if not scored:
            return [], 0

        # 3. Ordena por score tripartito decrescente (melhor primeiro)
        scored.sort(key=lambda x: x[0], reverse=True)

        # 4. Budget gate — injeta até esgotar o budget de tokens
        selected: list[dict[str, Any]] = []
        tokens_used = 0

        for ts, chunk in scored:
            content = chunk.get("content", "")
            # Estimativa de tokens: caracteres × TOKENS_PER_CHAR (sem chamar tokenizer)
            chunk_tokens = max(1, int(len(content) * TOKENS_PER_CHAR))
            overhead = 10  # overhead de formatação por chunk (marcadores, separadores)

            if tokens_used + chunk_tokens + overhead > token_budget:
                break  # budget esgotado — para aqui

            chunk_copy = dict(chunk)
            chunk_copy["tripartite_score"] = ts
            selected.append(chunk_copy)
            tokens_used += chunk_tokens + overhead

        _log.debug(
            f"Memory budget gate: {len(candidates)} candidatos → "
            f"{len(selected)} injetados, {tokens_used}/{token_budget} tokens usados"
        )

        return selected, tokens_used

    except Exception as exc:
        _log.warn(f"inject_memory_with_budget falhou (suprimido): {exc}")
        return [], 0


# ---------------------------------------------------------------------------
# Estimativa de tokens
# ---------------------------------------------------------------------------

def estimate_tokens_from_text(text: str) -> int:
    """
    Estimativa rápida de tokens sem tokenizer.

    Usa a heurística de TOKENS_PER_CHAR (1 token ≈ 4 chars em pt-BR/en).
    Adequada para estimativas de budget — não para contagem exata.

    Args:
        text: Texto a estimar.

    Returns:
        Número estimado de tokens (mínimo 1).
    """
    if not text:
        return 0
    return max(1, int(len(text) * TOKENS_PER_CHAR))


def format_memory_block(chunks: list[dict[str, Any]]) -> str:
    """
    Formata a lista de chunks selecionados como bloco de texto para injeção no prompt.

    Formato:
        [MEMÓRIAS RELEVANTES]
          - <content1>
          - <content2>
        [FIM DAS MEMÓRIAS]

    Args:
        chunks: Lista de chunks retornados por inject_memory_with_budget().

    Returns:
        String formatada pronta para injeção no prompt, ou string vazia se chunks=[].
    """
    if not chunks:
        return ""

    lines = ["[MEMÓRIAS RELEVANTES — baseadas no histórico de longo prazo desta sessão]"]
    for i, chunk in enumerate(chunks, start=1):
        content = chunk.get("content", "").strip()
        if content:
            # Limita cada chunk a 400 chars para não explodir o prompt
            truncated = content[:400] + ("…" if len(content) > 400 else "")
            lines.append(f"  {i}. {truncated}")
    lines.append("[FIM DAS MEMÓRIAS]")

    return "\n".join(lines)
