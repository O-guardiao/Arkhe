"""
knowledge_consolidator.py — Consolida nuggets de sessão em documentos do Knowledge Base

Responsabilidade: quando uma sessão fecha (ou a cada N turnos), coleta os nuggets
da sessão, agrupa por tópico, e cria/atualiza documentos no Knowledge Base global.

Pipeline:
  1. Coleta nuggets da sessão (memory_chunks WHERE session_id = ?)
  2. Agrupa por tópico via GPT-4.1-nano (clustering semântico)
  3. Para cada cluster: gera title, summary, full_context, tags, domain
  4. Busca documentos KB existentes que colidem (merge detection)
  5. INSERT novo ou MERGE com existente
  6. (Opcional) Mirror para Obsidian vault

Usa GPT-4.1-nano para todas as chamadas de LLM (barato, rápido).
Falha silenciosa total — nunca bloqueia o fechamento de sessão.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import closing
from typing import Any, Dict, List, Optional

from rlm.core.structured_log import get_logger

_log = get_logger("knowledge_consolidator")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

MIN_NUGGETS_TO_CONSOLIDATE: int = 2
"""Mínimo de nuggets para justificar consolidação (1 nugget = não vale)."""

MERGE_SIMILARITY_THRESHOLD: float = 0.75
"""Score mínimo de similaridade para merge com documento KB existente."""

CHECKPOINT_INTERVAL_TURNS: int = 10
"""A cada N turnos, roda consolidação parcial (para sessões longas)."""


# ---------------------------------------------------------------------------
# LLM Calls (via memory_mini_agent infra)
# ---------------------------------------------------------------------------

def _call_nano(system_prompt: str, user_content: str) -> Optional[str]:
    """Delega chamada ao GPT-4.1-nano via infra existente do mini agent."""
    try:
        from rlm.core.memory_mini_agent import _call_nano as _nano_call
        return _nano_call(system_prompt, user_content)
    except Exception as exc:
        _log.warn(f"Nano call falhou: {exc}")
        return None


_CLUSTER_PROMPT = """
Você recebe uma lista de "nuggets de memória" (fragmentos de informação) de uma sessão de trabalho.
Agrupe-os por TÓPICO. Cada grupo deve conter nuggets sobre o MESMO assunto.

REGRAS:
- Máximo 5 grupos
- Cada grupo precisa de pelo menos 2 nuggets (nuggets isolados vão no grupo "misc")
- Nuggets podem ficar em apenas 1 grupo
- Retorne JSON array de objetos

FORMATO DE SAÍDA (JSON):
[
  {"topic": "nome descritivo do tópico", "nugget_indices": [0, 2, 5]},
  {"topic": "outro tópico", "nugget_indices": [1, 3, 4]}
]

Retorne APENAS o JSON array.
""".strip()


_DOCUMENT_PROMPT = """
Você recebe um grupo de nuggets de memória sobre o mesmo tópico.
Crie um documento estruturado com:

1. TITLE: Título descritivo e específico (10-15 palavras). Deve incluir o que foi feito e o resultado.
2. SUMMARY: Resumo de 1-3 frases com as conclusões/decisões/outcomes principais. Autocontido.
3. TAGS: 5-10 tags relevantes em lowercase.
4. DOMAIN: Categoria principal (uma de: python, devops, architecture, database, security, frontend, backend, infra, general).

FORMATO DE SAÍDA (JSON):
{
  "title": "...",
  "summary": "...",
  "tags": ["tag1", "tag2"],
  "domain": "..."
}

Retorne APENAS o JSON.
""".strip()


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def collect_session_nuggets(
    session_id: str,
    memory_db_path: str,
) -> List[Dict[str, Any]]:
    """Coleta todos os nuggets não-deprecados de uma sessão."""
    try:
        with closing(sqlite3.connect(memory_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, content, metadata, importance_score, timestamp "
                "FROM memory_chunks "
                "WHERE session_id = ? AND is_deprecated = 0 "
                "ORDER BY timestamp ASC",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        _log.warn(f"Falha ao coletar nuggets da sessão {session_id}: {exc}")
        return []


def cluster_nuggets(nuggets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Agrupa nuggets por tópico usando GPT-4.1-nano.

    Retorna lista de clusters:
      [{"topic": "...", "nuggets": [dict, ...]}]
    """
    if len(nuggets) < MIN_NUGGETS_TO_CONSOLIDATE:
        return []

    # Formata nuggets para o prompt
    formatted = "\n".join(
        f"[{i}] {n.get('content', '')}"
        for i, n in enumerate(nuggets)
    )

    raw = _call_nano(_CLUSTER_PROMPT, f"Nuggets:\n{formatted}")
    if raw is None:
        # Fallback: todos os nuggets em um único cluster
        return [{"topic": "sessão", "nuggets": nuggets}]

    try:
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            clusters_raw = json.loads(match.group(0))
            result = []
            for cluster in clusters_raw:
                indices = cluster.get("nugget_indices", [])
                topic = cluster.get("topic", "misc")
                cluster_nuggets_list = [
                    nuggets[i] for i in indices
                    if isinstance(i, int) and 0 <= i < len(nuggets)
                ]
                if len(cluster_nuggets_list) >= MIN_NUGGETS_TO_CONSOLIDATE:
                    result.append({"topic": topic, "nuggets": cluster_nuggets_list})
            return result if result else [{"topic": "sessão", "nuggets": nuggets}]
    except (json.JSONDecodeError, KeyError, IndexError):
        pass

    return [{"topic": "sessão", "nuggets": nuggets}]


def generate_document_fields(
    topic: str,
    nuggets: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Gera title, summary, tags, domain para um cluster de nuggets.
    """
    contents = "\n".join(f"- {n.get('content', '')}" for n in nuggets)
    prompt_input = f"Tópico: {topic}\n\nNuggets:\n{contents}"

    raw = _call_nano(_DOCUMENT_PROMPT, prompt_input)
    if raw is None:
        # Fallback: usa tópico como título, concatena nuggets como summary
        return {
            "title": topic,
            "summary": ". ".join(n.get("content", "") for n in nuggets[:3]),
            "tags": [],
            "domain": "general",
        }

    try:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            doc = json.loads(match.group(0))
            return {
                "title": doc.get("title", topic),
                "summary": doc.get("summary", ""),
                "tags": doc.get("tags", []),
                "domain": doc.get("domain", "general"),
            }
    except (json.JSONDecodeError, KeyError):
        pass

    return {
        "title": topic,
        "summary": ". ".join(n.get("content", "") for n in nuggets[:3]),
        "tags": [],
        "domain": "general",
    }


def build_full_context(
    topic: str,
    nuggets: List[Dict[str, Any]],
    session_id: str,
) -> str:
    """Constrói o full_context a partir dos nuggets brutos."""
    lines = [
        f"## Sessão: {session_id}",
        f"## Tópico: {topic}",
        f"## Data: {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "### Nuggets consolidados:",
    ]
    for i, n in enumerate(nuggets, 1):
        content = n.get("content", "")
        importance = n.get("importance_score", 0.5)
        timestamp = n.get("timestamp", "?")
        lines.append(f"  {i}. [{importance:.1f}] {content} ({timestamp})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main consolidation pipeline
# ---------------------------------------------------------------------------

def consolidate_session(
    session_id: str,
    memory_db_path: str,
    knowledge_base: Any,  # GlobalKnowledgeBase
) -> List[str]:
    """
    Pipeline principal: consolida nuggets de uma sessão em documentos do KB.

    Args:
        session_id: ID da sessão a consolidar.
        memory_db_path: Caminho do memory.db da sessão.
        knowledge_base: Instância de GlobalKnowledgeBase.

    Returns:
        Lista de doc_ids criados ou atualizados no KB.
    """
    try:
        # 1. Coleta nuggets
        nuggets = collect_session_nuggets(session_id, memory_db_path)
        if len(nuggets) < MIN_NUGGETS_TO_CONSOLIDATE:
            _log.debug(f"Sessão {session_id}: {len(nuggets)} nuggets — abaixo do mínimo, pulando consolidação")
            return []

        _log.info(f"KB consolidation: sessão {session_id} com {len(nuggets)} nuggets")

        # 2. Agrupa por tópico
        clusters = cluster_nuggets(nuggets)
        if not clusters:
            return []

        doc_ids = []
        for cluster in clusters:
            topic = cluster["topic"]
            cluster_nuggets_list = cluster["nuggets"]

            # 3. Gera campos do documento
            fields = generate_document_fields(topic, cluster_nuggets_list)
            if fields is None:
                continue

            # 4. Gera full_context
            full_ctx = build_full_context(topic, cluster_nuggets_list, session_id)

            # 5. Calcula importância média dos nuggets
            avg_importance = sum(
                float(n.get("importance_score", 0.5)) for n in cluster_nuggets_list
            ) / len(cluster_nuggets_list)

            # 6. Busca colisão no KB (documento existente sobre o mesmo tópico)
            existing_docs = knowledge_base.search_hybrid(
                fields["title"],
                limit=3,
                status="active",
            )

            merged = False
            for existing in existing_docs:
                # Se o score é alto o suficiente, faz merge
                tripartite = knowledge_base.score_tripartite(existing)
                if tripartite >= MERGE_SIMILARITY_THRESHOLD:
                    _log.info(f"KB merge: '{fields['title'][:50]}' → doc existente '{existing['id']}'")
                    # Merge: atualiza documento existente
                    existing_full = knowledge_base.get_document(existing["id"])
                    if existing_full:
                        merged_context = existing_full.get("full_context", "") + "\n\n---\n\n" + full_ctx
                        merged_sessions = list(set(
                            existing_full.get("source_sessions", []) + [session_id]
                        ))
                        merged_tags = list(set(
                            existing_full.get("tags", []) + fields.get("tags", [])
                        ))
                        # Regenera summary com contexto expandido
                        # (usa o summary existente + novo como input)
                        merged_summary = existing_full.get("summary", "") + " " + fields.get("summary", "")
                        if len(merged_summary) > 500:
                            merged_summary = merged_summary[:500]

                        knowledge_base.update_document(
                            existing["id"],
                            summary=merged_summary,
                            full_context=merged_context,
                            tags=merged_tags,
                            importance=max(existing.get("importance", 0.5), avg_importance),
                            source_sessions=merged_sessions,
                        )
                        doc_ids.append(existing["id"])
                        merged = True
                        break

            if not merged:
                # 7. Insert novo documento
                doc_id = knowledge_base.add_document(
                    title=fields["title"],
                    summary=fields["summary"],
                    full_context=full_ctx,
                    tags=fields.get("tags", []),
                    domain=fields.get("domain", "general"),
                    importance=avg_importance,
                    source_sessions=[session_id],
                )
                doc_ids.append(doc_id)
                _log.info(f"KB insert: novo doc '{doc_id}' — '{fields['title'][:60]}'")

        return doc_ids

    except Exception as exc:
        _log.warn(f"Consolidação da sessão {session_id} falhou (suprimido): {exc}")
        return []
