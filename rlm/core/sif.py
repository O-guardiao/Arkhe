"""
SIF v3 — Skill Interface Format
================================

Protocolo autoral de entrega de capacidades para agentes LLM.

SIF v3 separa 3 preocupações que o v2 misturava:

1. Declaração compacta para o prompt
2. Instrução semântica curta para o LLM
3. Implementação executável para o REPL

O resultado é um contexto menor que o SIF v2 completo com codex inline,
mas mais legível e mais útil para planejamento pelo modelo.
"""
from __future__ import annotations

import ast
import functools
import re
import textwrap
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from rlm.core.skill_loader import SkillDef

from rlm.core.structured_log import get_logger

# Import lazy — skill_telemetry cria _STORE no nível de módulo (I/O de disco).
# Resolve sob demanda para não penalizar imports de sif em testes/ferramentas.

sif_log = get_logger("sif")


def _get_telemetry():
    """Retorna o singleton de telemetria (lazy — evita I/O de disco no import)."""
    from rlm.core.skill_telemetry import get_skill_telemetry  # noqa: PLC0415
    return get_skill_telemetry()


_SAFE_IMPORT_MODULES = {
    "collections",
    "datetime",
    "email",
    "email.message",
    "json",
    "math",
    "os",
    "pathlib",
    "random",
    "re",
    "rlm",
    "shlex",
    "smtplib",
    "subprocess",
    "time",
    "typing",
    "urllib",
    "urllib.parse",
    "urllib.request",
}


def _guarded_import(
    name: str,
    globals_: dict[str, Any] | None = None,
    locals_: dict[str, Any] | None = None,
    fromlist: tuple[str, ...] | list[str] = (),
    level: int = 0,
):
    root = name.split(".", 1)[0]
    if name not in _SAFE_IMPORT_MODULES and root not in _SAFE_IMPORT_MODULES:
        raise ImportError(f"SIF import bloqueado: {name}")
    return __import__(name, globals_, locals_, fromlist, level)


_SAFE_BUILTINS: dict[str, Any] = {
    "__import__": _guarded_import,
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}

_TYPE_REWRITE_MAP = {
    "subprocess.CompletedProcess": "CP",
    "CompletedProcess": "CP",
    "list[dict]": "[{}]",
    "list[dict[str,Any]]": "[{}]",
    "list[str]": "[str]",
    "dict[str,Any]": "{}",
    "dict[str, str]": "{str}",
}


def _sanitize_callable_name(name: str) -> str:
    candidate = re.sub(r"\W+", "_", name.strip().replace("-", "_"))
    if not candidate:
        raise ValueError("SIF skill name não pode ser vazio")
    if candidate[0].isdigit():
        candidate = f"skill_{candidate}"
    return candidate


def _compact_type_name(type_name: str) -> str:
    compact = type_name.strip().replace("typing.", "")
    if not compact:
        return "Any"
    compact = re.sub(r"\b(?:[A-Za-z_][\w]*\.)+([A-Za-z_][\w]*)", r"\1", compact)
    compact = compact.replace(" | ", "|")
    compact = compact.replace(", ", ",")
    return _TYPE_REWRITE_MAP.get(compact, compact)


def _truncate_words(text: str, max_words: int) -> str:
    words = [w for w in text.strip().split() if w]
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]) + "..."


def _clean_sentence(text: str) -> str:
    line = re.split(r"[\r\n]+", text.strip(), maxsplit=1)[0]
    line = line.replace("`", "").strip(" .:-")
    return re.sub(r"\s+", " ", line)


def _query_terms(query: str) -> set[str]:
    return {term for term in re.findall(r"[a-zA-Z0-9_]+", query.lower()) if len(term) > 1}


# ---------------------------------------------------------------------------
# SIFEntry — dados SIF de uma skill
# ---------------------------------------------------------------------------


@dataclass
class SIFEntry:
    """Metadados SIF de uma skill individual."""

    name: str
    signature: str = ""
    short_sig: str = ""
    prompt_hint: str = ""
    codex: str = ""
    impl: str = ""
    compose: list[str] = field(default_factory=list)
    examples_min: list[str] = field(default_factory=list)
    factory_fn: Callable | None = field(default=None, repr=False)

    @property
    def has_codex(self) -> bool:
        return bool(self.codex.strip())

    @property
    def has_impl(self) -> bool:
        return bool(self.impl.strip())

    @property
    def has_prompt_hint(self) -> bool:
        return bool(self.prompt_hint.strip())

    @property
    def is_callable(self) -> bool:
        return self.has_codex or self.has_impl

    @property
    def runtime_name(self) -> str:
        return _sanitize_callable_name(self.name)

    @property
    def return_type(self) -> str:
        if "->" not in self.signature:
            return "Any"
        return _compact_type_name(self.signature.split("->", 1)[1])

    @property
    def display_sig(self) -> str:
        if self.short_sig:
            return self.short_sig
        if self.signature:
            sig = self.signature.strip()
            if "->" in sig:
                left, right = sig.split("->", 1)
                sig = f"{left.strip()}→{_compact_type_name(right)}"
            return sig.replace(": ", ":").replace(" = ", "=").replace(", ", ",")
        return self.name + "()"


# ---------------------------------------------------------------------------
# SIFFactory — compila callables a partir de impl/codex
# ---------------------------------------------------------------------------


class SIFFactory:
    """Compila SIFEntries em callables Python e injeta no REPL."""

    _compiled: dict[str, Callable] = {}
    _compile_meta: dict[str, dict[str, Any]] = {}
    _usage_stats: dict[str, dict[str, Any]] = {}
    _lock = threading.RLock()

    @classmethod
    def _record_compile(cls, entry: SIFEntry, source: str) -> None:
        cls._compile_meta[entry.name] = {
            "runtime_name": entry.runtime_name,
            "source": source,
            "compiled_at": time.time(),
        }
        cls._usage_stats.setdefault(entry.name, {
            "runtime_name": entry.runtime_name,
            "source": source,
            "compile_count": 0,
            "call_count": 0,
            "last_compiled_at": None,
            "last_called_at": None,
        })
        cls._usage_stats[entry.name]["source"] = source
        cls._usage_stats[entry.name]["compile_count"] += 1
        cls._usage_stats[entry.name]["last_compiled_at"] = time.time()

    @classmethod
    def _wrap_callable(cls, entry: SIFEntry, fn: Callable, source: str) -> Callable:
        @functools.wraps(fn)
        def tracked(*args, **kwargs):
            stats = cls._usage_stats.setdefault(entry.name, {
                "runtime_name": entry.runtime_name,
                "source": source,
                "compile_count": 0,
                "call_count": 0,
                "last_compiled_at": None,
                "last_called_at": None,
            })
            telemetry = _get_telemetry()
            started = time.perf_counter()
            stats["call_count"] += 1
            stats["last_called_at"] = time.time()
            args_preview = _truncate_words(repr(args[:2] if args else kwargs), 20)
            try:
                result = fn(*args, **kwargs)
                telemetry.record_call(
                    skill_name=entry.name,
                    success=True,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    args_preview=args_preview,
                )
                return result
            except Exception as exc:
                telemetry.record_call(
                    skill_name=entry.name,
                    success=False,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    args_preview=args_preview,
                    error=str(exc),
                    utility_hit=False,
                )
                raise

        return tracked

    @classmethod
    def compile(cls, entry: SIFEntry) -> Callable | None:
        if not entry.name.strip():
            raise ValueError("SIFEntry.name não pode ser vazio")
        if not entry.is_callable:
            return None

        with cls._lock:
            cached = cls._compiled.get(entry.name)
            if cached is not None:
                return cached

            if entry.has_codex:
                try:
                    codex_src = entry.codex.strip()
                    _codex_code = compile(
                        ast.parse(codex_src, mode="eval"),
                        "<sif-codex>",
                        "eval",
                    )
                    fn = eval(_codex_code, {"__builtins__": _SAFE_BUILTINS}, {})  # noqa: S307
                    if callable(fn):
                        wrapped = cls._wrap_callable(entry, fn, "codex")
                        cls._compiled[entry.name] = wrapped
                        cls._record_compile(entry, "codex")
                        sif_log.debug(f"SIF Codex: '{entry.name}' compilado via eval seguro")
                        return wrapped
                    sif_log.warn(f"SIF codex para '{entry.name}' não produziu callable")
                except SyntaxError as e:
                    sif_log.warn(f"SIF codex '{entry.name}' syntax error: {e}")
                except Exception as e:
                    sif_log.warn(f"SIF codex '{entry.name}' eval error: {e}")
                if not entry.has_impl:
                    return None

            if entry.has_impl:
                try:
                    code = textwrap.dedent(entry.impl)
                    ast.parse(code)
                    ns: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}
                    exec(compile(code, f"<sif:{entry.name}>", "exec"), ns, ns)  # noqa: S102
                    fn = ns.get(entry.name) or next(
                        (v for v in ns.values() if callable(v) and not isinstance(v, type)),
                        None,
                    )
                    if fn is None:
                        sif_log.warn(f"SIF impl '{entry.name}' não define callable")
                        return None
                    wrapped = cls._wrap_callable(entry, fn, "impl")
                    cls._compiled[entry.name] = wrapped
                    cls._record_compile(entry, "impl")
                    sif_log.debug(f"SIF impl: '{entry.name}' compilado via exec seguro")
                    return wrapped
                except SyntaxError as e:
                    sif_log.warn(f"SIF impl '{entry.name}' syntax error: {e}")
                except Exception as e:
                    sif_log.warn(f"SIF impl '{entry.name}' exec error: {e}")

        return None

    @classmethod
    def inject_all(
        cls,
        entries: list[SIFEntry],
        repl_locals: dict,
        overwrite: bool = False,
    ) -> dict[str, Callable]:
        injected: dict[str, Callable] = {}
        for entry in entries:
            if not entry.is_callable:
                continue

            var_name = entry.runtime_name

            if var_name in repl_locals and not overwrite:
                existing = repl_locals.get(var_name)
                if existing is not entry.factory_fn:
                    sif_log.warn(
                        f"SIF: colisão de nome '{var_name}' no REPL; skill '{entry.name}' ignorada"
                    )
                else:
                    sif_log.debug(f"SIF: '{var_name}' já no REPL, pulando (overwrite=False)")
                continue

            fn = cls.compile(entry)
            if fn is not None:
                repl_locals[var_name] = fn
                entry.factory_fn = fn
                injected[var_name] = fn
                sif_log.info(f"SIF Factory: injetou `{var_name}()` no REPL")

        return injected

    @classmethod
    def get_stats(cls) -> dict[str, dict[str, Any]]:
        return {name: dict(stats) for name, stats in cls._usage_stats.items()}

    @classmethod
    def reset_stats(cls) -> None:
        cls._usage_stats.clear()
        cls._compile_meta.clear()

    @classmethod
    def clear_cache(cls) -> None:
        with cls._lock:
            cls._compiled.clear()
            cls._compile_meta.clear()


def _validate_skill_entry_consistency(skill: SkillDef) -> str:
    entry = skill.sif_entry  # type: ignore[attr-defined]
    if entry is not None and entry.name and entry.name != skill.name:
        raise ValueError(
            f"SIF inconsistente: skill.name='{skill.name}' != sif_entry.name='{entry.name}'"
        )
    return entry.name if entry is not None and entry.name else skill.name


def _score_skill_for_query(skill: SkillDef, query: str) -> int:
    if not query.strip():
        return 0
    terms = _query_terms(query)
    score = 0
    if skill.name.lower() in query.lower():
        score += 5
    for tag in skill.tags:
        tag_lower = tag.lower()
        if tag_lower in query.lower():
            score += 3
        elif tag_lower in terms:
            score += 2
    desc_terms = _query_terms(skill.description)
    score += len(terms & desc_terms)
    return score


def _default_prompt_hint(skill: SkillDef) -> str:
    entry = skill.sif_entry  # type: ignore[attr-defined]
    if entry is not None and entry.has_prompt_hint:
        return entry.prompt_hint.strip()

    if entry is not None and entry.examples_min:
        return _truncate_words(entry.examples_min[0], 12)

    sentence = _clean_sentence(skill.description)
    if sentence:
        return _truncate_words(sentence, 10)

    tags = ",".join(skill.tags[:3])
    if tags:
        return f"use for {tags}"

    if entry is not None:
        return f"call directly; returns {entry.return_type}"

    return "use via skill_doc when needed"


class SIFCodexBuilder:
    """Builder legado para expor codex executável em modo debug."""

    HEADER = (
        "# SIF Codex Debug — executáveis inline (não recomendado no prompt padrão)\n"
        "# Use apenas para inspeção/debug. Runtime continua compilando no REPL.\n"
    )

    @classmethod
    def build(cls, skill_defs: list[SkillDef]) -> str:
        entries_with_codex = []
        for skill in skill_defs:
            entry = skill.sif_entry  # type: ignore[attr-defined]
            if entry is not None and entry.has_codex:
                name = _validate_skill_entry_consistency(skill)
                entries_with_codex.append((name, entry))
        if not entries_with_codex:
            return ""

        max_name = max(len(name) for name, _ in entries_with_codex)
        lines = [cls.HEADER]
        for name, entry in entries_with_codex:
            lines.append(f"{name:<{max_name}} = {entry.codex.strip()}")
        lines.append("")
        return "\n".join(lines)

    @classmethod
    def estimate_tokens(cls, skill_defs: list[SkillDef]) -> int:
        return len(cls.build(skill_defs)) // 4


class SIFHintBuilder:
    """Gera hints semânticos compactos para o prompt do LLM."""

    HEADER = (
        "# SIF Guide v3 — hints curtos para decidir e chamar skills\n"
        "# Priorize chamadas diretas; use skill_doc(name) só se faltar detalhe.\n"
    )

    @classmethod
    def select_focus_skills(
        cls,
        skill_defs: list[SkillDef],
        query: str = "",
        max_skills: int = 8,
    ) -> list[SkillDef]:
        if not skill_defs:
            return []

        graph = SIFCompositionGraph.build(skill_defs)
        scored = []
        for skill in skill_defs:
            degree = len(graph.get(skill.name, []))
            score = _score_skill_for_query(skill, query)
            if score > 0:
                scored.append((score + degree, skill))

        if not scored:
            fallback = []
            for skill in skill_defs:
                entry = skill.sif_entry  # type: ignore[attr-defined]
                degree = len(graph.get(skill.name, []))
                runtime_bonus = 2 if entry is not None and entry.is_callable else 0
                fallback.append((degree + runtime_bonus, skill.name, skill))
            fallback.sort(key=lambda item: (-item[0], item[1]))
            return [item[2] for item in fallback[:max_skills]]

        scored.sort(key=lambda item: (-item[0], item[1].name))
        chosen: list[SkillDef] = []
        chosen_names: set[str] = set()
        for _, skill in scored:
            if skill.name not in chosen_names:
                chosen.append(skill)
                chosen_names.add(skill.name)
            if len(chosen) >= max_skills:
                break

        for skill in list(chosen):
            for neighbor in graph.get(skill.name, []):
                if neighbor in chosen_names:
                    continue
                matched = next((candidate for candidate in skill_defs if candidate.name == neighbor), None)
                if matched is None:
                    continue
                chosen.append(matched)
                chosen_names.add(neighbor)
                if len(chosen) >= max_skills:
                    return chosen

        return chosen

    @classmethod
    def build(
        cls,
        skill_defs: list[SkillDef],
        query: str = "",
        max_skills: int = 8,
        allow_partial_compose: bool = False,
    ) -> str:
        focus = cls.select_focus_skills(skill_defs, query=query, max_skills=max_skills)
        if not focus:
            return ""

        max_name = max(len(skill.name) for skill in focus)
        visible_names = {skill.name for skill in skill_defs}
        lines = [cls.HEADER]
        for skill in focus:
            entry = skill.sif_entry  # type: ignore[attr-defined]
            name = _validate_skill_entry_consistency(skill)
            hint = _default_prompt_hint(skill)
            tail: list[str] = []
            if entry is not None:
                tail.append(f"out:{entry.return_type}")
                visible_compose = [
                    target for target in entry.compose[:2]
                    if not allow_partial_compose or target in visible_names
                ]
                if visible_compose:
                    tail.append("+" + ",".join(visible_compose))
                if entry.examples_min:
                    tail.append("ex:" + _truncate_words(entry.examples_min[0], 6))
            lines.append(f"{name:<{max_name}} : {hint}; {'; '.join(tail)}" if tail else f"{name:<{max_name}} : {hint}")
        lines.append("")
        return "\n".join(lines)


class SIFCompositionGraph:
    """Validação, visualização compacta e planejamento simples do grafo compose."""

    @classmethod
    def _ordered_targets(cls, source_skill: str, targets: list[str]) -> list[str]:
        telemetry = _get_telemetry()
        weighted_targets = telemetry.get_weighted_transition_targets(source_skill)
        return sorted(
            targets,
            key=lambda target: (
                -float(weighted_targets.get(target, {}).get("weighted_score", 0.0)),
                -telemetry.get_transition_score(source_skill, target),
                target,
            ),
        )

    @classmethod
    def build(cls, skill_defs: list[SkillDef]) -> dict[str, list[str]]:
        names = {skill.name for skill in skill_defs}
        graph: dict[str, list[str]] = {skill.name: [] for skill in skill_defs}
        for skill in skill_defs:
            entry = skill.sif_entry  # type: ignore[attr-defined]
            if entry is None:
                continue
            for target in entry.compose:
                if target in names and target != skill.name and target not in graph[skill.name]:
                    graph[skill.name].append(target)
        return graph

    @classmethod
    def validate(cls, skill_defs: list[SkillDef]) -> dict[str, list[str]]:
        return cls.validate_subset(skill_defs, allow_missing_targets=False)

    @classmethod
    def validate_subset(
        cls,
        skill_defs: list[SkillDef],
        allow_missing_targets: bool = False,
    ) -> dict[str, list[str]]:
        names = {skill.name for skill in skill_defs}
        errors: dict[str, list[str]] = {}
        for skill in skill_defs:
            entry = skill.sif_entry  # type: ignore[attr-defined]
            if entry is None:
                continue
            problems: list[str] = []
            for target in entry.compose:
                if target not in names:
                    if allow_missing_targets:
                        continue
                    problems.append(f"compose target inexistente: {target}")
                elif target == skill.name:
                    problems.append("compose não pode apontar para si mesma")
            if problems:
                errors[skill.name] = problems
        return errors

    @classmethod
    def render_recipes(
        cls,
        skill_defs: list[SkillDef],
        query: str = "",
        limit: int = 4,
    ) -> str:
        graph = cls.build(skill_defs)
        telemetry = _get_telemetry()
        focus = SIFHintBuilder.select_focus_skills(skill_defs, query=query, max_skills=max(limit, 4))
        focus_names = {skill.name for skill in focus}
        recipes: list[str] = []
        for skill in focus:
            for target in cls._ordered_targets(skill.name, graph.get(skill.name, [])):
                if target not in focus_names:
                    continue
                transition = telemetry.get_weighted_transition_targets(skill.name).get(target, {})
                if transition:
                    recipe = (
                        f"{skill.name}->{target}"
                        f" [w={transition.get('weighted_score', 0.0):.2f}"
                        f", ok={transition.get('success_rate', 0.0):.2f}]"
                    )
                else:
                    recipe = f"{skill.name}->{target}"
                if recipe not in recipes:
                    recipes.append(recipe)
                if len(recipes) >= limit:
                    break
            if len(recipes) >= limit:
                break
        if not recipes:
            return ""
        return "# SIF Recipes\n" + "\n".join(f"- {recipe}" for recipe in recipes) + "\n"

    @classmethod
    def plan(
        cls,
        skill_defs: list[SkillDef],
        goal: str,
        max_steps: int = 4,
    ) -> list[str]:
        focus = SIFHintBuilder.select_focus_skills(skill_defs, query=goal, max_skills=max_steps)
        if not focus:
            return []

        graph = cls.build(skill_defs)
        plan: list[str] = []
        seen: set[str] = set()

        for skill in focus:
            if skill.name in seen:
                continue
            plan.append(skill.name)
            seen.add(skill.name)
            if len(plan) >= max_steps:
                break
            for target in cls._ordered_targets(skill.name, graph.get(skill.name, [])):
                if target not in seen:
                    plan.append(target)
                    seen.add(target)
                if len(plan) >= max_steps:
                    break
            if len(plan) >= max_steps:
                break

        return plan[:max_steps]


class SIFTableBuilder:
    """Gera o contexto SIF v3 para o system prompt."""

    HEADER = (
        "# SIF v3 — índice compacto + hints semânticos + recipes\n"
        "# name | call_sig | when_tags | +composes\n"
    )

    @classmethod
    def build(
        cls,
        skill_defs: list[SkillDef],
        query: str = "",
        include_hints: bool = True,
        include_recipes: bool = True,
        include_codex: bool = False,
        allow_partial_compose: bool = False,
    ) -> str:
        if not skill_defs:
            return ""

        errors = SIFCompositionGraph.validate_subset(
            skill_defs,
            allow_missing_targets=allow_partial_compose,
        )
        if errors:
            detail = "; ".join(f"{name}: {', '.join(msgs)}" for name, msgs in errors.items())
            raise ValueError(f"SIF composition graph inválido: {detail}")

        rows: list[tuple[str, str, str, str]] = []
        for skill in skill_defs:
            entry = skill.sif_entry  # type: ignore[attr-defined]
            skill_name = _validate_skill_entry_consistency(skill)
            sig = entry.display_sig if entry is not None else (skill.name + "()")
            when = ",".join(skill.tags[:4]) if skill.tags else ""
            visible_compose = [
                c for c in (entry.compose[:3] if entry else [])
                if not allow_partial_compose or c in {s.name for s in skill_defs}
            ]
            compose = ",".join(f"+{c}" for c in visible_compose)

            if entry is not None and entry.is_callable:
                hint = "[fn]"
            elif skill.has_mcp:
                hint = "[MCP]"
            else:
                hint = ""

            suffix = f"{compose} {hint}".strip()
            rows.append((skill_name, sig, when, suffix))

        name_w = max(len("name"), *(len(row[0]) for row in rows))
        sig_w = max(len("call_sig"), *(len(row[1]) for row in rows))
        when_w = max(len("when_tags"), *(len(row[2]) for row in rows))

        lines = [cls.HEADER]
        for name, sig, when, suffix in rows:
            lines.append(f"{name:<{name_w}} | {sig:<{sig_w}} | {when:<{when_w}} | {suffix}".rstrip())

        result = "\n".join(lines) + "\n"

        if include_hints:
            hints = SIFHintBuilder.build(
                skill_defs,
                query=query,
                allow_partial_compose=allow_partial_compose,
            )
            if hints:
                result += "\n" + hints

        if include_recipes:
            recipes = SIFCompositionGraph.render_recipes(skill_defs, query=query)
            if recipes:
                result += "\n" + recipes

        if include_codex:
            codex = SIFCodexBuilder.build(skill_defs)
            if codex:
                result += "\n" + codex

        return result

    @classmethod
    def estimate_tokens(
        cls,
        skill_defs: list[SkillDef],
        query: str = "",
        include_hints: bool = True,
        include_recipes: bool = True,
        include_codex: bool = False,
        allow_partial_compose: bool = False,
    ) -> int:
        return len(
            cls.build(
                skill_defs,
                query=query,
                include_hints=include_hints,
                include_recipes=include_recipes,
                include_codex=include_codex,
                allow_partial_compose=allow_partial_compose,
            )
        ) // 4


class SIFSkillProxy:
    """Proxy callable que carrega a skill sob demanda no primeiro uso."""

    def __init__(self, entry: SIFEntry, skill_def: Any):
        self._entry = entry
        self._skill_def = skill_def
        self._resolved: Callable | None = None

    def __call__(self, *args, **kwargs):
        if self._resolved is None:
            self._resolved = SIFFactory.compile(self._entry)
            if self._resolved is None:
                raise NotImplementedError(
                    f"Skill '{self._entry.name}' não tem implementação SIF compilável. "
                    f"Use skill_doc('{self._entry.name}') para ver exemplos manuais."
                )
            sif_log.debug(f"SIFProxy: '{self._entry.name}' resolvido sob demanda")

        return self._resolved(*args, **kwargs)

    def __repr__(self) -> str:
        status = "compilado" if self._resolved else "proxy"
        return f"<SIFSkillProxy:{self._entry.name} [{status}]>"


def parse_sif_block(toml_data: dict) -> SIFEntry | None:
    """Extrai os campos SIF do frontmatter TOML de um SKILL.md."""
    sif_data = toml_data.get("sif", {})
    if not sif_data:
        return None

    name = str(toml_data.get("name", "")).strip()
    if not name:
        raise ValueError("SKILL.md com bloco [sif] precisa declarar 'name' no nível raiz")

    return SIFEntry(
        name=name,
        signature=str(sif_data.get("signature", "")),
        short_sig=str(sif_data.get("short_sig", "")),
        prompt_hint=str(sif_data.get("prompt_hint", sif_data.get("hint", ""))),
        codex=str(sif_data.get("codex", "")),
        impl=str(sif_data.get("impl", "")),
        compose=[str(c) for c in sif_data.get("compose", [])],
        examples_min=[str(example) for example in sif_data.get("examples_min", [])],
    )


def estimate_sif_vs_full(skill_defs: list[SkillDef], query: str = "") -> dict[str, int]:
    """Compara custo em tokens entre SIF v3 e contexto full."""
    from rlm.core.skill_loader import SkillLoader

    loader = SkillLoader()
    full_ctx = loader._build_full_context(skill_defs)
    sif_v3 = SIFTableBuilder.build(skill_defs, query=query)
    sif_table_only = SIFTableBuilder.build(
        skill_defs,
        query=query,
        include_hints=False,
        include_recipes=False,
        include_codex=False,
    )
    sif_hints_only = SIFHintBuilder.build(skill_defs, query=query)
    sif_codex_only = SIFCodexBuilder.build(skill_defs)

    full_t = len(full_ctx) // 4
    sif_v3_t = len(sif_v3) // 4
    table_t = len(sif_table_only) // 4
    hints_t = len(sif_hints_only) // 4
    codex_t = len(sif_codex_only) // 4

    return {
        "sif_v3_tokens": sif_v3_t,
        "sif_table_tokens": table_t,
        "sif_hint_tokens": hints_t,
        "sif_codex_tokens": codex_t,
        "full_tokens": full_t,
        "saving_pct": round((1 - sif_v3_t / max(full_t, 1)) * 100),
        "skills_with_codex": sum(1 for s in skill_defs if s.sif_entry and s.sif_entry.has_codex),
        "skills_with_impl": sum(1 for s in skill_defs if s.sif_entry and s.sif_entry.has_impl),
        "focus_skills": len(SIFHintBuilder.select_focus_skills(skill_defs, query=query)),
    }