"""
test_critical_phase10.py — Phase 10 test suite

Covers:
  - Scheduler (TaskStore CRUD, cron matching, trigger computation)
  - New SKILL.md files: maps, travel, twitter, slack, notion, playwright
  - Sandbox REPL alias (get_environment("sandbox", ...) → DockerREPL)
  - max_depth > 1 (multiple other_backends allowed)
  - RLM_SANDBOX auto-detection
"""

from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SKILLS_ROOT = Path(__file__).parent.parent / "rlm" / "skills"
SERVER_ROOT = Path(__file__).parent.parent / "rlm" / "server"


def _read_skill(name: str) -> str:
    return (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")


# ===========================================================================
# 1. Scheduler — TaskStore
# ===========================================================================

class TestSchedulerImports(unittest.TestCase):
    """Verifica que o módulo scheduler existe e pode ser importado."""

    def test_scheduler_module_exists(self):
        scheduler_path = SERVER_ROOT / "scheduler.py"
        self.assertTrue(scheduler_path.exists(), f"scheduler.py not found at {scheduler_path}")

    def test_scheduler_imports_cleanly(self):
        # Adiciona o root ao sys.path se necessário
        root = str(Path(__file__).parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            import importlib
            mod = importlib.import_module("rlm.server.scheduler")
            self.assertTrue(hasattr(mod, "RLMScheduler"))
            self.assertTrue(hasattr(mod, "TaskStore"))
            self.assertTrue(hasattr(mod, "ScheduledTask"))
            self.assertTrue(hasattr(mod, "TaskResult"))
        except ImportError as exc:
            self.skipTest(f"Optional deps missing: {exc}")

    def test_scheduler_uses_runtime_logger_for_operational_events(self):
        root = str(Path(__file__).parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            import importlib
            from rlm.core.structured_log import RuntimeLogger

            mod = importlib.import_module("rlm.server.scheduler")
            self.assertIsInstance(mod.log, RuntimeLogger)
        except ImportError as exc:
            self.skipTest(f"Optional deps missing: {exc}")


class TestTaskStoreCRUD(unittest.TestCase):
    """Testa operações CRUD do TaskStore com banco SQLite temporário."""

    def setUp(self):
        root = str(Path(__file__).parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from rlm.server.scheduler import TaskStore, ScheduledTask, TaskResult
            self.TaskStore = TaskStore
            self.ScheduledTask = ScheduledTask
            self.TaskResult = TaskResult
        except ImportError as exc:
            self.skipTest(f"Optional deps missing: {exc}")

    def _make_store(self) -> "TaskStore":
        tmp_db = tempfile.mktemp(suffix=".db")
        return self.TaskStore(db_path=tmp_db)

    def _make_task(self, prompt="Verifica o clima", trigger_type="cron", trigger_value="0 8 * * *") -> "ScheduledTask":
        return self.ScheduledTask(
            task_id=str(uuid.uuid4()),
            prompt=prompt,
            trigger_type=trigger_type,
            trigger_value=trigger_value,
        )

    def test_add_and_list(self):
        store = self._make_store()
        task = self._make_task()
        store.add_task(task)
        tasks = store.get_all()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].task_id, task.task_id)
        self.assertEqual(tasks[0].prompt, task.prompt)

    def test_cancel_task(self):
        store = self._make_store()
        task = self._make_task(trigger_type="once", trigger_value="2030-01-01T00:00:00")
        store.add_task(task)
        store.cancel(task.task_id)
        tasks = store.get_all()
        # Tarefa cancelada não deve aparecer como enabled
        enabled = [t for t in tasks if t.enabled]
        self.assertEqual(len(enabled), 0)

    def test_delete_task(self):
        store = self._make_store()
        task = self._make_task(trigger_type="interval", trigger_value="3600")
        store.add_task(task)
        store.delete(task.task_id)
        tasks = store.get_all()
        self.assertEqual(len(tasks), 0)

    def test_record_result_and_history(self):
        store = self._make_store()
        task = self._make_task(trigger_type="cron", trigger_value="*/5 * * * *")
        store.add_task(task)
        result = self.TaskResult(
            task_id=task.task_id,
            success=True,
            result="OK",
            error="",
            duration_s=1.23,
        )
        store.record_result(result, next_run_ts=time.time() + 300)
        history = store.get_history(task.task_id, limit=5)
        self.assertEqual(len(history), 1)
        entry = history[0]
        # get_history retorna list[dict]
        self.assertTrue(entry["success"] if isinstance(entry, dict) else entry.success)

    def test_multiple_tasks(self):
        store = self._make_store()
        tasks = [
            self._make_task(f"Tarefa {i}", "interval", str(i * 60))
            for i in range(1, 6)
        ]
        ids = set()
        for t in tasks:
            store.add_task(t)
            ids.add(t.task_id)
        self.assertEqual(len(ids), 5, "IDs devem ser únicos")
        self.assertEqual(len(store.get_all()), 5)


class TestCronMatching(unittest.TestCase):
    """Testa o parser de expressões cron do scheduler."""

    def setUp(self):
        root = str(Path(__file__).parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from rlm.server.scheduler import _cron_matches
            self._cron_matches = _cron_matches
        except ImportError as exc:
            self.skipTest(f"Optional deps missing: {exc}")

    def _dt(self, hour=8, minute=0, day=1, month=1, weekday=0) -> datetime:
        return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)

    def test_wildcard_matches_any(self):
        dt = self._dt(hour=14, minute=30)
        self.assertTrue(self._cron_matches("* * * * *", dt))

    def test_exact_match(self):
        dt = self._dt(hour=8, minute=0)
        self.assertTrue(self._cron_matches("0 8 * * *", dt))

    def test_exact_no_match(self):
        dt = self._dt(hour=9, minute=0)
        self.assertFalse(self._cron_matches("0 8 * * *", dt))

    def test_step_match(self):
        # */15 minutos: 0, 15, 30, 45
        dt_match = self._dt(minute=30)
        dt_no    = self._dt(minute=17)
        self.assertTrue(self._cron_matches("*/15 * * * *", dt_match))
        self.assertFalse(self._cron_matches("*/15 * * * *", dt_no))

    def test_list_match(self):
        # Às 8h ou 20h
        dt8  = self._dt(hour=8,  minute=0)
        dt20 = self._dt(hour=20, minute=0)
        dt12 = self._dt(hour=12, minute=0)
        self.assertTrue(self._cron_matches("0 8,20 * * *", dt8))
        self.assertTrue(self._cron_matches("0 8,20 * * *", dt20))
        self.assertFalse(self._cron_matches("0 8,20 * * *", dt12))

    def test_range_match(self):
        # Horas 9-17
        dt_inside  = self._dt(hour=12, minute=0)
        dt_outside = self._dt(hour=18, minute=0)
        self.assertTrue(self._cron_matches("0 9-17 * * *", dt_inside))
        self.assertFalse(self._cron_matches("0 9-17 * * *", dt_outside))


# ===========================================================================
# 2. Novas SKILL.md (maps, travel, twitter, slack, notion, playwright)
# ===========================================================================

class TestNewSkillsParsing(unittest.TestCase):
    """Verifica que todas as novas skills existem e têm frontmatter válido."""

    EXPECTED_SKILLS = [
        ("maps",       "maps"),
        ("travel",     "travel"),
        ("twitter",    "twitter"),
        ("slack",      "slack"),
        ("notion",     "notion"),
        ("playwright", "playwright"),
    ]

    def _parse_frontmatter(self, text: str) -> dict:
        """Parse simples do frontmatter TOML entre +++ markers."""
        if not text.startswith("+++"):
            return {}
        end = text.index("+++", 3)
        fm_text = text[3:end].strip()
        result = {}
        for line in fm_text.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip().strip('"')
        return result

    def test_all_skills_exist(self):
        for folder, _ in self.EXPECTED_SKILLS:
            path = SKILLS_ROOT / folder / "SKILL.md"
            self.assertTrue(path.exists(), f"SKILL.md não encontrado: {path}")

    def test_all_skills_start_with_plus_plus_plus(self):
        for folder, _ in self.EXPECTED_SKILLS:
            text = _read_skill(folder)
            self.assertTrue(
                text.startswith("+++"),
                f"{folder}/SKILL.md deve começar com +++, não com: {text[:30]!r}"
            )

    def test_all_skills_have_name_field(self):
        for folder, expected_name in self.EXPECTED_SKILLS:
            text = _read_skill(folder)
            fm = self._parse_frontmatter(text)
            self.assertIn("name", fm, f"{folder}/SKILL.md sem campo 'name'")
            self.assertEqual(
                fm["name"], expected_name,
                f"{folder}/SKILL.md: nome esperado '{expected_name}', obtido '{fm['name']}'"
            )

    def test_all_skills_have_description(self):
        for folder, _ in self.EXPECTED_SKILLS:
            text = _read_skill(folder)
            self.assertIn("description", text,
                          f"{folder}/SKILL.md sem campo 'description'")

    def test_skills_have_substantial_content(self):
        for folder, _ in self.EXPECTED_SKILLS:
            text = _read_skill(folder)
            self.assertGreater(
                len(text), 500,
                f"{folder}/SKILL.md muito curto ({len(text)} chars)"
            )

    def test_playwright_has_mcp_section(self):
        text = _read_skill("playwright")
        self.assertIn("[mcp]", text, "playwright/SKILL.md deve ter seção [mcp]")
        self.assertIn("@playwright/mcp", text, "playwright/SKILL.md deve referenciar @playwright/mcp")

    def test_playwright_requires_node(self):
        text = _read_skill("playwright")
        self.assertIn("node", text, "playwright/SKILL.md deve referenciar dependência 'node'")

    def test_maps_has_osm_fallback(self):
        text = _read_skill("maps")
        self.assertIn("nominatim", text.lower(),
                      "maps/SKILL.md deve documentar fallback OSM/Nominatim gratuito")

    def test_travel_has_amadeus(self):
        text = _read_skill("travel")
        self.assertIn("amadeus", text.lower(),
                      "travel/SKILL.md deve documentar API Amadeus")

    def test_twitter_has_oauth(self):
        text = _read_skill("twitter")
        self.assertIn("oauth", text.lower(),
                      "twitter/SKILL.md deve documentar OAuth")

    def test_slack_has_bot_token(self):
        text = _read_skill("slack")
        self.assertIn("SLACK_BOT_TOKEN", text,
                      "slack/SKILL.md deve documentar SLACK_BOT_TOKEN")

    def test_notion_has_integration_token(self):
        text = _read_skill("notion")
        self.assertIn("NOTION_TOKEN", text,
                      "notion/SKILL.md deve documentar NOTION_TOKEN")


class TestAllSkillsViaLoader(unittest.TestCase):
    """Verifica que o skill_loader do RLM consegue fazer parse de todos os novos skills."""

    NEW_SKILLS = ["maps", "travel", "twitter", "slack", "notion", "playwright"]

    def setUp(self):
        root = str(Path(__file__).parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from rlm.core.skill_loader import _parse_skill_file
            self._parse = _parse_skill_file
        except ImportError as exc:
            self.skipTest(f"Optional deps missing: {exc}")

    def test_all_new_skills_parse_without_error(self):
        for name in self.NEW_SKILLS:
            path = SKILLS_ROOT / name / "SKILL.md"
            if not path.exists():
                self.skipTest(f"SKILL.md não encontrado: {path}")
            text = path.read_text(encoding="utf-8")
            with self.subTest(skill=name):
                try:
                    result = self._parse(text)
                except Exception as exc:
                    self.fail(f"_parse_skill_file falhou para '{name}': {exc}")
                # name deve ser extraído corretamente
                self.assertEqual(result.name, name,
                                 f"Skill '{name}': name extraído incorretamente: {result.name!r}")

    def test_playwright_parsed_as_mcp_tier(self):
        path = SKILLS_ROOT / "playwright" / "SKILL.md"
        if not path.exists():
            self.skipTest("playwright/SKILL.md não encontrado")
        text = path.read_text(encoding="utf-8")
        result = self._parse(text)
        # Deve ter configuração MCP parseada (mcp_command preenchido)
        self.assertTrue(
            result.has_mcp,
            f"playwright skill deve ter config MCP parseada pelo loader "
            f"(mcp_command={result.mcp_command!r})"
        )


# ===========================================================================
# 3. Sandbox REPL alias
# ===========================================================================

class TestSandboxEnvironment(unittest.TestCase):
    """Verifica que get_environment('sandbox', ...) retorna DockerREPL."""

    def setUp(self):
        root = str(Path(__file__).parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)

    def test_sandbox_alias_accepted(self):
        """get_environment deve aceitar 'sandbox' sem lançar ValueError."""
        from rlm.environments import get_environment
        try:
            env = get_environment("sandbox", {})
            # Se chegou aqui sem erro, está correto
            # Apenas verificamos o tipo
            from rlm.environments.docker_repl import DockerREPL
            self.assertIsInstance(env, DockerREPL)
        except ImportError:
            self.skipTest("DockerREPL deps não disponíveis neste ambiente")
        except Exception as exc:
            # Outros erros de conexão são OK — o importante é que 'sandbox' foi aceito
            if "Unknown environment" in str(exc):
                self.fail(f"get_environment rejeita 'sandbox': {exc}")

    def test_sandbox_and_docker_are_equivalent(self):
        """'sandbox' e 'docker' devem retornar instâncias do mesmo tipo."""
        from rlm.environments import get_environment
        try:
            env_docker  = get_environment("docker",  {})
            env_sandbox = get_environment("sandbox", {})
            self.assertEqual(type(env_docker), type(env_sandbox))
        except ImportError:
            self.skipTest("DockerREPL deps não disponíveis")
        except Exception as exc:
            if "Unknown environment" in str(exc):
                self.fail(f"Alias não configurado: {exc}")

    def test_unknown_environment_still_raises(self):
        """Ambientes desconhecidos ainda devem lançar ValueError."""
        from rlm.environments import get_environment
        with self.assertRaises(ValueError):
            get_environment("nonexistent_env_xyz", {})


# ===========================================================================
# 4. max_depth > 1
# ===========================================================================

class TestMaxDepthMultiple(unittest.TestCase):
    """Verifica que RLM aceita max_depth > 1 e múltiplos other_backends."""

    def setUp(self):
        root = str(Path(__file__).parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)

    def test_rlm_accepts_max_depth_3(self):
        """RLM não deve lançar erro com max_depth=3."""
        try:
            from rlm.core.rlm import RLM
            rlm = RLM(
                backend="anthropic",
                backend_kwargs={"model": "claude-3-5-haiku-20241022"},
                environment="local",
                max_depth=3,
            )
            self.assertEqual(rlm.max_depth, 3)
        except ImportError as exc:
            self.skipTest(f"Optional deps: {exc}")

    def test_rlm_accepts_two_other_backends(self):
        """RLM deve aceitar lista de 2 backends sem ValueError."""
        try:
            from rlm.core.rlm import RLM
            mock_backend_1 = MagicMock()
            mock_backend_2 = MagicMock()
            # Não deve lançar ValueError
            rlm = RLM(
                backend="anthropic",
                backend_kwargs={"model": "claude-3-5-haiku-20241022"},
                environment="local",
                max_depth=2,
                other_backends=[mock_backend_1, mock_backend_2],
                other_backend_kwargs=[{"model": "m1"}, {"model": "m2"}],
            )
            self.assertEqual(len(rlm.other_backends), 2)
        except ImportError as exc:
            self.skipTest(f"Optional deps: {exc}")

    def test_rlm_raises_on_mismatched_backends_and_kwargs(self):
        """Mismatch entre other_backends e other_backend_kwargs deve lançar ValueError."""
        try:
            from rlm.core.rlm import RLM
        except ImportError as exc:
            self.skipTest(f"Optional deps: {exc}")

        mock_b = MagicMock()
        with self.assertRaises(ValueError):
            RLM(
                backend="anthropic",
                backend_kwargs={"model": "claude-3-5-haiku-20241022"},
                environment="local",
                max_depth=2,
                other_backends=[mock_b, mock_b],  # 2 backends
                other_backend_kwargs=[{"model": "m1"}],  # apenas 1 kwarg → erro
            )

    def test_single_backend_still_works(self):
        """Um único other_backend ainda deve funcionar normalmente."""
        try:
            from rlm.core.rlm import RLM
            mock_b = MagicMock()
            rlm = RLM(
                backend="anthropic",
                backend_kwargs={"model": "claude-3-5-haiku-20241022"},
                environment="local",
                max_depth=1,
                other_backends=[mock_b],
                other_backend_kwargs=[{"model": "m1"}],
            )
            self.assertEqual(len(rlm.other_backends), 1)
        except ImportError as exc:
            self.skipTest(f"Optional deps: {exc}")


# ===========================================================================
# 5. RLM_SANDBOX auto-detection
# ===========================================================================

class TestRLMSandboxEnvVar(unittest.TestCase):
    """Verifica que RLM_SANDBOX=1 faz environment_type='sandbox'."""

    def setUp(self):
        root = str(Path(__file__).parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)

    def test_sandbox_env_var_sets_environment_type(self):
        try:
            from rlm.core.rlm import RLM
        except ImportError as exc:
            self.skipTest(f"Optional deps: {exc}")

        with patch.dict(os.environ, {"RLM_SANDBOX": "1"}):
            rlm = RLM(
                backend="anthropic",
                backend_kwargs={"model": "claude-3-5-haiku-20241022"},
                environment="local",  # deveria ser overridden para 'sandbox'
            )
            self.assertEqual(
                rlm.environment_type, "sandbox",
                "RLM_SANDBOX=1 deve fazer environment_type='sandbox'"
            )

    def test_no_sandbox_env_var_keeps_local(self):
        try:
            from rlm.core.rlm import RLM
        except ImportError as exc:
            self.skipTest(f"Optional deps: {exc}")

        env_without_sandbox = {k: v for k, v in os.environ.items() if k != "RLM_SANDBOX"}
        with patch.dict(os.environ, env_without_sandbox, clear=True):
            rlm = RLM(
                backend="anthropic",
                backend_kwargs={"model": "claude-3-5-haiku-20241022"},
                environment="local",
            )
            self.assertEqual(rlm.environment_type, "local")

    def test_explicit_docker_not_overridden(self):
        """Se já passou 'docker' explicitamente, não deve trocar para sandbox mesmo com flag."""
        try:
            from rlm.core.rlm import RLM
        except ImportError as exc:
            self.skipTest(f"Optional deps: {exc}")

        with patch.dict(os.environ, {"RLM_SANDBOX": "1"}):
            rlm = RLM(
                backend="anthropic",
                backend_kwargs={"model": "claude-3-5-haiku-20241022"},
                environment="docker",  # explícito — não deve mudar
            )
            self.assertEqual(rlm.environment_type, "docker")


# ===========================================================================
# 6. Contagem regressiva: todos os skills esperados existem
# ===========================================================================

class TestAllSkillInventory(unittest.TestCase):
    """Inventário completo de skills esperados no projeto."""

    EXPECTED = [
        "browser", "web_search", "github", "email", "calendar",
        "whatsapp", "voice", "telegram_bot",
        "maps", "travel", "twitter", "slack", "notion", "playwright",
    ]

    def test_all_expected_skills_exist(self):
        missing = []
        for skill in self.EXPECTED:
            path = SKILLS_ROOT / skill / "SKILL.md"
            if not path.exists():
                missing.append(skill)
        self.assertEqual(
            missing, [],
            f"Skills não encontrados: {missing}"
        )

    def test_all_skills_start_with_triple_plus(self):
        bad = []
        for skill in self.EXPECTED:
            path = SKILLS_ROOT / skill / "SKILL.md"
            if path.exists():
                text = path.read_text(encoding="utf-8")
                if not text.startswith("+++"):
                    bad.append(skill)
        self.assertEqual(
            bad, [],
            f"Skills com frontmatter inválido (não começam com +++): {bad}"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
