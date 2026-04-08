"""
tests/test_critical_skills_new.py

Testes críticos para as novas skills, browser plugin e telegram gateway.
Cobertura:
  - Parsing de todos os 8 novos SKILL.md
  - plugins/browser.py: make_browser_globals, funções individuais, fallback stdlib
  - server/telegram_gateway.py: config, rate limiter, comandos, truncagem
  - Injeção de browser globals em rlm.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import threading
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Localiza o root do projeto
# ---------------------------------------------------------------------------

PROJ_ROOT = Path(__file__).parent.parent  # rlm-main/
SKILLS_DIR = PROJ_ROOT / "rlm" / "skills"
sys.path.insert(0, str(PROJ_ROOT))


# ===========================================================================
# CLASSE 1 — Parsing dos novos SKILL.md
# ===========================================================================

class TestNewSkillsParsing:
    """Garante que todos os 8 novos SKILL.md parseiam sem erros."""

    @pytest.fixture
    def loader(self):
        from rlm.core.skillkit.skill_loader import SkillLoader
        return SkillLoader()

    NEW_SKILLS = [
        "browser", "web_search", "github", "email",
        "calendar", "whatsapp", "voice", "telegram_bot",
    ]

    @pytest.mark.parametrize("skill_name", NEW_SKILLS)
    def test_skill_file_exists(self, skill_name):
        path = SKILLS_DIR / skill_name / "SKILL.md"
        assert path.exists(), f"SKILL.md não encontrado: {path}"

    @pytest.mark.parametrize("skill_name", NEW_SKILLS)
    def test_skill_parses_without_error(self, loader, skill_name):
        path = SKILLS_DIR / skill_name / "SKILL.md"
        skill = loader._parse_skill_file(path)
        assert skill.name, f"skill.name vazio para {skill_name}"
        assert skill.description, f"skill.description vazio para {skill_name}"

    @pytest.mark.parametrize("skill_name", NEW_SKILLS)
    def test_skill_has_body(self, loader, skill_name):
        path = SKILLS_DIR / skill_name / "SKILL.md"
        skill = loader._parse_skill_file(path)
        assert len(skill.body) > 100, f"skill.body muito curto para {skill_name}"

    @pytest.mark.parametrize("skill_name", NEW_SKILLS)
    def test_skill_name_matches_dirname(self, loader, skill_name):
        path = SKILLS_DIR / skill_name / "SKILL.md"
        skill = loader._parse_skill_file(path)
        assert skill.name == skill_name, (
            f"skill.name='{skill.name}' != dirname='{skill_name}'"
        )

    def test_all_new_skills_loaded_from_dir(self, loader):
        skills = loader.load_from_dir(SKILLS_DIR)
        names = {s.name for s in skills}
        for expected in self.NEW_SKILLS:
            assert expected in names, f"Skill '{expected}' não carregada de {SKILLS_DIR}"

    def test_total_skill_count_at_least_12(self, loader):
        """Existentes (4) + novos (8) = 12 mínimo."""
        skills = loader.load_from_dir(SKILLS_DIR)
        assert len(skills) >= 12, f"Esperado >= 12 skills, encontrado {len(skills)}"

    def test_no_mcp_for_context_only_skills(self, loader):
        """Skills de contexto puro (email, calendar, etc.) não têm servidor MCP."""
        context_only = ["email", "calendar", "whatsapp", "voice", "telegram_bot"]
        for skill_name in context_only:
            path = SKILLS_DIR / skill_name / "SKILL.md"
            skill = loader._parse_skill_file(path)
            assert not skill.has_mcp, (
                f"Skill '{skill_name}' não deveria ter MCP (é contexto-only)"
            )

    def test_system_prompt_context_contains_all_new_skills(self, loader):
        skills = loader.load_from_dir(SKILLS_DIR)
        ctx = loader.build_system_prompt_context(skills, mode="compact")
        for name in self.NEW_SKILLS:
            assert name in ctx, f"'{name}' não aparece no system prompt context"

    def test_requires_bins_empty_for_pure_python_skills(self, loader):
        """Skills puras Python não precisam de binários externos."""
        pure_python = ["email", "whatsapp", "voice", "telegram_bot", "github"]
        for skill_name in pure_python:
            path = SKILLS_DIR / skill_name / "SKILL.md"
            skill = loader._parse_skill_file(path)
            assert not skill.requires_bins, (
                f"Skill '{skill_name}' declarou requires.bins={skill.requires_bins} "
                "mas deveria ser vazio (pura Python)"
            )


# ===========================================================================
# CLASSE 2 — plugins/browser.py: make_browser_globals
# ===========================================================================

class TestBrowserPluginImports:
    """Verifica que o módulo importa e a factory retorna as funções corretas."""

    def test_module_importable(self):
        from rlm.plugins import browser  # noqa: F401

    def test_make_browser_globals_returns_dict(self):
        from rlm.plugins.browser import make_browser_globals
        g = make_browser_globals()
        assert isinstance(g, dict)

    def test_make_browser_globals_has_all_functions(self):
        from rlm.plugins.browser import make_browser_globals
        g = make_browser_globals()
        expected = {"web_get", "web_post", "web_scrape", "web_search", "web_download"}
        assert set(g.keys()) == expected, f"Chaves faltando: {expected - set(g.keys())}"

    def test_all_values_are_callable(self):
        from rlm.plugins.browser import make_browser_globals
        g = make_browser_globals()
        for name, fn in g.items():
            assert callable(fn), f"{name} não é callable"

    def test_manifest_present(self):
        from rlm.plugins.browser import MANIFEST
        assert MANIFEST.name == "browser"
        assert "web_get" in MANIFEST.functions
        assert "web_search" in MANIFEST.functions


# ===========================================================================
# CLASSE 3 — web_get (com mock de urllib)
# ===========================================================================

class TestWebGet:
    """Testa web_get com rede mockada."""

    def _mock_response(self, body: str, status: int = 200, charset: str = "utf-8"):
        resp = MagicMock()
        resp.status = status
        resp.read.return_value = body.encode(charset)
        resp.headers.get_content_charset.return_value = charset
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_web_get_returns_text_on_200(self):
        from rlm.plugins.browser import web_get
        resp = self._mock_response("<html>hello</html>")
        with patch("rlm.plugins.browser._have_requests", return_value=False), \
             patch("rlm.plugins.browser.urllib.request.urlopen", return_value=resp):
            result = web_get("https://example.com")
        assert "hello" in result

    def test_web_get_raises_on_400(self):
        from rlm.plugins.browser import web_get
        from urllib.error import HTTPError
        err = HTTPError("https://example.com", 404, "Not Found", {}, None)
        err.read = lambda: b"not found"
        with patch("rlm.plugins.browser._have_requests", return_value=False), \
             patch("rlm.plugins.browser.urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="HTTP 404"):
                web_get("https://example.com/notfound")

    def test_web_get_uses_requests_when_available(self):
        from rlm.plugins.browser import web_get

        mock_resp = MagicMock()
        mock_resp.text = "<html>via requests</html>"
        mock_resp.raise_for_status = MagicMock()
        fake_requests = types.SimpleNamespace(get=MagicMock(return_value=mock_resp))

        with patch("rlm.plugins.browser._have_requests", return_value=True), \
             patch.dict("sys.modules", {"requests": fake_requests}):
            result = web_get("https://example.com")

        assert "via requests" in result
        fake_requests.get.assert_called_once()


# ===========================================================================
# CLASSE 4 — web_scrape (stdlib fallback _TextExtractor)
# ===========================================================================

class TestWebScrape:
    """Testa extração de HTML sem beautifulsoup4."""

    def test_scrape_extracts_title(self):
        from rlm.plugins.browser import web_scrape
        html = "<html><head><title>Minha Página</title></head><body><p>Texto</p></body></html>"
        with patch("rlm.plugins.browser.web_get", return_value=html), \
             patch("rlm.plugins.browser._have_bs4", return_value=False):
            result = web_scrape("https://example.com")
        assert result["title"] == "Minha Página"

    def test_scrape_extracts_text(self):
        from rlm.plugins.browser import web_scrape
        html = "<html><body><p>Olá mundo</p><p>Segunda linha</p></body></html>"
        with patch("rlm.plugins.browser.web_get", return_value=html), \
             patch("rlm.plugins.browser._have_bs4", return_value=False):
            result = web_scrape("https://example.com")
        assert "Olá" in result["text"] or "mundo" in result["text"]

    def test_scrape_extracts_links(self):
        from rlm.plugins.browser import web_scrape
        html = '<html><body><a href="https://rlm.ai">RLM</a></body></html>'
        with patch("rlm.plugins.browser.web_get", return_value=html), \
             patch("rlm.plugins.browser._have_bs4", return_value=False):
            result = web_scrape("https://example.com")
        assert any(l["href"] == "https://rlm.ai" for l in result["links"])

    def test_scrape_hides_script_content(self):
        from rlm.plugins.browser import web_scrape
        html = "<html><body><script>alert('xss')</script><p>Visível</p></body></html>"
        with patch("rlm.plugins.browser.web_get", return_value=html), \
             patch("rlm.plugins.browser._have_bs4", return_value=False):
            result = web_scrape("https://example.com")
        assert "alert" not in result["text"]
        assert "Visível" in result["text"]

    def test_scrape_returns_dict_keys(self):
        from rlm.plugins.browser import web_scrape
        html = "<html><head><title>T</title></head><body>X</body></html>"
        with patch("rlm.plugins.browser.web_get", return_value=html), \
             patch("rlm.plugins.browser._have_bs4", return_value=False):
            result = web_scrape("https://example.com")
        assert "title" in result and "text" in result and "links" in result


# ===========================================================================
# CLASSE 5 — web_search (mock DuckDuckGo)
# ===========================================================================

class TestWebSearch:
    """Testa web_search com API mockada."""

    DDG_RESPONSE = json.dumps({
        "Heading": "Python",
        "AbstractText": "Python é uma linguagem de programação.",
        "AbstractURL": "https://python.org",
        "RelatedTopics": [
            {"Text": "Tutorial Python", "FirstURL": "https://docs.python.org"},
            {"Text": "Python asyncio", "FirstURL": "https://docs.python.org/asyncio"},
        ],
    })

    def test_web_search_returns_list(self):
        from rlm.plugins.browser import web_search
        with patch("rlm.plugins.browser._urllib_get",
                   return_value=(200, self.DDG_RESPONSE)):
            results = web_search("Python tutorial")
        assert isinstance(results, list)

    def test_web_search_result_has_required_keys(self):
        from rlm.plugins.browser import web_search
        with patch("rlm.plugins.browser._urllib_get",
                   return_value=(200, self.DDG_RESPONSE)):
            results = web_search("Python")
        assert len(results) >= 1
        for r in results:
            assert "title" in r
            assert "url" in r
            assert "snippet" in r

    def test_web_search_respects_max_results(self):
        from rlm.plugins.browser import web_search
        with patch("rlm.plugins.browser._urllib_get",
                   return_value=(200, self.DDG_RESPONSE)):
            results = web_search("Python", max_results=2)
        assert len(results) <= 2

    def test_web_search_returns_error_on_exception(self):
        from rlm.plugins.browser import web_search
        with patch("rlm.plugins.browser._urllib_get", side_effect=Exception("timeout")):
            results = web_search("qualquer coisa")
        assert isinstance(results, list)
        assert len(results) == 1
        assert "Erro" in results[0]["title"]


# ===========================================================================
# CLASSE 6 — web_download
# ===========================================================================

class TestWebDownload:
    """Testa web_download com rede mockada."""

    def test_download_creates_file(self):
        from rlm.plugins.browser import web_download
        content = b"col1,col2\n1,2\n3,4\n"
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [content]
        mock_resp.raise_for_status = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        fake_requests = types.SimpleNamespace(get=MagicMock(return_value=mock_resp))

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = os.path.join(tmpdir, "output.csv")
            with patch("rlm.plugins.browser._have_requests", return_value=True), \
                 patch.dict("sys.modules", {"requests": fake_requests}):
                path = web_download("https://example.com/data.csv", dest)
            assert os.path.exists(path)
            with open(path, "rb") as f:
                assert f.read() == content

    def test_download_returns_absolute_path(self):
        from rlm.plugins.browser import web_download
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b"data"]
        mock_resp.raise_for_status = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        fake_requests = types.SimpleNamespace(get=MagicMock(return_value=mock_resp))

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = os.path.join(tmpdir, "file.txt")
            with patch("rlm.plugins.browser._have_requests", return_value=True), \
                 patch.dict("sys.modules", {"requests": fake_requests}):
                path = web_download("https://example.com/file.txt", dest)
            assert os.path.isabs(path)


# ===========================================================================
# CLASSE 7 — Telegram Gateway: config e rate limiter
# ===========================================================================

class TestTelegramGatewayConfig:
    """Testa configuração e utilitários do gateway."""

    def test_gateway_config_defaults(self):
        from rlm.gateway.telegram_gateway import GatewayConfig
        cfg = GatewayConfig()
        assert cfg.poll_timeout_s == 30
        assert cfg.max_requests_per_min == 10
        assert cfg.max_response_length == 4000
        assert cfg.typing_feedback is True
        assert cfg.allowed_chat_ids == []
        assert cfg.api_base_url == "http://127.0.0.1:8000"
        assert cfg.api_timeout_s == 120

    def test_rate_limiter_allows_within_limit(self):
        from rlm.gateway.telegram_gateway import RateLimiter
        rl = RateLimiter(max_per_window=5, window_s=60.0)
        for _ in range(5):
            assert rl.allow(chat_id=123) is True

    def test_rate_limiter_blocks_over_limit(self):
        from rlm.gateway.telegram_gateway import RateLimiter
        rl = RateLimiter(max_per_window=3, window_s=60.0)
        for _ in range(3):
            rl.allow(chat_id=456)
        assert rl.allow(chat_id=456) is False

    def test_rate_limiter_isolates_chat_ids(self):
        from rlm.gateway.telegram_gateway import RateLimiter
        rl = RateLimiter(max_per_window=2, window_s=60.0)
        rl.allow(chat_id=111)
        rl.allow(chat_id=111)
        # chat_id 111 esgotado; 222 ainda tem limite
        assert rl.allow(chat_id=111) is False
        assert rl.allow(chat_id=222) is True

    def test_rate_limiter_resets_after_window(self):
        from rlm.gateway.telegram_gateway import RateLimiter
        rl = RateLimiter(max_per_window=1, window_s=0.05)  # janela de 50ms
        rl.allow(chat_id=999)
        assert rl.allow(chat_id=999) is False
        time.sleep(0.1)
        assert rl.allow(chat_id=999) is True


# ===========================================================================
# CLASSE 8 — Telegram Gateway: comandos e truncagem
# ===========================================================================

class TestTelegramGatewayCommands:
    """Testa tratamento de comandos especiais (/help, /reset, /status)."""

    def _make_gateway(self):
        from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig
        cfg = GatewayConfig(bot_token="fake:TOKEN")
        gw = TelegramGateway(config=cfg)
        return gw

    def test_help_command_returns_string(self):
        gw = self._make_gateway()
        resp = gw._handle_command(chat_id=1, command="/help", username="user")
        assert resp is not None
        assert "Arkhe" in resp or "help" in resp.lower() or "Agente" in resp

    def test_start_command_returns_string(self):
        gw = self._make_gateway()
        resp = gw._handle_command(chat_id=1, command="/start", username="user")
        assert resp is not None

    def test_reset_command_calls_bridge(self):
        gw = self._make_gateway()
        with patch("rlm.gateway.telegram_gateway._bridge_post", return_value={"status": "ok"}) as mock_bridge:
            resp = gw._handle_command(chat_id=42, command="/reset", username="user")
        assert "reiniciada" in resp.lower() or "reset" in resp.lower()
        mock_bridge.assert_called_once()

    def test_status_command_returns_stats(self):
        gw = self._make_gateway()
        gw._stats["start_time"] = time.time() - 120
        resp = gw._handle_command(chat_id=1, command="/status", username="user")
        assert resp is not None
        assert "Bridge" in resp or "Status" in resp or "bridge" in resp.lower()

    def test_unknown_command_returns_none(self):
        gw = self._make_gateway()
        resp = gw._handle_command(chat_id=1, command="/xyzunknown", username="user")
        assert resp is None

    def test_send_message_truncates_long_text(self):
        """Mensagens > 4000 chars são truncadas antes de enviar."""
        from rlm.gateway.telegram_gateway import _send_message
        long_text = "a" * 5000
        captured = {}

        def fake_tg_request(token, method, data=None, timeout=35):
            captured.update(data or {})
            return {"ok": True}

        with patch("rlm.gateway.telegram_gateway._tg_request", side_effect=fake_tg_request):
            _send_message("fake:TOKEN", 123, long_text)

        assert len(captured.get("text", "")) <= 4000 + 60  # margem para sufixo


# ===========================================================================
# CLASSE 9 — Injeção de browser globals em rlm.py
# ===========================================================================

class TestBrowserGlobalsInjectedInRLM:
    """Verifica que rlm.py importa e injeta corretamente."""

    def test_rlm_imports_make_browser_globals(self):
        """Após refatoração: make_browser_globals deve existir em rlm_context_mixin.py."""
        mixin_path = PROJ_ROOT / "rlm" / "core" / "engine" / "rlm_context_mixin.py"
        source = mixin_path.read_text(encoding="utf-8")
        assert "make_browser_globals" in source, (
            "make_browser_globals não encontrado em rlm/core/engine/rlm_context_mixin.py"
        )

    def test_browser_globals_injected_in_completion_block(self):
        """O bloco de injeção deve chamar make_browser_globals().
        Após refatoração, a injeção está em _inject_repl_globals em rlm_context_mixin.py.
        """
        # A lógica foi movida para rlm_context_mixin.py na refatoração de mixins
        mixin_path = PROJ_ROOT / "rlm" / "core" / "engine" / "rlm_context_mixin.py"
        source = mixin_path.read_text(encoding="utf-8")
        assert "environment.globals.update(make_browser_globals())" in source

    def test_browser_globals_in_environment_after_spawn(self):
        """Simula a injeção e verifica que as funções chegam no globals."""
        from rlm.plugins.browser import make_browser_globals
        env_globals: dict = {}
        env_globals.update(make_browser_globals())
        assert "web_get" in env_globals
        assert "web_search" in env_globals
        assert "web_scrape" in env_globals
        assert "web_post" in env_globals
        assert "web_download" in env_globals
        assert all(callable(v) for v in env_globals.values())

    def test_phase_94_comment_in_rlm_py(self):
        """Garante que o comentário de fase está no arquivo correto.
        Após refatoração, o bloco Phase 9.4 está em rlm_context_mixin.py.
        """
        # A lógica foi movida para rlm_context_mixin.py na refatoração de mixins
        mixin_path = PROJ_ROOT / "rlm" / "core" / "engine" / "rlm_context_mixin.py"
        source = mixin_path.read_text(encoding="utf-8")
        # O comentário explícito de fase foi substituído pelo docstring do método
        assert "browser globals" in source.lower() or "make_browser_globals" in source
