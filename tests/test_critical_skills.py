"""
Testes críticos — Fase 9.1: MCP Skill Loader

Cobre:
- Parsing TOML frontmatter (com e sem MCP)
- Eligibility checks (bins disponíveis/ausentes)
- build_system_prompt_context
- activate/activate_all (MCP mockado)
- deactivate_all
- load_from_dir com skills reais em disco

Execute:
    pytest tests/test_critical_skills.py -v
"""

import pathlib
import tempfile
import shutil
import pytest
from unittest.mock import MagicMock, patch

from rlm.core.skill_loader import SkillDef, SkillLoader


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_skills_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Cria estrutura de skills temporária para testes."""
    # sqlite skill (com MCP, requer 'python' — binário sempre disponível)
    (tmp_path / "sqlite").mkdir()
    (tmp_path / "sqlite" / "SKILL.md").write_text(
        '+++\n'
        'name = "sqlite"\n'
        'description = "Query SQLite databases."\n\n'
        '[mcp]\n'
        'command = "python"\n'
        'args = ["-m", "mcp_sqlite"]\n\n'
        '[requires]\n'
        'bins = ["python"]\n'
        '+++\n\n'
        '# SQLite Skill\n\nUse `sqlite.read_query(query=...)` to run queries.\n',
        encoding="utf-8",
    )

    # weather skill (sem MCP, requer 'curl')
    (tmp_path / "weather").mkdir()
    (tmp_path / "weather" / "SKILL.md").write_text(
        '+++\n'
        'name = "weather"\n'
        'description = "Get weather forecasts."\n\n'
        '[requires]\n'
        'bins = ["curl"]\n'
        '+++\n\n'
        '# Weather Skill\n\nUse subprocess with curl.\n',
        encoding="utf-8",
    )

    # impossible skill (requer binário inexistente)
    (tmp_path / "alien").mkdir()
    (tmp_path / "alien" / "SKILL.md").write_text(
        '+++\n'
        'name = "alien"\n'
        'description = "Alien technology."\n\n'
        '[requires]\n'
        'bins = ["alientool-xyz-9999"]\n'
        '+++\n',
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture
def real_skills_dir() -> pathlib.Path:
    """Aponta para o diretório real de skills do projeto."""
    return pathlib.Path(__file__).parent.parent / "rlm" / "skills"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class TestSkillParsing:

    def test_parse_full_toml_frontmatter(self, tmp_skills_dir):
        loader = SkillLoader()
        skill_file = tmp_skills_dir / "sqlite" / "SKILL.md"
        skill = loader._parse_skill_file(skill_file)

        assert skill.name == "sqlite"
        assert skill.description == "Query SQLite databases."
        assert skill.mcp_command == "python"
        assert skill.mcp_args == ["-m", "mcp_sqlite"]
        assert skill.requires_bins == ["python"]

    def test_parse_no_mcp_section(self, tmp_skills_dir):
        loader = SkillLoader()
        skill_file = tmp_skills_dir / "weather" / "SKILL.md"
        skill = loader._parse_skill_file(skill_file)

        assert skill.name == "weather"
        assert skill.mcp_command == ""
        assert skill.mcp_args == []
        assert not skill.has_mcp

    def test_parse_body_extracted(self, tmp_skills_dir):
        loader = SkillLoader()
        skill = loader._parse_skill_file(tmp_skills_dir / "sqlite" / "SKILL.md")
        assert "SQLite Skill" in skill.body
        assert "read_query" in skill.body

    def test_parse_no_frontmatter_uses_dir_name(self, tmp_path):
        """Arquivo sem +++ usa o nome do diretório pai como name."""
        (tmp_path / "myskill").mkdir()
        (tmp_path / "myskill" / "SKILL.md").write_text(
            "# My Skill\n\nJust markdown, no frontmatter.\n",
            encoding="utf-8",
        )
        loader = SkillLoader()
        skill = loader._parse_skill_file(tmp_path / "myskill" / "SKILL.md")
        assert skill.name == "myskill"
        assert skill.description == ""
        assert skill.body == "# My Skill\n\nJust markdown, no frontmatter."

    def test_parse_missing_closing_delimeter_raises(self, tmp_path):
        (tmp_path / "bad").mkdir()
        (tmp_path / "bad" / "SKILL.md").write_text("+++\nname = 'x'\n", encoding="utf-8")
        loader = SkillLoader()
        with pytest.raises(ValueError, match="fechamento"):
            loader._parse_skill_file(tmp_path / "bad" / "SKILL.md")

    def test_parse_mcp_args_as_list(self, tmp_path):
        (tmp_path / "svc").mkdir()
        (tmp_path / "svc" / "SKILL.md").write_text(
            '+++\n'
            'name = "svc"\n'
            'description = ""\n'
            '[mcp]\n'
            'command = "npx.cmd"\n'
            'args = ["-y", "@scope/pkg", "--flag"]\n'
            '+++\n',
            encoding="utf-8",
        )
        loader = SkillLoader()
        skill = loader._parse_skill_file(tmp_path / "svc" / "SKILL.md")
        assert skill.mcp_args == ["-y", "@scope/pkg", "--flag"]

    def test_parse_mcp_env_vars(self, tmp_path):
        (tmp_path / "env_svc").mkdir()
        (tmp_path / "env_svc" / "SKILL.md").write_text(
            '+++\n'
            'name = "env_svc"\n'
            'description = ""\n'
            '[mcp]\n'
            'command = "python"\n'
            'args = []\n'
            '[mcp.env]\n'
            'API_KEY = "secret"\n'
            'DEBUG = "1"\n'
            '+++\n',
            encoding="utf-8",
        )
        loader = SkillLoader()
        skill = loader._parse_skill_file(tmp_path / "env_svc" / "SKILL.md")
        assert skill.mcp_env == {"API_KEY": "secret", "DEBUG": "1"}

    def test_parse_any_bins(self, tmp_path):
        (tmp_path / "optional").mkdir()
        (tmp_path / "optional" / "SKILL.md").write_text(
            '+++\n'
            'name = "optional"\n'
            'description = ""\n'
            '[requires]\n'
            'any_bins = ["node", "bun", "deno"]\n'
            '+++\n',
            encoding="utf-8",
        )
        loader = SkillLoader()
        skill = loader._parse_skill_file(tmp_path / "optional" / "SKILL.md")
        assert skill.requires_any_bins == ["node", "bun", "deno"]
        assert skill.requires_bins == []

    def test_parse_runtime_metadata(self, tmp_path):
        (tmp_path / "ops").mkdir()
        (tmp_path / "ops" / "SKILL.md").write_text(
            '+++\n'
            'name = "ops"\n'
            'description = "Operational task."\n'
            '[runtime]\n'
            'estimated_cost = 1.75\n'
            'risk_level = "high"\n'
            'side_effects = ["filesystem_write"]\n'
            'preconditions = ["auth"]\n'
            'postconditions = ["artifact_created"]\n'
            'fallback_policy = "ask_user"\n'
            '[quality]\n'
            'historical_reliability = 0.88\n'
            '+++\n',
            encoding="utf-8",
        )
        loader = SkillLoader()
        skill = loader._parse_skill_file(tmp_path / "ops" / "SKILL.md")
        assert skill.runtime.estimated_cost == 1.75
        assert skill.runtime.risk_level == "high"
        assert skill.runtime.side_effects == ["filesystem_write"]
        assert skill.runtime.preconditions == ["auth"]
        assert skill.runtime.postconditions == ["artifact_created"]
        assert skill.runtime.fallback_policy == "ask_user"
        assert skill.runtime.historical_reliability == 0.88

    def test_parse_quality_and_retrieval_metadata(self, tmp_path):
        (tmp_path / "ops").mkdir()
        (tmp_path / "ops" / "SKILL.md").write_text(
            '+++\n'
            'name = "ops"\n'
            'description = "Operational task."\n'
            '[sif]\n'
            'signature = "ops.run() -> str"\n'
            'examples_min = ["rodar diagnóstico"]\n'
            '[quality]\n'
            'historical_reliability = 0.88\n'
            'success_count = 12\n'
            'failure_count = 3\n'
            'last_30d_utility = 0.75\n'
            '[retrieval]\n'
            'embedding_text = "diagnóstico deploy terminal logs"\n'
            'example_queries = ["ver logs do deploy", "diagnosticar terminal"]\n'
            '+++\n',
            encoding="utf-8",
        )
        loader = SkillLoader()
        skill = loader._parse_skill_file(tmp_path / "ops" / "SKILL.md")
        assert skill.quality.success_count == 12
        assert skill.quality.failure_count == 3
        assert skill.quality.last_30d_utility == 0.75
        assert skill.retrieval.embedding_text == "diagnóstico deploy terminal logs"
        assert skill.retrieval.example_queries == ["ver logs do deploy", "diagnosticar terminal"]
        assert skill.sif_entry is not None
        assert skill.sif_entry.examples_min == ["rodar diagnóstico"]

    def test_rank_skills_usa_retrieval_semantico(self):
        loader = SkillLoader()
        shell = SkillDef(
            name="shell",
            description="Executa comandos no terminal",
            tags=["terminal"],
        )
        shell.retrieval.embedding_text = "deploy logs terminal processo servidor"
        browser = SkillDef(
            name="browser",
            description="Lê páginas web",
            tags=["web"],
        )
        browser.retrieval.embedding_text = "navegação html páginas link"
        ranked = loader.rank_skills([browser, shell], query="preciso diagnosticar logs de deploy")
        assert ranked[0].skill.name == "shell"
        assert ranked[0].semantic_score > 0.0

    def test_load_from_dir_aplica_quality_store_persistido(self, tmp_path):
        (tmp_path / "ops").mkdir()
        (tmp_path / "ops" / "SKILL.md").write_text(
            '+++\n'
            'name = "ops"\n'
            'description = "Operational task."\n'
            '[quality]\n'
            'historical_reliability = 0.40\n'
            '+++\n',
            encoding="utf-8",
        )
        quality_file = tmp_path / "quality.json"
        quality_file.write_text(
            '{"ops": {"historical_reliability": 0.91, "call_count": 6, "success_count": 5}}',
            encoding="utf-8",
        )

        loader = SkillLoader(quality_store_path=quality_file)
        skills = loader.load_from_dir(tmp_path)

        assert len(skills) == 1
        assert skills[0].runtime.historical_reliability == 0.91

    def test_assess_skill_availability_suporta_env_any(self, monkeypatch):
        monkeypatch.delenv("TOKEN_A", raising=False)
        monkeypatch.setenv("TOKEN_B", "ok")
        loader = SkillLoader()
        skill = SkillDef(name="ops")
        skill.runtime.preconditions = ["env_any:TOKEN_A|TOKEN_B"]

        availability = loader.assess_skill_availability(skill)

        assert availability.ready is True
        assert availability.reasons == []


# ---------------------------------------------------------------------------
# has_mcp & namespace_name
# ---------------------------------------------------------------------------

class TestSkillDefProperties:

    def test_has_mcp_true_when_command_set(self):
        s = SkillDef(name="sqlite", mcp_command="python", mcp_args=[])
        assert s.has_mcp is True

    def test_has_mcp_false_when_no_command(self):
        s = SkillDef(name="weather")
        assert s.has_mcp is False

    def test_namespace_name_replaces_hyphens(self):
        s = SkillDef(name="my-cool-skill")
        assert s.namespace_name == "my_cool_skill"

    def test_namespace_name_simple(self):
        s = SkillDef(name="sqlite")
        assert s.namespace_name == "sqlite"


class TestRealSkillContracts:

    def test_real_high_impact_skills_tem_runtime_contracts(self, real_skills_dir):
        loader = SkillLoader()
        skills = {skill.name: skill for skill in loader.load_from_dir(real_skills_dir)}

        assert skills["github"].runtime.preconditions == ["env:GITHUB_TOKEN"]
        assert skills["email"].runtime.risk_level == "high"
        assert "external_message_send" in skills["email"].runtime.side_effects
        assert skills["slack"].runtime.preconditions == ["env:SLACK_BOT_TOKEN"]
        assert skills["notion"].runtime.preconditions == ["env:NOTION_TOKEN"]
        assert skills["shell"].runtime.risk_level == "high"


# ---------------------------------------------------------------------------
# load_from_dir
# ---------------------------------------------------------------------------

class TestLoadFromDir:

    def test_loads_all_skill_dirs(self, tmp_skills_dir):
        loader = SkillLoader()
        skills = loader.load_from_dir(tmp_skills_dir)
        names = {s.name for s in skills}
        assert "sqlite" in names
        assert "weather" in names
        assert "alien" in names

    def test_nonexistent_dir_returns_empty(self):
        loader = SkillLoader()
        skills = loader.load_from_dir("/nonexistent/path/xyz")
        assert skills == []

    def test_empty_dir_returns_empty(self, tmp_path):
        loader = SkillLoader()
        skills = loader.load_from_dir(tmp_path)
        assert skills == []

    def test_invalid_skill_file_skipped_without_crash(self, tmp_path):
        """Arquivo com frontmatter inválido não deve parar o carregamento."""
        (tmp_path / "bad").mkdir()
        (tmp_path / "bad" / "SKILL.md").write_text("+++\nname = 'x'\n", encoding="utf-8")
        (tmp_path / "good").mkdir()
        (tmp_path / "good" / "SKILL.md").write_text(
            '+++\nname = "good"\ndescription = "ok"\n+++\n',
            encoding="utf-8",
        )
        loader = SkillLoader()
        skills = loader.load_from_dir(tmp_path)
        names = {s.name for s in skills}
        assert "good" in names
        # "bad" foi pulado
        assert "bad" not in names

    def test_skill_flat_file_with_skill_md_suffix(self, tmp_path):
        """Arquivo flat no formato nome.skill.md também é carregado."""
        (tmp_path / "notion.skill.md").write_text(
            '+++\nname = "notion"\ndescription = "Notion integration."\n+++\n',
            encoding="utf-8",
        )
        loader = SkillLoader()
        skills = loader.load_from_dir(tmp_path)
        names = {s.name for s in skills}
        assert "notion" in names

    def test_real_skills_dir_loads_without_crash(self, real_skills_dir):
        """Diretório real rlm/skills/ deve carregar sem exceção."""
        if not real_skills_dir.exists():
            pytest.skip("Skills dir not found")
        loader = SkillLoader()
        skills = loader.load_from_dir(real_skills_dir)
        assert len(skills) >= 1
        names = {s.name for s in skills}
        assert "sqlite" in names
        assert "weather" in names


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

class TestEligibility:

    def test_skill_requiring_python_is_eligible(self, tmp_skills_dir):
        """'python' sempre existe no PATH onde os testes rodam."""
        loader = SkillLoader()
        skills = loader.load_from_dir(tmp_skills_dir)
        eligible = loader.filter_eligible(skills)
        names = {s.name for s in eligible}
        assert "sqlite" in names  # requer 'python'

    def test_skill_requiring_nonexistent_bin_not_eligible(self, tmp_skills_dir):
        loader = SkillLoader()
        skills = loader.load_from_dir(tmp_skills_dir)
        eligible = loader.filter_eligible(skills)
        names = {s.name for s in eligible}
        assert "alien" not in names

    def test_skill_with_no_bins_always_eligible(self, tmp_path):
        (tmp_path / "noreq").mkdir()
        (tmp_path / "noreq" / "SKILL.md").write_text(
            '+++\nname = "noreq"\ndescription = "no requirements"\n+++\n',
            encoding="utf-8",
        )
        loader = SkillLoader()
        skills = loader.load_from_dir(tmp_path)
        eligible = loader.filter_eligible(skills)
        assert any(s.name == "noreq" for s in eligible)

    def test_any_bins_eligible_if_at_least_one_present(self, tmp_path):
        (tmp_path / "anybin").mkdir()
        (tmp_path / "anybin" / "SKILL.md").write_text(
            '+++\nname = "anybin"\ndescription = ""\n'
            '[requires]\nany_bins = ["python", "alientool-xyz-9999"]\n+++\n',
            encoding="utf-8",
        )
        loader = SkillLoader()
        skills = loader.load_from_dir(tmp_path)
        eligible = loader.filter_eligible(skills)
        assert any(s.name == "anybin" for s in eligible)

    def test_any_bins_not_eligible_if_none_present(self, tmp_path):
        (tmp_path / "nonebin").mkdir()
        (tmp_path / "nonebin" / "SKILL.md").write_text(
            '+++\nname = "nonebin"\ndescription = ""\n'
            '[requires]\nany_bins = ["alientool-xyz-9999", "alientool-abc-9999"]\n+++\n',
            encoding="utf-8",
        )
        loader = SkillLoader()
        skills = loader.load_from_dir(tmp_path)
        eligible = loader.filter_eligible(skills)
        assert not any(s.name == "nonebin" for s in eligible)


# ---------------------------------------------------------------------------
# System Prompt Context
# ---------------------------------------------------------------------------

class TestSystemPromptContext:

    def test_context_contains_skill_name(self):
        skills = [SkillDef(name="sqlite", description="Query databases.")]
        loader = SkillLoader()
        ctx = loader.build_system_prompt_context(skills)
        assert "sqlite" in ctx
        assert "Query databases." in ctx

    def test_context_contains_mcp_namespace_info(self):
        skills = [SkillDef(name="sqlite", description="Query.", mcp_command="python", mcp_args=[])]
        loader = SkillLoader()
        # mode="full": comportamento legado — todos os bodies sempre presentes
        ctx = loader.build_system_prompt_context(skills, mode="full")
        assert "sqlite" in ctx  # namespace name
        assert "list_tools()" in ctx

    def test_context_no_namespace_for_non_mcp_skill(self):
        skills = [SkillDef(name="weather", description="Weather data.")]
        loader = SkillLoader()
        ctx = loader.build_system_prompt_context(skills)
        assert "weather" in ctx
        assert "list_tools()" not in ctx

    def test_context_empty_list_returns_empty_string(self):
        loader = SkillLoader()
        ctx = loader.build_system_prompt_context([])
        assert ctx == ""

    def test_context_contains_body_content(self):
        skills = [SkillDef(name="mem", description="Memory.", body="Use `mem.search()` to recall.")]
        loader = SkillLoader()
        # mode="full": body sempre presente; mode="auto" requer query + tags matching
        ctx = loader.build_system_prompt_context(skills, mode="full")
        assert "mem.search()" in ctx

    def test_context_multiple_skills(self):
        skills = [
            SkillDef(name="sqlite", description="SQL."),
            SkillDef(name="weather", description="Weather."),
        ]
        loader = SkillLoader()
        ctx = loader.build_system_prompt_context(skills)
        assert "sqlite" in ctx
        assert "weather" in ctx


# ---------------------------------------------------------------------------
# Activate (MCP mockado)
# ---------------------------------------------------------------------------

class TestActivate:

    def test_activate_non_mcp_skill_returns_none(self):
        loader = SkillLoader()
        skill = SkillDef(name="weather", description="Weather.")
        repl = {}
        result = loader.activate(skill, repl)
        assert result is None
        assert "weather" not in repl

    def test_activate_mcp_skill_injects_into_repl(self):
        loader = SkillLoader()
        skill = SkillDef(name="sqlite", mcp_command="python", mcp_args=["-m", "fake"])

        mock_ns = MagicMock()
        mock_ns.list_tools.return_value = [{"name": "read_query"}]

        with patch("rlm.plugins.mcp.load_server", return_value=mock_ns):
            repl = {}
            result = loader.activate(skill, repl)

        assert result is mock_ns
        assert repl["sqlite"] is mock_ns

    def test_activate_passes_env_and_scope_to_loader(self):
        loader = SkillLoader()
        skill = SkillDef(
            name="sqlite",
            mcp_command="python",
            mcp_args=["-m", "fake"],
            mcp_env={"DB_PATH": "base.db"},
        )

        mock_ns = MagicMock()

        with patch("rlm.plugins.mcp.load_server", return_value=mock_ns) as mocked_load:
            repl = {}
            loader.activate(
                skill,
                repl,
                env_overrides={"DB_PATH": "override.db", "DEBUG": "1"},
                activation_scope="session-123",
            )

        mocked_load.assert_called_once_with(
            server_name="sqlite",
            command="python",
            args=["-m", "fake"],
            env={"DB_PATH": "override.db", "DEBUG": "1"},
            scope_key="session-123",
        )

    def test_activate_same_skill_twice_reuses_instance(self):
        loader = SkillLoader()
        skill = SkillDef(name="sqlite", mcp_command="python", mcp_args=[])

        mock_ns = MagicMock()
        call_count = []

        def fake_load_server(*args, **kwargs):
            call_count.append(1)
            return mock_ns

        with patch("rlm.plugins.mcp.load_server", side_effect=fake_load_server):
            repl = {}
            loader.activate(skill, repl, activation_scope="session-a")
            loader.activate(skill, repl, activation_scope="session-a")  # segunda vez — deve reutilizar

        assert len(call_count) == 1, "load_server deve ser chamado apenas uma vez"

    def test_activate_same_skill_different_scope_creates_new_instance(self):
        loader = SkillLoader()
        skill = SkillDef(name="sqlite", mcp_command="python", mcp_args=[])

        instances = [MagicMock(name="ns1"), MagicMock(name="ns2")]

        with patch("rlm.plugins.mcp.load_server", side_effect=instances):
            repl_a = {}
            repl_b = {}
            first = loader.activate(skill, repl_a, activation_scope="session-a")
            second = loader.activate(skill, repl_b, activation_scope="session-b")

        assert first is instances[0]
        assert second is instances[1]
        assert repl_a["sqlite"] is instances[0]
        assert repl_b["sqlite"] is instances[1]

    def test_activate_failed_mcp_does_not_crash(self):
        loader = SkillLoader()
        skill = SkillDef(name="broken", mcp_command="nonexistent-cmd", mcp_args=[])

        with patch("rlm.plugins.mcp.load_server", side_effect=RuntimeError("connection failed")):
            repl = {}
            result = loader.activate(skill, repl)

        assert result is None
        assert "broken" not in repl

    def test_activate_all_returns_only_mcp_skills(self):
        loader = SkillLoader()
        skills = [
            SkillDef(name="sqlite", mcp_command="python", mcp_args=[]),
            SkillDef(name="weather"),  # sem MCP
        ]
        mock_ns = MagicMock()
        with patch("rlm.plugins.mcp.load_server", return_value=mock_ns):
            repl = {}
            activated = loader.activate_all(skills, repl)

        assert "sqlite" in activated
        assert "weather" not in activated
        assert repl["sqlite"] is mock_ns
        assert "weather" not in repl

    def test_deactivate_all_closes_namespaces(self):
        loader = SkillLoader()
        mock_ns1 = MagicMock()
        mock_ns2 = MagicMock()
        loader._active = {("sqlite", "session-a"): mock_ns1, ("fs", "session-a"): mock_ns2}
        loader.deactivate_all()

        mock_ns1.close.assert_called_once()
        mock_ns2.close.assert_called_once()
        assert loader._active == {}

    def test_deactivate_all_tolerates_close_error(self):
        loader = SkillLoader()
        mock_ns = MagicMock()
        mock_ns.close.side_effect = Exception("close error")
        loader._active = {("broken", "session-a"): mock_ns}
        loader.deactivate_all()  # não deve explodir
        assert loader._active == {}

    def test_deactivate_scope_closes_only_matching_scope(self):
        loader = SkillLoader()
        mock_ns1 = MagicMock()
        mock_ns1.cache_key = "sqlite::session-a"
        mock_ns2 = MagicMock()
        mock_ns2.cache_key = "fs::session-b"
        loader._active = {("sqlite", "session-a"): mock_ns1, ("fs", "session-b"): mock_ns2}

        closed = loader.deactivate_scope("session-a")

        assert closed == 1
        mock_ns1.close.assert_called_once()
        mock_ns2.close.assert_not_called()
        assert loader._active == {("fs", "session-b"): mock_ns2}

    def test_deactivate_scope_ignores_empty_scope(self):
        loader = SkillLoader()
        mock_ns = MagicMock()
        loader._active = {("sqlite", "session-a"): mock_ns}

        assert loader.deactivate_scope("") == 0
        assert loader._active == {("sqlite", "session-a"): mock_ns}

    def test_get_active_names_reflects_state(self):
        loader = SkillLoader()
        mock_ns = MagicMock()
        with patch("rlm.plugins.mcp.load_server", return_value=mock_ns):
            skill = SkillDef(name="sqlite", mcp_command="python", mcp_args=[])
            loader.activate(skill, {})

        assert "sqlite" in loader.get_active_names()

        loader.deactivate_all()
        assert loader.get_active_names() == []


# ---------------------------------------------------------------------------
# API Integration
# ---------------------------------------------------------------------------

class TestApiSkillIntegration:

    def test_skill_loader_importable(self):
        from rlm.core.skill_loader import SkillLoader, SkillDef
        assert SkillLoader is not None
        assert SkillDef is not None

    def test_api_imports_skill_loader(self):
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert "from rlm.core.skill_loader import SkillLoader" in text

    def test_api_has_skills_endpoint(self):
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert '"/skills"' in text

    def test_api_has_skill_telemetry_endpoints(self):
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert '"/skills/telemetry"' in text
        assert '"/skills/telemetry/{skill_name}"' in text
        assert '"/skills/telemetry/compose"' in text
        assert '"/skills/telemetry/session/{session_id}/compose"' in text
        assert '"/skills/telemetry/search"' in text

    def test_api_deactivates_on_shutdown(self):
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert "skill_loader.deactivate_all()" in text

    def test_api_registers_session_scope_cleanup(self):
        api_src = pathlib.Path(__file__).parent.parent / "rlm" / "server" / "api.py"
        text = api_src.read_text(encoding="utf-8")
        assert "add_close_callback" in text
        assert "deactivate_scope(session.session_id)" in text

    def test_api_injects_skill_context_into_repl(self):
        server_dir = pathlib.Path(__file__).parent.parent / "rlm" / "server"
        text = (server_dir / "api.py").read_text(encoding="utf-8") + (server_dir / "runtime_pipeline.py").read_text(encoding="utf-8")
        assert "__rlm_skills__" in text
        assert "skill_loader.activate_all" in text

    def test_real_skills_parse_without_error(self):
        """Todos os SKILL.md reais passam pelo parser sem levantar exceção."""
        real_dir = pathlib.Path(__file__).parent.parent / "rlm" / "skills"
        if not real_dir.exists():
            pytest.skip("Skills dir not found")
        loader = SkillLoader()
        skills = loader.load_from_dir(real_dir)
        assert len(skills) >= 1
        for s in skills:
            assert s.name, f"Skill sem nome em {s.source_path}"
            assert isinstance(s.description, str)
            assert isinstance(s.mcp_args, list)


class TestMCPClientRecovery:

    def test_health_check_reports_ok(self):
        from rlm.core.mcp_client import BaseSyncMCPClient

        client = BaseSyncMCPClient.__new__(BaseSyncMCPClient)
        client.transport_name = "fake"
        client.list_tools = MagicMock(return_value=[{"name": "tool"}])
        client.is_connected = MagicMock(return_value=True)

        status = client.health_check()

        assert status["ok"] is True
        assert status["tool_count"] == 1

    def test_call_tool_reconnects_after_transport_failure(self):
        from rlm.core.mcp_client import BaseSyncMCPClient

        class _Text:
            type = "text"
            text = "ok apos reconnect"

        class _Result:
            content = [_Text()]

        first_session = MagicMock()
        second_session = MagicMock()
        first_session.call_tool.return_value = object()
        second_session.call_tool.return_value = object()

        client = BaseSyncMCPClient.__new__(BaseSyncMCPClient)
        client.transport_name = "fake"
        client._session = first_session
        client._thread = MagicMock()
        client._thread.is_alive.return_value = True
        client._error = None
        client._lifecycle_lock = __import__("threading").RLock()
        client.connect = MagicMock()
        client.is_connected = MagicMock(side_effect=[False, True])

        calls = {"n": 0}

        def _run_coro_sync(_coro):
            calls["n"] += 1
            if calls["n"] == 1:
                raise BrokenPipeError("broken pipe")
            return _Result()

        def _reconnect(timeout: float = 15.0):
            client._session = second_session

        client._run_coro_sync = MagicMock(side_effect=_run_coro_sync)
        client.reconnect = MagicMock(side_effect=_reconnect)

        output = client.call_tool("demo", {"x": 1})

        assert output == "ok apos reconnect"
        client.reconnect.assert_called_once()
