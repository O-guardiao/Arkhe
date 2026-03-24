"""
RLM MCP Skill Loader — Smart Skill Delivery System

Inspired by but architecturally different from OpenClaw skills/.

Uma "Skill" no RLM é um arquivo SKILL.md com frontmatter TOML que:
1. Descreve QUANDO e COMO usar a skill
2. Declara o servidor MCP a ser iniciado (command + args) — opcional
3. Declara tags para keyword routing inteligente
4. Define priority de injeção no system prompt

Formato do SKILL.md:
    +++
    name = "sqlite"
    description = "Query SQLite databases..."
    tags = ["sqlite", "banco de dados", "sql", "db"]
    priority = "contextual"   # "always" | "contextual" | "lazy"

    [mcp]
    command = "npx"
    args = ["-y", "@modelcontextprotocol/server-sqlite"]

    [requires]
    bins = ["node"]
    +++

    # Corpo markdown com exemplos de uso

## Smart Skill Delivery — 3 Camadas

Ao invés de injetar TODOS os bodies no system prompt (problema do OpenClaw),
o RLM usa entrega inteligente de contexto:

  CAMADA 1 — Index compacto (sempre no system prompt):
    Apenas nome + descrição 1 linha de cada skill.
    ~30 tokens por skill → 19 skills ≈ 570 tokens totais.

  CAMADA 2 — Keyword routing (zero overhead de LLM):
    Se `tags` de uma skill estiverem na query → body completo é injetado.
    Sem chamada extra ao LLM — pura comparação de strings Python.

  CAMADA 3 — skill_doc() global no REPL (on-demand):
    O LLM chama `skill_doc("shell")` no início do bloco Python
    se precisar de exemplos completos de uma skill não detectada pelo routing.
    Retorna o body completo na próxima iteração via stdout do REPL.

Resultado: query simples → ~570 tokens. Query jurídica complexa → ~3000 tokens.
OpenClaw: SEMPRE todos os bodies → ~20.000 tokens por completion.
"""
from __future__ import annotations

import os
import json
import re
import shutil
import sys
from dataclasses import dataclass, field

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[no-redef]
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]  # pip install tomli
from pathlib import Path
from typing import Any, Callable

from rlm.core.structured_log import get_logger

# Import lazy para evitar circular: sif importa skill_loader para estimate_tokens
# O import real acontece dentro dos métodos que precisam dele

skill_log = get_logger("skill_loader")

_MICRO_QUERY_PATTERNS = [
    re.compile(r"^\s*(oi|ol[aá]|opa|e ai|e aí|bom dia|boa tarde|boa noite)\s*!*\s*$", re.IGNORECASE),
    re.compile(r"^\s*(obrigad[oa]|valeu|vlw|tmj|show|perfeito)\s*!*\s*$", re.IGNORECASE),
    re.compile(r"^\s*(quem e voce|quem é você|o que voce pode fazer|o que você pode fazer|como voce pode ajudar|como você pode ajudar)\s*\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*(tudo bem|como vai|como voce esta|como você está)\s*\??\s*$", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# SkillDef
# ---------------------------------------------------------------------------


@dataclass
class SkillRuntimeMeta:
    """Metadados operacionais usados por roteamento, risco e fallback."""

    estimated_cost: float = 1.0
    risk_level: str = "medium"
    side_effects: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    fallback_policy: str = ""
    historical_reliability: float | None = None


@dataclass
class SkillQualityMeta:
    historical_reliability: float | None = None
    success_count: int = 0
    failure_count: int = 0
    last_30d_utility: float | None = None


@dataclass
class SkillRetrievalMeta:
    embedding_text: str = ""
    example_queries: list[str] = field(default_factory=list)


@dataclass
class SkillRank:
    skill: "SkillDef"
    score: float
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    hint_score: float = 0.0
    description_score: float = 0.0
    telemetry_score: float = 0.0
    trace_score: float = 0.0
    reliability_score: float = 0.0
    cost_penalty: float = 0.0
    risk_penalty: float = 0.0


@dataclass
class SkillPromptPlan:
    effective_mode: str
    expanded_skills: list["SkillDef"] = field(default_factory=list)
    index_only_skills: list["SkillDef"] = field(default_factory=list)
    matched_skills: list["SkillDef"] = field(default_factory=list)
    ranked_skills: list[SkillRank] = field(default_factory=list)
    blocked_skills: list["SkillAvailability"] = field(default_factory=list)


@dataclass
class SkillAvailability:
    skill: "SkillDef"
    ready: bool
    reasons: list[str] = field(default_factory=list)

@dataclass
class SkillDef:
    """Parsed representation of a SKILL.md file."""

    name: str                           # Identificador único (slug)
    description: str = ""              # Injetado no system prompt
    body: str = ""                     # Corpo markdown com exemplos

    # MCP server config (opcional — se ausente a skill é só contexto)
    mcp_command: str = ""              # ex: "npx.cmd", "python", "uvx"
    mcp_args: list[str] = field(default_factory=list)  # args do servidor MCP
    mcp_env: dict[str, str] = field(default_factory=dict)  # env vars extras

    # Eligibility
    requires_bins: list[str] = field(default_factory=list)  # binários necessários
    requires_any_bins: list[str] = field(default_factory=list)  # basta um destes

    # Smart Skill Delivery
    tags: list[str] = field(default_factory=list)
    # Keywords para keyword routing — ex: ["terminal", "ssh", "deploy"]
    # Se qualquer tag aparecer na query do usuário → body completo é injetado.

    priority: str = "contextual"
    # "always"      → body sempre no system prompt (skills universais)
    # "contextual"  → body injetado só se tags matcharem a query (padrão)
    # "lazy"        → nunca no system prompt; só via skill_doc() no REPL

    # SIF — Skill Interface Format
    sif_entry: Any = field(default=None)
    # Metadados SIF: assinatura, impl compilada, grafo de composição.
    # Tipo Any para evitar import circular com sif.py.
    # Em runtime é um SIFEntry ou None.

    # Runtime state
    source_path: str = ""              # Caminho do SKILL.md de origem
    runtime: SkillRuntimeMeta = field(default_factory=SkillRuntimeMeta)
    quality: SkillQualityMeta = field(default_factory=SkillQualityMeta)
    retrieval: SkillRetrievalMeta = field(default_factory=SkillRetrievalMeta)

    @property
    def has_mcp(self) -> bool:
        return bool(self.mcp_command)

    @property
    def namespace_name(self) -> str:
        """Nome da variável injetada no REPL Python (ex: 'sqlite', 'fs')."""
        return self.name.replace("-", "_")

    def matches_query(self, query: str) -> bool:
        """Retorna True se qualquer tag ou o próprio nome está na query."""
        if not query:
            return False
        q = query.lower()
        if self.name.lower() in q:
            return True
        return any(tag.lower() in q for tag in self.tags)


# ---------------------------------------------------------------------------
# _parse_skill_file — função de módulo (exposta para testes e uso estático)
# ---------------------------------------------------------------------------

def _parse_skill_file(text: str, name: str = "") -> SkillDef:
    """Parse o conteúdo de um SKILL.md com frontmatter TOML delimitado por +++.

    Args:
        text: Conteúdo completo do arquivo SKILL.md.
        name: Nome de fallback caso o frontmatter não declare ``name``.

    Returns:
        :class:`SkillDef` preenchido. ``source_path`` ficará vazio —
        use :meth:`SkillLoader._parse_skill_file` se precisar do caminho.
    """
    toml_data: dict[str, Any] = {}
    body = text.strip()

    if text.startswith("+++"):
        end = text.find("\n+++", 3)
        if end == -1:
            raise ValueError("Frontmatter TOML sem fechamento (esperado '\\n+++')")
        toml_block = text[3:end].strip()
        body = text[end + 4:].strip()
        toml_data = tomllib.loads(toml_block)

    # Campos obrigatórios
    resolved_name: str = toml_data.get("name", name)
    description: str = toml_data.get("description", "")

    # MCP config
    mcp = toml_data.get("mcp", {})
    mcp_command: str = mcp.get("command", "")
    # Normalize Windows-only .cmd shims on non-Windows platforms
    if mcp_command.endswith(".cmd") and sys.platform != "win32":
        mcp_command = mcp_command[:-4]  # "npx.cmd" → "npx"
    mcp_args: list[str] = [str(a) for a in mcp.get("args", [])]
    mcp_env: dict[str, str] = {k: str(v) for k, v in mcp.get("env", {}).items()}

    # Requires
    req = toml_data.get("requires", {})
    requires_bins: list[str] = [str(b) for b in req.get("bins", [])]
    requires_any_bins: list[str] = [str(b) for b in req.get("any_bins", [])]

    # Smart Skill Delivery
    tags: list[str] = [str(t) for t in toml_data.get("tags", [])]
    priority: str = str(toml_data.get("priority", "contextual"))

    runtime = toml_data.get("runtime", {})
    quality = toml_data.get("quality", {})
    retrieval = toml_data.get("retrieval", {})
    runtime_meta = SkillRuntimeMeta(
        estimated_cost=float(runtime.get("estimated_cost", 1.0)),
        risk_level=str(runtime.get("risk_level", "medium") or "medium"),
        side_effects=[str(v) for v in runtime.get("side_effects", [])],
        preconditions=[str(v) for v in runtime.get("preconditions", [])],
        postconditions=[str(v) for v in runtime.get("postconditions", [])],
        fallback_policy=str(runtime.get("fallback_policy", "")),
        historical_reliability=(
            float(quality.get("historical_reliability"))
            if quality.get("historical_reliability") is not None
            else None
        ),
    )
    quality_meta = SkillQualityMeta(
        historical_reliability=(
            float(quality.get("historical_reliability"))
            if quality.get("historical_reliability") is not None
            else None
        ),
        success_count=int(quality.get("success_count", 0)),
        failure_count=int(quality.get("failure_count", 0)),
        last_30d_utility=(
            float(quality.get("last_30d_utility"))
            if quality.get("last_30d_utility") is not None
            else None
        ),
    )
    retrieval_meta = SkillRetrievalMeta(
        embedding_text=str(retrieval.get("embedding_text", "")),
        example_queries=[str(v) for v in retrieval.get("example_queries", [])],
    )

    # SIF — Skill Interface Format
    from rlm.core.sif import parse_sif_block  # import lazy
    sif_entry = parse_sif_block(toml_data)

    return SkillDef(
        name=resolved_name,
        description=description,
        body=body,
        mcp_command=mcp_command,
        mcp_args=mcp_args,
        mcp_env=mcp_env,
        requires_bins=requires_bins,
        requires_any_bins=requires_any_bins,
        tags=tags,
        priority=priority,
        sif_entry=sif_entry,
        runtime=runtime_meta,
        quality=quality_meta,
        retrieval=retrieval_meta,
    )


# ---------------------------------------------------------------------------
# SkillLoader
# ---------------------------------------------------------------------------

class SkillLoader:
    """
    Descobre, parseia e ativa MCP Skills para o RLM.

    Usage:
        loader = SkillLoader()
        skills = loader.load_from_dir("rlm/skills")

        # Verificar quais estão elegíveis (binários disponíveis)
        eligible = loader.filter_eligible(skills)

        # Gerar contexto para o system prompt
        ctx = loader.build_system_prompt_context(eligible)

        # Ativar servidores MCP e injetar no REPL
        loader.activate_all(eligible, repl_locals)
    """

    def __init__(self, quality_store_path: str | Path | None = None):
        self._active: dict[tuple[str, str], Any] = {}  # (name, scope) → MCPServerNamespace
        if quality_store_path is None:
            quality_store_path = os.environ.get(
                "RLM_SKILL_QUALITY_FILE",
                ".rlm_workspace/skill_quality.json",
            )
        self.quality_store_path = Path(quality_store_path)
        self._quality_store = self._load_quality_store()

    # --- Discovery ---

    def load_from_dir(self, skills_dir: str | Path) -> list[SkillDef]:
        """
        Varre um diretório recursivamente em busca de SKILL.md files.

        Estrutura esperada:
            skills/
              sqlite/SKILL.md
              filesystem/SKILL.md
              weather/SKILL.md
        """
        skills_dir = Path(skills_dir)
        if not skills_dir.is_dir():
            skill_log.warn(f"Skills dir not found: {skills_dir}")
            return []

        results: list[SkillDef] = []

        # Procura SKILL.md em subdirs diretos
        for entry in sorted(skills_dir.iterdir()):
            skill_file: Path | None = None
            if entry.is_dir():
                candidate = entry / "SKILL.md"
                if candidate.exists():
                    skill_file = candidate
            elif entry.is_file() and entry.name.endswith(".skill.md"):
                skill_file = entry

            if skill_file:
                try:
                    skill = self._parse_skill_file(skill_file)
                    self._apply_historical_quality(skill)
                    results.append(skill)
                    skill_log.debug(f"Loaded skill '{skill.name}' from {skill_file}")
                except Exception as e:
                    skill_log.warn(f"Failed to parse skill {skill_file}: {e}")

        return results

    def _parse_skill_file(self, path: Path) -> SkillDef:
        """Parse um SKILL.md a partir do caminho — delega para a função de módulo."""
        text = path.read_text(encoding="utf-8")
        fallback_name = path.parent.name or path.stem
        from dataclasses import replace as _dc_replace
        return _dc_replace(
            _parse_skill_file(text, name=fallback_name),
            source_path=str(path),
        )

    def _load_quality_store(self) -> dict[str, Any]:
        if not self.quality_store_path.exists():
            return {}
        try:
            with self.quality_store_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except Exception as exc:
            skill_log.warn(f"Failed to load skill quality store {self.quality_store_path}: {exc}")
        return {}

    def _save_quality_store(self) -> None:
        try:
            self.quality_store_path.parent.mkdir(parents=True, exist_ok=True)
            with self.quality_store_path.open("w", encoding="utf-8") as fh:
                json.dump(self._quality_store, fh, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception as exc:
            skill_log.warn(f"Failed to persist skill quality store {self.quality_store_path}: {exc}")

    def _apply_historical_quality(self, skill: SkillDef) -> None:
        record = self._quality_store.get(skill.name)
        if not isinstance(record, dict):
            return
        reliability = record.get("historical_reliability")
        if reliability is not None:
            try:
                skill.runtime.historical_reliability = float(reliability)
                skill.quality.historical_reliability = float(reliability)
            except (TypeError, ValueError):
                return
        skill.quality.success_count = int(record.get("success_count", skill.quality.success_count))
        skill.quality.failure_count = int(record.get("failure_count", skill.quality.failure_count))
        utility = record.get("last_30d_utility")
        if utility is not None:
            try:
                skill.quality.last_30d_utility = float(utility)
            except (TypeError, ValueError):
                pass

    def update_historical_reliability_from_telemetry(
        self,
        skills: list[SkillDef],
        *,
        persist: bool = True,
    ) -> bool:
        from rlm.core.skill_telemetry import get_skill_telemetry

        telemetry = get_skill_telemetry()
        changed = False
        for skill in skills:
            stats = telemetry.get_skill_stats(skill.name)
            call_count = int(stats.get("call_count", 0))
            if call_count <= 0:
                self._apply_historical_quality(skill)
                continue

            success_count = int(stats.get("success_count", 0))
            failure_count = int(stats.get("failure_count", 0))
            utility_rate = float(telemetry.get_skill_utility_rate(skill.name))
            derived_reliability = round((success_count + 1.0) / (call_count + 2.0), 4)
            previous = skill.runtime.historical_reliability
            skill.runtime.historical_reliability = derived_reliability
            skill.quality.historical_reliability = derived_reliability
            skill.quality.success_count = success_count
            skill.quality.failure_count = failure_count
            skill.quality.last_30d_utility = utility_rate
            stored = self._quality_store.get(skill.name, {})
            if not isinstance(stored, dict):
                stored = {}
            stored["historical_reliability"] = derived_reliability
            stored["call_count"] = call_count
            stored["success_count"] = success_count
            stored["failure_count"] = failure_count
            stored["last_30d_utility"] = utility_rate
            self._quality_store[skill.name] = stored
            if previous != derived_reliability:
                changed = True

        if changed and persist:
            self._save_quality_store()
        return changed

    # --- Eligibility ---

    def filter_eligible(
        self,
        skills: list[SkillDef],
        strict: bool = False,
    ) -> list[SkillDef]:
        """
        Retorna apenas as skills com todos os binários disponíveis no PATH.

        Args:
            skills: Lista de skills a filtrar.
            strict: Se True, loga warning para skills não elegíveis.
        """
        eligible = []
        for skill in skills:
            ok, reason = self._check_eligibility(skill)
            if ok:
                eligible.append(skill)
            elif strict:
                skill_log.warn(f"Skill '{skill.name}' not eligible: {reason}")
        return eligible

    def _check_eligibility(self, skill: SkillDef) -> tuple[bool, str]:
        """Retorna (eligible, reason_if_not)."""
        for b in skill.requires_bins:
            if not shutil.which(b):
                return False, f"missing binary: {b}"

        if skill.requires_any_bins:
            if not any(shutil.which(b) for b in skill.requires_any_bins):
                return False, f"none of {skill.requires_any_bins} found"

        return True, ""

    # --- System Prompt Context ---

    def build_system_prompt_context(
        self,
        skills: list[SkillDef],
        query: str = "",
        mode: str = "auto",
    ) -> str:
        """
        Gera contexto de skills para injetar no system prompt.

        Modos:
          "auto"    (padrão) — Smart Skill Delivery:
                    • priority="always" → body completo sempre
                    • priority="contextual" → body só se tags matcharem query
                    • priority="lazy" → apenas nome+descrição no index
                    Quando query="" (startup), todos ficam no index compacto.

          "sif"     → SIF v3: índice compacto + hints semânticos + recipes.
                    Entrega todas as skills em formato colunar, expande só
                    as instruções mais relevantes para a query e mantém as
                    implementações executáveis fora do prompt.
                    Use inject_sif_callables() para injetar as funções e
                    helpers de planejamento no REPL.

          "micro"   → pacote mínimo para small talk e pedidos triviais.
                    Entrega só um subconjunto SIF de alta utilidade para o
                    modelo não ficar cego às capacidades do runtime.

          "focused" → expande poucas skills relevantes, inclui tabela SIF
                    resumida e compose mais provável para tarefas multi-skill.

          "full"    → comportamento legado: todos os bodies (alto custo de tokens)
          "compact" → apenas index: nome + descrição, sem nenhum body

        O LLM pode chamar skill_doc("name") no REPL para obter o body
        completo de qualquer skill em qualquer momento.
        """
        if not skills:
            return ""

        plan = self.plan_prompt_context(skills, query=query, mode=mode)
        if plan.effective_mode == "compact":
            return self._build_compact_index(skills)
        if plan.effective_mode == "full":
            return self._build_full_context(skills)
        if plan.effective_mode == "sif":
            from rlm.core.sif import SIFTableBuilder
            return SIFTableBuilder.build(skills, query=query)
        if plan.effective_mode == "micro":
            return self._build_micro_context(skills, query=query)
        if plan.effective_mode == "focused":
            return self._build_focused_context(plan, query=query)
        return self._render_auto_context(plan)

    def plan_prompt_context(
        self,
        skills: list[SkillDef],
        query: str = "",
        mode: str = "auto",
    ) -> SkillPromptPlan:
        if not skills:
            return SkillPromptPlan(effective_mode=mode)
        self.update_historical_reliability_from_telemetry(skills, persist=False)
        if mode == "compact":
            return SkillPromptPlan(effective_mode="compact")
        if mode == "full":
            return SkillPromptPlan(effective_mode="full")
        if mode == "sif":
            return SkillPromptPlan(effective_mode="sif")
        if mode == "micro":
            return SkillPromptPlan(effective_mode="micro")
        if mode == "focused":
            return self._plan_focused_context(skills, query=query)
        if mode == "auto" and self._should_use_micro_mode(query):
            return SkillPromptPlan(effective_mode="micro")

        always_skills: list[SkillDef] = []
        index_only_skills: list[SkillDef] = []
        contextual_skills: list[SkillDef] = []
        blocked_skills: list[SkillAvailability] = []
        query_text = query.strip()
        for skill in skills:
            availability = self.assess_skill_availability(skill)
            if not availability.ready:
                if skill.priority == "always" or self._score_overlap(query, skill.name, skill.tags) > 0:
                    blocked_skills.append(availability)
                continue
            if skill.priority == "always":
                always_skills.append(skill)
            elif skill.priority == "lazy":
                index_only_skills.append(skill)
            else:
                contextual_skills.append(skill)

        if not query_text:
            index_only_skills.extend(contextual_skills)
            return SkillPromptPlan(
                effective_mode="auto",
                expanded_skills=always_skills,
                index_only_skills=index_only_skills,
                matched_skills=[],
                ranked_skills=[],
                blocked_skills=blocked_skills,
            )

        ranked = self.rank_skills(contextual_skills, query=query)
        matched_skills = [
            rank.skill
            for rank in ranked
            if rank.score >= 2.5 and self._has_query_signal(rank)
        ][:4]
        matched_names = {skill.name for skill in matched_skills}
        for skill in contextual_skills:
            if skill.name not in matched_names:
                index_only_skills.append(skill)

        return SkillPromptPlan(
            effective_mode="auto",
            expanded_skills=always_skills + matched_skills,
            index_only_skills=index_only_skills,
            matched_skills=matched_skills,
            ranked_skills=ranked,
            blocked_skills=blocked_skills,
        )

    def _plan_focused_context(self, skills: list[SkillDef], query: str = "") -> SkillPromptPlan:
        if not skills:
            return SkillPromptPlan(effective_mode="focused")

        always_skills: list[SkillDef] = []
        contextual_skills: list[SkillDef] = []
        blocked_skills: list[SkillAvailability] = []
        for skill in skills:
            availability = self.assess_skill_availability(skill)
            if not availability.ready:
                if skill.priority == "always" or self._score_overlap(query, skill.name, skill.tags) > 0:
                    blocked_skills.append(availability)
                continue
            if skill.priority == "always":
                always_skills.append(skill)
            elif skill.priority != "lazy":
                contextual_skills.append(skill)

        ranked = self.rank_skills(contextual_skills, query=query)
        matched_skills = [rank.skill for rank in ranked if rank.score >= 1.5][:3]
        if not matched_skills and ranked:
            matched_skills = [ranked[0].skill]

        extra_context = [
            rank.skill for rank in ranked
            if rank.skill not in matched_skills
        ][:3]

        return SkillPromptPlan(
            effective_mode="focused",
            expanded_skills=always_skills + matched_skills,
            index_only_skills=extra_context,
            matched_skills=matched_skills,
            ranked_skills=ranked,
            blocked_skills=blocked_skills,
        )

    def assess_skill_availability(self, skill: SkillDef) -> SkillAvailability:
        reasons: list[str] = []
        for raw_precondition in skill.runtime.preconditions:
            ok, reason = self._evaluate_precondition(raw_precondition)
            if ok:
                continue
            if reason:
                reasons.append(reason)
        return SkillAvailability(skill=skill, ready=not reasons, reasons=reasons)

    def rank_skills(self, skills: list[SkillDef], query: str = "") -> list[SkillRank]:
        if not skills:
            return []

        self.update_historical_reliability_from_telemetry(skills)

        from rlm.core.skill_telemetry import get_skill_telemetry
        from rlm.core.semantic_retrieval import SemanticTextIndex

        telemetry = get_skill_telemetry()
        query_terms = self._tokenize(query)
        semantic_index = SemanticTextIndex(
            (skill.name, self._build_retrieval_corpus(skill)) for skill in skills
        )
        semantic_hits = {
            str(item["key"]): float(item["similarity"])
            for item in semantic_index.search(query, top_k=max(len(skills), 1))
        }
        ranked: list[SkillRank] = []
        for skill in skills:
            lexical_score = self._score_overlap(query, skill.name, skill.tags)
            semantic_score = semantic_hits.get(skill.name, 0.0) * 3.0
            entry = skill.sif_entry
            hint_text = entry.prompt_hint if entry is not None and entry.prompt_hint else ""
            hint_score = self._score_text_overlap(query_terms, hint_text) * 1.5
            description_score = self._score_text_overlap(query_terms, skill.description)
            telemetry_score = telemetry.get_skill_success_rate(skill.name) * 2.0
            trace_score = telemetry.get_trace_relevance_score(query, skill.name) * 1.25
            reliability_score = (
                (skill.runtime.historical_reliability if skill.runtime.historical_reliability is not None else 0.5)
                * 1.5
            )
            cost_penalty = max(skill.runtime.estimated_cost, 0.0) * 0.35
            risk_penalty = self._risk_penalty(skill.runtime.risk_level)
            priority_bonus = 0.5 if skill.priority == "always" else 0.0
            score = (
                lexical_score
                + semantic_score
                + hint_score
                + description_score
                + telemetry_score
                + trace_score
                + reliability_score
                + priority_bonus
                - cost_penalty
                - risk_penalty
            )
            ranked.append(SkillRank(
                skill=skill,
                score=round(score, 4),
                lexical_score=round(lexical_score, 4),
                semantic_score=round(semantic_score, 4),
                hint_score=round(hint_score, 4),
                description_score=round(description_score, 4),
                telemetry_score=round(telemetry_score, 4),
                trace_score=round(trace_score, 4),
                reliability_score=round(reliability_score, 4),
                cost_penalty=round(cost_penalty, 4),
                risk_penalty=round(risk_penalty, 4),
            ))
        ranked.sort(key=lambda item: (-item.score, item.skill.name))
        return ranked

    def _build_retrieval_corpus(self, skill: SkillDef) -> str:
        entry = skill.sif_entry
        parts = [
            skill.name,
            skill.description,
            skill.retrieval.embedding_text,
            " ".join(skill.retrieval.example_queries),
            " ".join(skill.tags),
        ]
        if entry is not None:
            parts.append(entry.prompt_hint)
            parts.extend(entry.examples_min)
        return "\n".join(part.strip() for part in parts if part and part.strip())

    def _render_auto_context(self, plan: SkillPromptPlan) -> str:
        sections: list[str] = []
        if plan.expanded_skills:
            sections.append("## Skills Ativas (exemplos completos)\n")
            for skill in plan.expanded_skills:
                sections.append(self._format_skill_full(skill))
        if plan.blocked_skills:
            sections.append("## Skills Indisponiveis Agora\n")
            for availability in plan.blocked_skills[:4]:
                reasons = "; ".join(availability.reasons)
                sections.append(f"- **{availability.skill.name}**: indisponivel agora ({reasons})")
            sections.append("")
        if plan.index_only_skills:
            hint = ' — chame `skill_doc("nome")` no REPL para exemplos completos'
            sections.append(f"## Skills Disponíveis{hint}\n")
            for skill in plan.index_only_skills:
                mcp_hint = f" [MCP:`{skill.namespace_name}`]" if skill.has_mcp else ""
                sections.append(f"- **{skill.name}**{mcp_hint}: {skill.description}")
            sections.append("")
        return "\n".join(sections)

    def _build_focused_context(self, plan: SkillPromptPlan, query: str = "") -> str:
        from rlm.core.sif import SIFTableBuilder

        chosen: list[SkillDef] = []
        seen: set[str] = set()
        for skill in plan.expanded_skills + plan.index_only_skills:
            if skill.name in seen:
                continue
            chosen.append(skill)
            seen.add(skill.name)
            if len(chosen) >= 6:
                break

        sections = [
            "## Skills Focused\n"
            "Pacote para tarefas com 2-3 capacidades acopladas. "
            "Inclui visão compacta do compose mais provável e poucos exemplos completos.\n"
        ]
        if chosen:
            sections.append(
                SIFTableBuilder.build(
                    chosen,
                    query=query,
                    include_hints=True,
                    include_recipes=True,
                    allow_partial_compose=True,
                )
            )
        if plan.expanded_skills:
            sections.append("## Skills Ativas (focused)\n")
            for skill in plan.expanded_skills:
                sections.append(self._format_skill_full(skill))
        if plan.blocked_skills:
            sections.append("## Skills Indisponiveis Agora\n")
            for availability in plan.blocked_skills[:4]:
                reasons = "; ".join(availability.reasons)
                sections.append(f"- **{availability.skill.name}**: indisponivel agora ({reasons})")
            sections.append("")
        return "\n".join(sections)

    def _tokenize(self, text: str) -> set[str]:
        return {term for term in re.findall(r"[a-zA-Z0-9_]+", text.lower()) if len(term) > 1}

    def _score_text_overlap(self, query_terms: set[str], text: str) -> float:
        if not query_terms or not text.strip():
            return 0.0
        text_terms = self._tokenize(text)
        return float(len(query_terms & text_terms))

    def _score_overlap(self, query: str, name: str, tags: list[str]) -> float:
        if not query.strip():
            return 0.0
        q = query.lower()
        score = 0.0
        if name.lower() in q:
            score += 5.0
        query_terms = self._tokenize(query)
        for tag in tags:
            tag_l = tag.lower()
            if tag_l in q:
                score += 2.5
            else:
                score += 0.5 * self._score_text_overlap(query_terms, tag_l)
        return score

    def _risk_penalty(self, risk_level: str) -> float:
        risk = risk_level.strip().lower()
        if risk == "high":
            return 0.75
        if risk == "medium":
            return 0.25
        return 0.0

    def _has_query_signal(self, rank: SkillRank) -> bool:
        return (
            rank.lexical_score > 0.0
            or rank.hint_score > 0.0
            or rank.description_score > 0.0
            or rank.trace_score > 0.0
            or rank.semantic_score >= 1.5
        )

    def _evaluate_precondition(self, precondition: str) -> tuple[bool, str]:
        token = precondition.strip()
        if not token:
            return True, ""

        prefix, separator, raw_value = token.partition(":")
        if not separator:
            return True, ""

        value = raw_value.strip()
        if not value:
            return True, ""

        if prefix == "env":
            return (True, "") if os.environ.get(value) else (False, f"missing env {value}")

        if prefix == "env_any":
            options = [item.strip() for item in value.split("|") if item.strip()]
            if any(os.environ.get(item) for item in options):
                return True, ""
            joined = ", ".join(options)
            return False, f"missing one of envs [{joined}]"

        if prefix in {"bin", "tool"}:
            return (True, "") if shutil.which(value) else (False, f"missing binary {value}")

        path = Path(os.path.expandvars(os.path.expanduser(value)))
        if prefix == "file":
            return (True, "") if path.is_file() else (False, f"missing file {path}")
        if prefix == "dir":
            return (True, "") if path.is_dir() else (False, f"missing directory {path}")

        return True, ""

    def _should_use_micro_mode(self, query: str) -> bool:
        if not query:
            return False
        normalized = " ".join(query.strip().split())
        if not normalized:
            return False
        return any(pattern.match(normalized) for pattern in _MICRO_QUERY_PATTERNS)

    def _build_micro_context(self, skills: list[SkillDef], query: str = "") -> str:
        from rlm.core.sif import SIFTableBuilder

        sif_skills = [
            skill for skill in skills
            if skill.sif_entry is not None and self.assess_skill_availability(skill).ready
        ]
        if not sif_skills:
            return self._build_compact_index(skills)

        always = [skill for skill in sif_skills if skill.priority == "always"]
        focus = [rank.skill for rank in self.rank_skills(sif_skills, query=query)[:4]]

        chosen: list[SkillDef] = []
        seen: set[str] = set()
        for skill in always + focus:
            if skill.name in seen:
                continue
            chosen.append(skill)
            seen.add(skill.name)
            if len(chosen) >= 5:
                break

        header = (
            "## Skills Micro\n"
            "Pacote mínimo para conversa leve e pedidos triviais. "
            "Se surgir tarefa real, use skill_doc(nome) ou chame a skill direta.\n\n"
        )
        body = SIFTableBuilder.build(
            chosen,
            query=query,
            include_hints=True,
            include_recipes=False,
            allow_partial_compose=True,
        )
        return header + body

    def _build_compact_index(self, skills: list[SkillDef]) -> str:
        """Apenas index: nome + descrição. Sem nenhum body."""
        lines = ["## Skills Disponíveis (use skill_doc(\"nome\") para exemplos)\n"]
        for skill in skills:
            mcp_hint = f" [MCP:`{skill.namespace_name}`]" if skill.has_mcp else ""
            lines.append(f"- **{skill.name}**{mcp_hint}: {skill.description}")
        lines.append("")
        return "\n".join(lines)

    def _build_full_context(self, skills: list[SkillDef]) -> str:
        """Comportamento legado: todos os bodies. Alto custo de tokens."""
        lines = ["## Available Skills\n"]
        for skill in skills:
            lines.append(self._format_skill_full(skill))
        return "\n".join(lines)

    def _format_skill_full(self, skill: SkillDef) -> str:
        """Formata uma skill com body completo."""
        parts = [f"### {skill.name}"]
        if skill.description:
            parts.append(skill.description)
        if skill.has_mcp:
            parts.append(
                f"**REPL namespace**: `{skill.namespace_name}` "
                f"(MCP server auto-loaded — call `{skill.namespace_name}.list_tools()` to see tools)"
            )
        if skill.body:
            parts.append("")
            parts.append(skill.body)
        parts.append("")
        return "\n".join(parts)

    # --- Skill Doc (REPL global) ---

    def build_skill_doc_fn(
        self, skills: list[SkillDef]
    ) -> tuple[Callable[[str], str], Callable[[], list[str]]]:
        """
        Retorna (skill_doc, skill_list) para injetar no REPL como globals.

        skill_doc(name) → body completo da skill (para o LLM usar on-demand)
        skill_list()    → lista de nomes disponíveis

        Uso típico pelo LLM no início de um bloco Python:
            # Ver exemplos completos antes de implementar
            print(skill_doc("shell"))
        """
        skill_map: dict[str, SkillDef] = {s.name: s for s in skills}
        available: list[str] = sorted(skill_map.keys())

        def skill_doc(name: str) -> str:
            """Retorna documentação completa e exemplos de uso de uma skill.

            Args:
                name: Nome da skill (ex: \"shell\", \"notion\", \"playwright\")

            Returns:
                String markdown com description + body completo da skill.
                Se a skill não existir, lista as disponíveis.
            """
            s = skill_map.get(name)
            if s is None:
                return (
                    f"Skill '{name}' não encontrada.\n"
                    f"Disponíveis: {available}\n"
                    f"Use skill_doc(nome) para ver exemplos de qualquer uma."
                )
            parts = [f"### {s.name}", s.description]
            if s.source_path:
                parts.append(f"**Fonte**: `{s.source_path}`")
            if s.has_mcp:
                parts.append(f"**REPL namespace**: `{s.namespace_name}` (MCP auto-loaded)")
            if s.body:
                parts.append("")
                parts.append(s.body)
            return "\n".join(parts)

        def skill_list() -> list[str]:
            """Lista todos os nomes de skills disponíveis no RLM."""
            return available

        return skill_doc, skill_list

    def estimate_tokens(self, skills: list[SkillDef], query: str = "") -> dict[str, int]:
        """
        Estima custo em tokens do contexto gerado para uma dada query.
        Útil para debugging e otimização.
        (Estimativa: 1 token ≈ 4 chars)
        """
        full_ctx = self._build_full_context(skills)
        smart_ctx = self.build_system_prompt_context(skills, query=query, mode="auto")
        focused_ctx = self.build_system_prompt_context(skills, query=query, mode="focused")
        compact_ctx = self._build_compact_index(skills)
        from rlm.core.sif import SIFTableBuilder
        sif_ctx = SIFTableBuilder.build(skills, query=query, allow_partial_compose=True)
        plan = self.plan_prompt_context(skills, query=query, mode="auto")
        return {
            "full_tokens": len(full_ctx) // 4,
            "smart_tokens": len(smart_ctx) // 4,
            "focused_tokens": len(focused_ctx) // 4,
            "compact_tokens": len(compact_ctx) // 4,
            "sif_tokens": len(sif_ctx) // 4,
            "matched_skills": len(plan.matched_skills),
            "saving_pct": round((1 - len(smart_ctx) / max(len(full_ctx), 1)) * 100),
            "focused_saving_pct": round((1 - len(focused_ctx) / max(len(full_ctx), 1)) * 100),
            "sif_saving_pct": round((1 - len(sif_ctx) / max(len(full_ctx), 1)) * 100),
        }

    def set_request_context(self, session_id: str = "", client_id: str = "", query: str = "") -> dict[str, Any]:
        from rlm.core.skill_telemetry import get_skill_telemetry
        return get_skill_telemetry().set_context(session_id=session_id, client_id=client_id, query=query)

    def clear_request_context(self, tokens: dict[str, Any] | None) -> None:
        from rlm.core.skill_telemetry import get_skill_telemetry
        get_skill_telemetry().reset_context(tokens)

    # --- SIF Injection ---

    def inject_sif_callables(
        self,
        skills: list[SkillDef],
        repl_locals: dict,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """
        Camada 2 do SIF: compila e injeta callables Python direto no REPL.

        Skills com [sif] impl declarado têm suas funções compiladas e injetadas
        como globals do REPL. O LLM chama diretamente sem ler documentação.

        Ex: shell("nginx -t") → executa subprocess.run imediatamente
            weather("São Paulo") → acessa wttr.in e retorna texto
            web_search("lei 14.133") → DuckDuckGo query e retorna lista

        Args:
            skills: Skills elegíveis com sif_entry.
            repl_locals: Namespace do REPL onde injetar.
            overwrite: Se True, sobrescreve globals existentes.

        Returns:
            Dict {name: callable} das funções injetadas.
        """
        from rlm.core.sif import SIFCompositionGraph, SIFFactory
        from rlm.core.skill_telemetry import get_skill_telemetry
        entries = [
            s.sif_entry for s in skills
            if s.sif_entry is not None and s.sif_entry.is_callable
        ]
        injected = SIFFactory.inject_all(entries, repl_locals, overwrite=overwrite)
        repl_locals.setdefault("sif_stats", SIFFactory.get_stats)
        repl_locals.setdefault("skill_trace_stats", get_skill_telemetry().get_summary)
        repl_locals.setdefault(
            "sif_plan",
            lambda goal, max_steps=4: SIFCompositionGraph.plan(skills, goal, max_steps=max_steps),
        )
        if injected:
            names = list(injected.keys())
            skill_log.info(f"SIF Factory: injetou {len(names)} callables → {names}")
        return injected

    # --- Activation ---

    def activate(
        self,
        skill: SkillDef,
        repl_locals: dict,
        env_overrides: dict[str, str] | None = None,
        activation_scope: str = "",
    ) -> Any | None:
        """
        Ativa uma skill MCP: inicia o servidor e injeta o namespace no REPL.

        Retorna o MCPServerNamespace ou None se a skill não tem MCP.

        Args:
            skill: Skill a ativar.
            repl_locals: Namespace do REPL onde injetar o objeto.
            env_overrides: Variáveis de ambiente extras para o servidor MCP.
        """
        if not skill.has_mcp:
            skill_log.debug(f"Skill '{skill.name}' has no MCP server, skipping activation")
            return None

        scope_key = activation_scope or ""
        active_key = (skill.name, scope_key)

        if active_key in self._active:
            existing = self._active[active_key]
            repl_locals[skill.namespace_name] = existing
            return existing

        try:
            from rlm.plugins.mcp import load_server  # import lazy to avoid circular
            merged_env = {**skill.mcp_env, **(env_overrides or {})}
            ns = load_server(
                server_name=skill.name,
                command=skill.mcp_command,
                args=skill.mcp_args,
                env=merged_env,
                scope_key=scope_key,
            )
            self._active[active_key] = ns
            repl_locals[skill.namespace_name] = ns
            scope_msg = f" [scope={scope_key}]" if scope_key else ""
            skill_log.info(
                f"✓ Skill '{skill.name}' MCP server activated → `{skill.namespace_name}`{scope_msg}"
            )
            return ns
        except Exception as e:
            skill_log.warn(f"Failed to activate MCP skill '{skill.name}': {e}")
            return None

    def activate_all(
        self,
        skills: list[SkillDef],
        repl_locals: dict,
        env_overrides: dict[str, str] | None = None,
        activation_scope: str = "",
    ) -> dict[str, Any]:
        """
        Ativa todas as skills MCP e injeta no REPL.
        Retorna {name: namespace} para as que foram ativadas com sucesso.
        """
        activated = {}
        for skill in skills:
            ns = self.activate(skill, repl_locals, env_overrides, activation_scope=activation_scope)
            if ns is not None:
                activated[skill.name] = ns
        return activated

    def deactivate_all(self) -> None:
        """Fecha todos os servidores MCP ativos."""
        for active_key, ns in list(self._active.items()):
            try:
                from rlm.plugins.mcp import close_cache_key

                if not close_cache_key(ns.cache_key):
                    ns.close()
                skill_log.debug(f"Closed MCP skill '{active_key[0]}' [scope={active_key[1]}]")
            except Exception as e:
                skill_log.warn(f"Error closing skill '{active_key[0]}' [scope={active_key[1]}]: {e}")
        self._active.clear()

    def deactivate_scope(self, activation_scope: str) -> int:
        """Fecha apenas os servidores MCP ativos de um escopo específico."""
        scope_key = activation_scope or ""
        if not scope_key:
            return 0

        closed = 0
        for active_key, ns in list(self._active.items()):
            if active_key[1] != scope_key:
                continue
            try:
                from rlm.plugins.mcp import close_cache_key

                if not close_cache_key(ns.cache_key):
                    ns.close()
                closed += 1
                skill_log.debug(f"Closed MCP skill '{active_key[0]}' [scope={scope_key}]")
            except Exception as e:
                skill_log.warn(f"Error closing skill '{active_key[0]}' [scope={scope_key}]: {e}")
            finally:
                self._active.pop(active_key, None)
        return closed

    def get_active_names(self) -> list[str]:
        return sorted({name for name, _scope in self._active.keys()})
