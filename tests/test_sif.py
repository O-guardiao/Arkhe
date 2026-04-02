"""Tests — SIF v3 (Skill Interface Format)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rlm.core.skillkit.sif import (  # noqa: E402
    SIFCodexBuilder,
    SIFCompositionGraph,
    SIFEntry,
    SIFFactory,
    SIFHintBuilder,
    SIFSkillProxy,
    SIFTableBuilder,
    estimate_sif_vs_full,
    parse_sif_block,
)
from rlm.core.skillkit.skill_loader import SkillDef, SkillLoader  # noqa: E402
from rlm.core.skillkit.skill_loader import SkillRuntimeMeta  # noqa: E402
from rlm.core.skillkit.skill_telemetry import SkillTelemetryStore, get_skill_telemetry  # noqa: E402


def make_skill_with_sif(
    name: str,
    signature: str = "",
    short_sig: str = "",
    prompt_hint: str = "",
    codex: str = "",
    impl: str = "",
    compose: list[str] | None = None,
    tags: list[str] | None = None,
    priority: str = "contextual",
    body: str = "## Exemplos\n```python\nfoo()\n```\n",
    description: str | None = None,
    runtime: SkillRuntimeMeta | None = None,
) -> SkillDef:
    entry = SIFEntry(
        name=name,
        signature=signature or f"{name}(query: str) -> str",
        short_sig=short_sig,
        prompt_hint=prompt_hint,
        codex=codex,
        impl=impl,
        compose=compose or [],
    )
    return SkillDef(
        name=name,
        description=description or f"Skill de teste: {name}",
        body=body,
        tags=tags or [name, "teste"],
        priority=priority,
        sif_entry=entry,
        runtime=runtime or SkillRuntimeMeta(),
    )


SHELL_IMPL = """
def shell(cmd, capture=True, timeout=10):
    import subprocess, shlex
    args = shlex.split(cmd) if isinstance(cmd, str) else cmd
    return subprocess.run(args, capture_output=capture, text=True, timeout=timeout)
"""


class TestParseSifBlock:
    def test_retorna_none_sem_bloco_sif(self):
        assert parse_sif_block({"name": "shell"}) is None

    def test_parseia_prompt_hint(self):
        result = parse_sif_block({
            "name": "shell",
            "sif": {
                "signature": "shell(cmd: str) -> str",
                "prompt_hint": "run shell commands safely",
                "compose": ["notion"],
                "examples_min": ["ver logs do serviço"],
            },
        })
        assert result is not None
        assert result.prompt_hint == "run shell commands safely"
        assert result.compose == ["notion"]
        assert result.examples_min == ["ver logs do serviço"]

    def test_compose_converte_para_strings(self):
        result = parse_sif_block({"name": "test", "sif": {"compose": [1, "x"]}})
        assert result is not None
        assert result.compose == ["1", "x"]

    def test_name_vazio_gera_erro(self):
        with pytest.raises(ValueError):
            parse_sif_block({"sif": {"signature": "x() -> None"}})


class TestSIFEntry:
    def test_display_sig_usa_short_sig(self):
        entry = SIFEntry(name="shell", short_sig="shell(cmd)→CP", signature="shell(cmd: str) -> str")
        assert entry.display_sig == "shell(cmd)→CP"

    def test_display_sig_compacta_tipos_genericos(self):
        entry = SIFEntry(name="f", signature="f(x: int) -> dict[str, list[int]]")
        assert entry.display_sig == "f(x:int)→dict[str,list[int]]"

    def test_runtime_name_normaliza(self):
        entry = SIFEntry(name="123-web-search")
        assert entry.runtime_name == "skill_123_web_search"


class TestSIFFactoryCompile:
    def setup_method(self):
        SIFFactory.clear_cache()
        SIFFactory.reset_stats()
        get_skill_telemetry().reset()

    def test_compila_impl(self):
        entry = SIFEntry(name="add", impl="def add(a, b): return a + b")
        fn = SIFFactory.compile(entry)
        assert fn is not None
        assert fn(2, 3) == 5

    def test_compila_codex(self):
        entry = SIFEntry(name="sub", codex="lambda a,b: a - b")
        fn = SIFFactory.compile(entry)
        assert fn is not None
        assert fn(9, 2) == 7

    def test_usa_cache_na_segunda_chamada(self):
        entry = SIFEntry(name="cached", impl="def cached(x): return x * 2")
        fn1 = SIFFactory.compile(entry)
        fn2 = SIFFactory.compile(entry)
        assert fn1 is fn2

    def test_clear_cache_remove_compilados(self):
        entry = SIFEntry(name="to_clear", impl="def to_clear(): return 42")
        SIFFactory.compile(entry)
        assert "to_clear" in SIFFactory._compiled
        SIFFactory.clear_cache()
        assert "to_clear" not in SIFFactory._compiled

    def test_sandbox_bloqueia_open_no_codex(self):
        entry = SIFEntry(name="unsafe", codex="lambda: open('x.txt').read()")
        fn = SIFFactory.compile(entry)
        assert fn is not None
        with pytest.raises(NameError):
            fn()

    def test_get_stats_registra_compilacao_e_chamada(self):
        entry = SIFEntry(name="stats_fn", codex="lambda x: x + 1")
        fn = SIFFactory.compile(entry)
        assert fn is not None
        assert fn(2) == 3
        stats = SIFFactory.get_stats()
        assert stats["stats_fn"]["compile_count"] == 1
        assert stats["stats_fn"]["call_count"] == 1
        assert stats["stats_fn"]["source"] == "codex"


class TestSIFFactoryInjectAll:
    def setup_method(self):
        SIFFactory.clear_cache()
        SIFFactory.reset_stats()
        get_skill_telemetry().reset()

    def test_injeta_callables(self):
        entries = [
            SIFEntry(name="add2", impl="def add2(a, b): return a + b"),
            SIFEntry(name="mul2", impl="def mul2(a, b): return a * b"),
        ]
        ns: dict = {}
        injected = SIFFactory.inject_all(entries, ns)
        assert sorted(injected.keys()) == ["add2", "mul2"]
        assert ns["add2"](2, 3) == 5

    def test_nao_sobrescreve_namespace_por_padrao(self):
        ns: dict = {"web_search": lambda: "original"}
        entry = SIFEntry(name="web-search", impl="def web_search(): return 'novo'")
        SIFFactory.inject_all([entry], ns, overwrite=False)
        assert ns["web_search"]() == "original"


class TestSIFHintBuilder:
    def test_usa_prompt_hint_quando_disponivel(self):
        skill = make_skill_with_sif(
            "shell",
            prompt_hint="run terminal commands and capture output",
            tags=["terminal", "deploy"],
        )
        result = SIFHintBuilder.build([skill], query="deploy app")
        assert "run terminal commands" in result

    def test_foco_por_query_prioriza_skill_relacionada(self):
        shell = make_skill_with_sif("shell", prompt_hint="run terminal commands", tags=["terminal", "deploy"])
        weather = make_skill_with_sif("weather", prompt_hint="check weather", tags=["clima"])
        focus = SIFHintBuilder.select_focus_skills([shell, weather], query="deploy terminal")
        assert focus[0].name == "shell"


class TestSIFCompositionGraph:
    def test_validate_detecta_alvo_inexistente(self):
        skill = make_skill_with_sif("shell", compose=["missing"])
        errors = SIFCompositionGraph.validate([skill])
        assert "shell" in errors

    def test_plan_usa_query_e_compose(self):
        shell = make_skill_with_sif("shell", prompt_hint="run terminal commands", tags=["terminal"], compose=["notion"])
        notion = make_skill_with_sif("notion", prompt_hint="write documentation", tags=["docs"])
        plan = SIFCompositionGraph.plan([shell, notion], goal="usar terminal e documentar", max_steps=3)
        assert plan[0] == "shell"
        assert "notion" in plan

    def test_plan_prioriza_compose_com_transicao_observada(self):
        telemetry = get_skill_telemetry()
        shell = make_skill_with_sif(
            "shell",
            prompt_hint="run terminal commands",
            tags=["terminal"],
            compose=["notion", "browser"],
        )
        notion = make_skill_with_sif("notion", prompt_hint="write documentation", tags=["docs"])
        browser = make_skill_with_sif("browser", prompt_hint="inspect web pages", tags=["web"])

        telemetry.record_call(skill_name="shell", success=True, latency_ms=8, session_id="sess-1")
        telemetry.record_call(skill_name="browser", success=True, latency_ms=11, session_id="sess-1")
        telemetry.record_call(skill_name="shell", success=True, latency_ms=7, session_id="sess-2")
        telemetry.record_call(skill_name="browser", success=True, latency_ms=12, session_id="sess-2")

        plan = SIFCompositionGraph.plan([shell, notion, browser], goal="usar terminal", max_steps=3)

        assert plan[0] == "shell"
        assert plan[1] == "browser"

    def test_plan_prioriza_compose_por_score_ponderado(self):
        telemetry = get_skill_telemetry()
        shell = make_skill_with_sif(
            "shell",
            prompt_hint="run terminal commands",
            tags=["terminal"],
            compose=["notion", "browser"],
        )
        notion = make_skill_with_sif("notion", prompt_hint="write documentation", tags=["docs"])
        browser = make_skill_with_sif("browser", prompt_hint="inspect web pages", tags=["web"])

        telemetry.record_call(skill_name="shell", success=True, latency_ms=8, session_id="sess-1")
        telemetry.record_call(skill_name="notion", success=True, latency_ms=1200, utility_hit=False, session_id="sess-1")
        telemetry.record_call(skill_name="shell", success=True, latency_ms=7, session_id="sess-2")
        telemetry.record_call(skill_name="browser", success=True, latency_ms=20, utility_hit=True, session_id="sess-2")

        plan = SIFCompositionGraph.plan([shell, notion, browser], goal="usar terminal", max_steps=3)

        assert plan[0] == "shell"
        assert plan[1] == "browser"


class TestSIFTableBuilder:
    def test_retorna_vazio_para_lista_vazia(self):
        assert SIFTableBuilder.build([]) == ""

    def test_saida_contem_header_e_nome(self):
        table = SIFTableBuilder.build([make_skill_with_sif("shell")])
        assert "SIF v3" in table
        assert "shell" in table

    def test_tabela_tem_colunas_dinamicas(self):
        skills = [
            make_skill_with_sif("skill_a", short_sig="a()→str"),
            make_skill_with_sif("very_long_skill_name", short_sig="very_long_skill_name(path: str, mode: str = 'x')→dict[str,list[int]]"),
        ]
        table = SIFTableBuilder.build(skills, include_hints=False, include_recipes=False)
        rows = [line for line in table.splitlines() if line and not line.startswith("#")]
        assert len(rows) == 2
        assert rows[0].count("|") == rows[1].count("|") == 3

    def test_prompt_padrao_inclui_hints_e_recipes(self):
        shell = make_skill_with_sif("shell", prompt_hint="run terminal commands", compose=["notion"], tags=["terminal"])
        notion = make_skill_with_sif("notion", prompt_hint="write notes", tags=["docs"])
        result = SIFTableBuilder.build([shell, notion], query="terminal docs")
        assert "SIF Guide v3" in result
        assert "SIF Recipes" in result
        assert "shell->notion" in result

    def test_codex_nao_aparece_por_padrao(self):
        skill = make_skill_with_sif("fn", codex="lambda: 42")
        result = SIFTableBuilder.build([skill])
        assert "lambda: 42" not in result

    def test_include_codex_explicito_funciona(self):
        skill = make_skill_with_sif("fn", codex="lambda: 42")
        result = SIFTableBuilder.build([skill], include_codex=True)
        assert "lambda: 42" in result

    def test_subset_parcial_pode_omitir_compose_targets(self):
        shell = make_skill_with_sif("shell", prompt_hint="run terminal commands", compose=["notion"])
        result = SIFTableBuilder.build([shell], allow_partial_compose=True)
        assert "shell" in result
        assert "+notion" not in result

    def test_estimate_tokens_retorna_inteiro(self):
        tokens = SIFTableBuilder.estimate_tokens([make_skill_with_sif("x")])
        assert isinstance(tokens, int)
        assert tokens > 0


class TestSIFCodexBuilder:
    def test_retorna_vazio_sem_codex(self):
        assert SIFCodexBuilder.build([make_skill_with_sif("x")]) == ""

    def test_inclui_codex_quando_existe(self):
        skill = make_skill_with_sif("shell", codex="lambda cmd: cmd")
        result = SIFCodexBuilder.build([skill])
        assert "shell" in result
        assert "lambda cmd: cmd" in result


class TestSIFSkillProxy:
    def setup_method(self):
        SIFFactory.clear_cache()
        SIFFactory.reset_stats()

    def test_proxy_resolve_na_primeira_chamada(self):
        entry = SIFEntry(name="proxy_fn", impl="def proxy_fn(x): return x + 1")
        proxy = SIFSkillProxy(entry, object())
        assert proxy._resolved is None
        assert proxy(5) == 6
        assert proxy._resolved is not None

    def test_proxy_raise_sem_impl(self):
        proxy = SIFSkillProxy(SIFEntry(name="no_impl"), object())
        with pytest.raises(NotImplementedError):
            proxy()


class TestSkillLoaderSIFIntegration:
    def setup_method(self):
        SIFFactory.clear_cache()
        SIFFactory.reset_stats()
        get_skill_telemetry().reset()

    def test_build_system_prompt_context_modo_sif(self):
        loader = SkillLoader()
        shell = make_skill_with_sif("shell", prompt_hint="run shell commands", tags=["terminal"])
        weather = make_skill_with_sif("weather", prompt_hint="check weather", tags=["clima"])
        result = loader.build_system_prompt_context([shell, weather], query="terminal", mode="sif")
        assert "SIF v3" in result
        assert "SIF Guide v3" in result

    def test_inject_sif_callables_injeta_helpers(self):
        loader = SkillLoader()
        skill = make_skill_with_sif("codex_fn", codex="lambda a,b: a - b")
        ns: dict = {}
        injected = loader.inject_sif_callables([skill], ns)
        assert "codex_fn" in injected
        assert "sif_stats" in ns
        assert "sif_plan" in ns
        assert ns["codex_fn"](10, 3) == 7
        assert ns["sif_plan"]("subtrair numeros", max_steps=2) == ["codex_fn"]

    def test_build_system_prompt_context_modo_micro(self):
        loader = SkillLoader()
        shell = make_skill_with_sif("shell", prompt_hint="run shell commands", tags=["terminal"], priority="always")
        notion = make_skill_with_sif("notion", prompt_hint="write notes", tags=["docs"])
        weather = make_skill_with_sif("weather", prompt_hint="check weather", tags=["clima"])
        result = loader.build_system_prompt_context([shell, notion, weather], query="oi", mode="micro")
        assert "Skills Micro" in result
        assert "SIF v3" in result
        assert "shell" in result

    def test_build_system_prompt_context_modo_focused(self):
        loader = SkillLoader()
        shell = make_skill_with_sif(
            "shell",
            prompt_hint="run shell commands",
            tags=["terminal", "deploy"],
            compose=["notion", "github"],
            priority="always",
        )
        notion = make_skill_with_sif("notion", prompt_hint="write notes", tags=["docs", "documentar"])
        github = make_skill_with_sif("github", prompt_hint="publish updates", tags=["github", "repo"])

        result = loader.build_system_prompt_context(
            [shell, notion, github],
            query="deploy e documentar no notion",
            mode="focused",
        )

        assert "Skills Focused" in result
        assert "SIF v3" in result
        assert "Skills Ativas (focused)" in result
        assert "### shell" in result

    def test_auto_trivial_faz_fallback_para_micro(self):
        loader = SkillLoader()
        shell = make_skill_with_sif("shell", prompt_hint="run shell commands", tags=["terminal"], priority="always")
        notion = make_skill_with_sif("notion", prompt_hint="write notes", tags=["docs"])
        result = loader.build_system_prompt_context([shell, notion], query="obrigado", mode="auto")
        assert "Skills Micro" in result
        assert "Skills Ativas" not in result

    def test_rank_skills_usa_telemetria_custo_e_hint(self):
        loader = SkillLoader()
        telemetry = get_skill_telemetry()
        shell = make_skill_with_sif(
            "shell",
            prompt_hint="run terminal deploy commands",
            tags=["terminal", "deploy"],
            runtime=SkillRuntimeMeta(estimated_cost=0.4, risk_level="medium"),
        )
        browser = make_skill_with_sif(
            "browser",
            prompt_hint="read webpages",
            tags=["web", "http"],
            runtime=SkillRuntimeMeta(estimated_cost=2.5, risk_level="low"),
        )
        telemetry.record_call(skill_name="shell", success=True, latency_ms=10)
        telemetry.record_call(skill_name="shell", success=True, latency_ms=12)
        telemetry.record_call(skill_name="browser", success=False, latency_ms=20, utility_hit=False)
        ranked = loader.rank_skills([browser, shell], query="fazer deploy no terminal")
        assert ranked[0].skill.name == "shell"
        assert ranked[0].telemetry_score > ranked[1].telemetry_score

    def test_rank_skills_usa_recuperacao_lexical_de_traces(self):
        loader = SkillLoader()
        telemetry = get_skill_telemetry()
        shell = make_skill_with_sif("shell", tags=["ops"])
        browser = make_skill_with_sif("browser", tags=["ops"])

        telemetry.record_call(
            skill_name="shell",
            success=True,
            latency_ms=10,
            session_id="sess-1",
            query="fazer deploy no terminal de producao",
        )
        telemetry.record_call(
            skill_name="browser",
            success=True,
            latency_ms=12,
            session_id="sess-2",
            query="navegar em pagina web publica",
        )

        ranked = loader.rank_skills([browser, shell], query="deploy no terminal")

        assert ranked[0].skill.name == "shell"
        assert ranked[0].trace_score > ranked[1].trace_score

    def test_request_context_helpers_controlam_contexto(self):
        loader = SkillLoader()
        telemetry = get_skill_telemetry()
        tokens = loader.set_request_context(session_id="sess-1", client_id="cli-1", query="deploy")
        assert telemetry.current_context()["session_id"] == "sess-1"
        loader.clear_request_context(tokens)
        assert telemetry.current_context()["session_id"] == ""

    def test_telemetria_filtra_eventos_por_skill_e_tipo(self):
        telemetry = get_skill_telemetry()
        telemetry.record_routing(
            mode="auto",
            query="deploy",
            ranked_skills=[{"skill": "shell", "score": 1.2}],
            selected_skills=["shell"],
            session_id="sess-1",
        )
        telemetry.record_call(skill_name="shell", success=True, latency_ms=11, session_id="sess-1")
        telemetry.record_call(skill_name="browser", success=False, latency_ms=17, session_id="sess-2")

        shell_events = telemetry.get_recent_events(skill_name="shell")
        call_events = telemetry.get_recent_events(event_type="call")
        session_events = telemetry.get_recent_events(session_id="sess-1")

        assert len(shell_events) == 1
        assert shell_events[0]["skill_name"] == "shell"
        assert len(call_events) == 2
        assert len(session_events) == 2

    def test_telemetria_retorna_summary_e_report_por_skill(self):
        telemetry = get_skill_telemetry()
        telemetry.record_routing(
            mode="auto",
            query="deploy",
            ranked_skills=[{"skill": "shell", "score": 1.3}],
            selected_skills=["shell"],
        )
        telemetry.record_call(skill_name="shell", success=True, latency_ms=9)

        summary = telemetry.get_summary(include_recent=True, limit=5)
        report = telemetry.get_skill_report("shell", limit=5)

        assert summary["tracked_skills"] == 1
        assert summary["route_events"] == 1
        assert summary["call_events"] == 1
        assert summary["transition_edges"] == 0
        assert len(summary["recent_events"]) == 2
        assert report["skill"] == "shell"
        assert report["stats"]["success_rate"] == 1.0
        assert len(report["recent_events"]) == 1

    def test_telemetria_registra_transicoes_por_sessao(self):
        telemetry = get_skill_telemetry()
        telemetry.record_call(skill_name="shell", success=True, latency_ms=10, session_id="sess-1")
        telemetry.record_call(skill_name="browser", success=True, latency_ms=14, session_id="sess-1")
        telemetry.record_call(skill_name="shell", success=True, latency_ms=9, session_id="sess-2")
        telemetry.record_call(skill_name="browser", success=False, latency_ms=20, session_id="sess-2")

        transitions = telemetry.get_transition_targets("shell")
        summary = telemetry.get_summary()
        report = telemetry.get_skill_report("shell")

        assert transitions == {"browser": 2}
        assert telemetry.get_transition_score("shell", "browser") == 2
        assert summary["transition_edges"] == 1
        assert summary["transitions"]["shell"]["browser"] == 2
        assert report["transitions"]["browser"] == 2

    def test_telemetria_retorna_relatorio_de_compose_global_e_por_sessao(self):
        telemetry = get_skill_telemetry()
        telemetry.record_call(skill_name="shell", success=True, latency_ms=10, session_id="sess-1")
        telemetry.record_call(skill_name="browser", success=True, latency_ms=12, session_id="sess-1")
        telemetry.record_call(skill_name="shell", success=True, latency_ms=9, session_id="sess-2")
        telemetry.record_call(skill_name="notion", success=True, latency_ms=11, session_id="sess-2")

        global_report = telemetry.get_transition_report(limit=5)
        session_report = telemetry.get_transition_report(session_id="sess-1", limit=5)

        assert global_report["transition_edges"] == 2
        assert global_report["top_edges"][0]["source"] == "shell"
        assert session_report["session_id"] == "sess-1"
        assert session_report["transition_edges"] == 1
        assert session_report["transitions"] == {"shell": {"browser": 1}}

    def test_telemetria_recupera_traces_relevantes_por_query(self):
        telemetry = get_skill_telemetry()
        telemetry.record_call(
            skill_name="shell",
            success=True,
            latency_ms=10,
            session_id="sess-1",
            query="fazer deploy no terminal",
        )
        telemetry.record_call(
            skill_name="browser",
            success=True,
            latency_ms=11,
            session_id="sess-2",
            query="abrir pagina no navegador",
        )

        matches = telemetry.get_relevant_traces("deploy terminal", limit=5)

        assert matches[0]["skill_name"] == "shell"
        assert matches[0]["retrieval_score"] > 0

    def test_loader_persiste_historical_reliability_derivada_da_telemetria(self, tmp_path):
        loader = SkillLoader(quality_store_path=tmp_path / "skill_quality.json")
        telemetry = get_skill_telemetry()
        shell = make_skill_with_sif("shell")

        telemetry.record_call(skill_name="shell", success=True, latency_ms=10)
        telemetry.record_call(skill_name="shell", success=True, latency_ms=11)
        telemetry.record_call(skill_name="shell", success=False, latency_ms=13)

        changed = loader.update_historical_reliability_from_telemetry([shell], persist=True)

        assert changed is True
        assert shell.runtime.historical_reliability == 0.6
        reloaded = SkillLoader(quality_store_path=tmp_path / "skill_quality.json")
        reloaded_skill = make_skill_with_sif("shell")
        reloaded._apply_historical_quality(reloaded_skill)
        assert reloaded_skill.runtime.historical_reliability == 0.6

    def test_plan_prompt_context_atualiza_historical_reliability_antes_do_ranking(self, tmp_path):
        loader = SkillLoader(quality_store_path=tmp_path / "skill_quality.json")
        telemetry = get_skill_telemetry()
        shell = make_skill_with_sif("shell", tags=["terminal", "deploy"])
        browser = make_skill_with_sif("browser", tags=["web"])

        telemetry.record_call(skill_name="shell", success=True, latency_ms=9)
        telemetry.record_call(skill_name="shell", success=True, latency_ms=10)
        telemetry.record_call(skill_name="browser", success=False, latency_ms=25)

        plan = loader.plan_prompt_context([shell, browser], query="deploy terminal", mode="auto")

        assert shell.runtime.historical_reliability == 0.75
        assert browser.runtime.historical_reliability == 0.3333
        assert plan.ranked_skills[0].skill.name == "shell"

    def test_plan_prompt_context_bloqueia_skill_sem_precondicao_de_env(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        loader = SkillLoader()
        github = make_skill_with_sif(
            "github",
            prompt_hint="criar issue ou consultar pull request",
            tags=["github", "issue", "pr"],
            runtime=SkillRuntimeMeta(preconditions=["env:GITHUB_TOKEN"]),
        )
        shell = make_skill_with_sif(
            "shell",
            prompt_hint="executar comandos no terminal",
            tags=["terminal", "deploy"],
        )

        plan = loader.plan_prompt_context([github, shell], query="criar issue no github", mode="auto")
        rendered = loader.build_system_prompt_context([github, shell], query="criar issue no github", mode="auto")

        assert all(skill.name != "github" for skill in plan.expanded_skills)
        assert plan.blocked_skills[0].skill.name == "github"
        assert "missing env GITHUB_TOKEN" in plan.blocked_skills[0].reasons[0]
        assert "github" in rendered
        assert "missing env GITHUB_TOKEN" in rendered

    def test_build_micro_context_ignora_skill_indisponivel(self, monkeypatch):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        loader = SkillLoader()
        slack = make_skill_with_sif(
            "slack",
            prompt_hint="publicar mensagem no slack",
            tags=["slack", "notificar"],
            runtime=SkillRuntimeMeta(preconditions=["env:SLACK_BOT_TOKEN"]),
            priority="always",
        )
        shell = make_skill_with_sif(
            "shell",
            prompt_hint="executar comandos no terminal",
            tags=["terminal"],
            priority="always",
        )

        rendered = loader.build_system_prompt_context([slack, shell], query="oi", mode="micro")

        assert "shell" in rendered
        assert "slack" not in rendered

    def test_estimate_tokens_inclui_modo_focused(self):
        loader = SkillLoader()
        shell = make_skill_with_sif("shell", prompt_hint="run shell commands", tags=["terminal", "deploy"])
        notion = make_skill_with_sif("notion", prompt_hint="write notes", tags=["docs"])

        estimate = loader.estimate_tokens([shell, notion], query="deploy e documentar")

        assert "focused_tokens" in estimate
        assert "focused_saving_pct" in estimate
        assert estimate["focused_tokens"] > 0


class TestEstimateSifVsFull:
    def test_retorna_metricas_v3(self):
        skills = [
            make_skill_with_sif("shell", prompt_hint="run shell commands", body="A" * 2000),
            make_skill_with_sif("notion", prompt_hint="write notes", body="B" * 2000),
        ]
        result = estimate_sif_vs_full(skills, query="shell")
        assert "sif_v3_tokens" in result
        assert "sif_hint_tokens" in result
        assert result["sif_v3_tokens"] < result["full_tokens"]


class TestSkillTelemetryPersistence:
    def test_replay_reconstroi_metricas_de_call_e_routing(self, tmp_path):
        trace_file = tmp_path / "skill_traces.jsonl"
        writer = SkillTelemetryStore(trace_path=trace_file, load_existing=False)

        writer.record_routing(
            mode="auto",
            query="deploy",
            ranked_skills=[{"skill": "shell", "score": 2.0}],
            selected_skills=["shell"],
            blocked_skills=[{"name": "github", "reasons": ["missing env GITHUB_TOKEN"]}],
            session_id="sess-1",
        )
        writer.record_call(skill_name="shell", success=True, latency_ms=10, session_id="sess-1")
        writer.record_call(skill_name="shell", success=False, latency_ms=20, session_id="sess-1")

        restored = SkillTelemetryStore(trace_path=trace_file, load_existing=True)
        stats = restored.get_skill_stats("shell")
        summary = restored.get_summary(include_recent=True, limit=5)

        assert stats["route_count"] == 1
        assert stats["call_count"] == 2
        assert stats["success_rate"] == 0.5
        assert summary["route_events"] == 1
        assert summary["call_events"] == 2
        assert len(summary["recent_events"]) == 3
        assert summary["recent_events"][0]["payload"]["blocked_skills"][0]["name"] == "github"

    def test_replay_reconstroi_transicoes_por_sessao(self, tmp_path):
        trace_file = tmp_path / "skill_traces.jsonl"
        writer = SkillTelemetryStore(trace_path=trace_file, load_existing=False)

        writer.record_call(skill_name="shell", success=True, latency_ms=8, session_id="sess-1")
        writer.record_call(skill_name="browser", success=True, latency_ms=11, session_id="sess-1")
        writer.record_call(skill_name="shell", success=True, latency_ms=7, session_id="sess-2")
        writer.record_call(skill_name="notion", success=True, latency_ms=9, session_id="sess-2")

        restored = SkillTelemetryStore(trace_path=trace_file, load_existing=True)

        assert restored.get_transition_score("shell", "browser") == 1
        assert restored.get_transition_score("shell", "notion") == 1
        assert restored.get_transition_targets("shell") == {"browser": 1, "notion": 1}

    def test_transition_insights_reconstroi_score_ponderado(self, tmp_path):
        trace_file = tmp_path / "skill_traces.jsonl"
        writer = SkillTelemetryStore(trace_path=trace_file, load_existing=False)

        writer.record_call(skill_name="shell", success=True, latency_ms=8, session_id="sess-1")
        writer.record_call(skill_name="browser", success=True, latency_ms=25, utility_hit=True, session_id="sess-1")
        writer.record_call(skill_name="shell", success=True, latency_ms=7, session_id="sess-2")
        writer.record_call(skill_name="notion", success=False, latency_ms=1500, utility_hit=False, session_id="sess-2")

        restored = SkillTelemetryStore(trace_path=trace_file, load_existing=True)
        weighted = restored.get_weighted_transition_targets("shell")

        assert list(weighted.keys())[0] == "browser"
        assert weighted["browser"]["weighted_score"] > weighted["notion"]["weighted_score"]


class TestSIFDiskIntegration:
    SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "rlm", "skills")

    @pytest.fixture
    def loader(self):
        return SkillLoader()

    @pytest.fixture
    def all_skills(self, loader):
        return loader.load_from_dir(self.SKILLS_DIR)

    def test_descobre_skills_com_sif_entry(self, all_skills):
        assert any(skill.sif_entry is not None for skill in all_skills)

    def test_sif_contexto_real_menor_que_full(self, loader, all_skills):
        full_ctx = loader._build_full_context(all_skills)
        sif_ctx = SIFTableBuilder.build(all_skills)
        assert len(sif_ctx) < len(full_ctx)

    def test_sif_contexto_real_contem_todos_os_nomes(self, all_skills):
        sif_ctx = SIFTableBuilder.build(all_skills)
        for skill in all_skills:
            assert skill.name in sif_ctx

    def test_skills_com_impl_compilam(self, all_skills):
        SIFFactory.clear_cache()
        failed = []
        for skill in all_skills:
            if skill.sif_entry and skill.sif_entry.has_impl:
                if SIFFactory.compile(skill.sif_entry) is None:
                    failed.append(skill.name)
        assert not failed

    def test_codex_reais_compilam(self, all_skills):
        SIFFactory.clear_cache()
        failed = []
        for skill in all_skills:
            if skill.sif_entry and skill.sif_entry.has_codex:
                if SIFFactory.compile(skill.sif_entry) is None:
                    failed.append(skill.name)
        assert not failed

    def test_estimate_real_retorna_saving_positivo(self, all_skills):
        result = estimate_sif_vs_full(all_skills, query="deploy terminal")
        assert result["saving_pct"] > 0
        assert result["focus_skills"] > 0

    def test_micro_contexto_real_nao_quebra(self, loader, all_skills):
        micro_ctx = loader.build_system_prompt_context(all_skills, query="oi", mode="micro")
        assert "Skills Micro" in micro_ctx
        assert "SIF v3" in micro_ctx

    def test_inject_real_expoe_skill_trace_stats(self, loader, all_skills):
        ns: dict = {}
        loader.inject_sif_callables(all_skills, ns)
        assert "skill_trace_stats" in ns