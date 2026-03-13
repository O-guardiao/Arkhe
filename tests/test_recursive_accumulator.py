"""
Testes críticos — Recursive Primitive Accumulation

Cobre:
- SubRLMArtifactResult: estrutura, callables(), values(), as_custom_tools()
- sub_rlm(..., return_artifacts=True): fluxo completo via mock
- sub_rlm(..., return_artifacts=False): comportamento original inalterado
- capture_artifacts em RLM.completion(): integração com LocalREPL.extract_artifacts()
- RLMChatCompletion.artifacts: campo presente, to_dict(), from_dict()
- Regressão: todos os caminhos sem return_artifacts continuam retornando str

Execute:
    pytest tests/test_recursive_accumulator.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock
import pytest


# ===========================================================================
# Helpers
# ===========================================================================

def _make_parent_mock(depth: int = 0, max_depth: int = 2) -> MagicMock:
    parent = MagicMock()
    parent.depth = depth
    parent.max_depth = max_depth
    parent.backend = "openai"
    parent.backend_kwargs = {"model_name": "gpt-4o-mini"}
    parent.environment_type = "local"
    parent.environment_kwargs = {}
    return parent


def _make_mock_rlm_cls(response: str = "resultado", artifacts: dict | None = None):
    """Mock de classe RLM cujo child.completion() retorna response + artifacts opcionais."""
    mock_completion = MagicMock()
    mock_completion.response = response
    mock_completion.artifacts = artifacts  # pode ser None ou dict
    mock_instance = MagicMock()
    mock_instance.completion.return_value = mock_completion
    mock_cls = MagicMock(return_value=mock_instance)
    return mock_cls, mock_instance


# ===========================================================================
# SubRLMArtifactResult — estrutura e métodos auxiliares
# ===========================================================================

class TestSubRLMArtifactResult:

    def test_import(self):
        from rlm.core.sub_rlm import SubRLMArtifactResult
        assert SubRLMArtifactResult is not None

    def test_has_required_fields(self):
        from rlm.core.sub_rlm import SubRLMArtifactResult
        r = SubRLMArtifactResult(answer="ok", artifacts={"fn": lambda: 1, "x": 42})
        assert r.answer == "ok"
        assert "fn" in r.artifacts
        assert "x" in r.artifacts

    def test_depth_default_zero(self):
        from rlm.core.sub_rlm import SubRLMArtifactResult
        r = SubRLMArtifactResult(answer="ok", artifacts={})
        assert r.depth == 0

    def test_depth_custom(self):
        from rlm.core.sub_rlm import SubRLMArtifactResult
        r = SubRLMArtifactResult(answer="ok", artifacts={}, depth=2)
        assert r.depth == 2

    def test_callables_filters_only_callables(self):
        from rlm.core.sub_rlm import SubRLMArtifactResult

        def my_fn(): return 42

        r = SubRLMArtifactResult(
            answer="ok",
            artifacts={"my_fn": my_fn, "value": 100, "name": "alice"},
        )
        callables = r.callables()
        assert "my_fn" in callables
        assert callable(callables["my_fn"])
        assert "value" not in callables
        assert "name" not in callables

    def test_values_filters_only_non_callables(self):
        from rlm.core.sub_rlm import SubRLMArtifactResult

        def my_fn(): return 42

        r = SubRLMArtifactResult(
            answer="ok",
            artifacts={"my_fn": my_fn, "value": 100, "name": "alice"},
        )
        values = r.values()
        assert "value" in values
        assert "name" in values
        assert "my_fn" not in values

    def test_callables_empty_when_no_callables(self):
        from rlm.core.sub_rlm import SubRLMArtifactResult
        r = SubRLMArtifactResult(answer="ok", artifacts={"a": 1, "b": "texto"})
        assert r.callables() == {}

    def test_values_empty_when_all_callables(self):
        from rlm.core.sub_rlm import SubRLMArtifactResult
        r = SubRLMArtifactResult(answer="ok", artifacts={"fn": lambda: None})
        assert r.values() == {}

    def test_as_custom_tools_returns_full_copy(self):
        from rlm.core.sub_rlm import SubRLMArtifactResult

        def my_fn(): return 42

        original = {"my_fn": my_fn, "data": [1, 2, 3]}
        r = SubRLMArtifactResult(answer="ok", artifacts=original)
        tools = r.as_custom_tools()
        assert "my_fn" in tools
        assert "data" in tools
        # Deve ser cópia, não referência
        assert tools is not original

    def test_as_custom_tools_isolated_mutation(self):
        """Mutação em as_custom_tools() não deve afetar artifacts original."""
        from rlm.core.sub_rlm import SubRLMArtifactResult
        r = SubRLMArtifactResult(answer="ok", artifacts={"x": 1})
        tools = r.as_custom_tools()
        tools["y"] = 99
        assert "y" not in r.artifacts

    def test_empty_artifacts(self):
        from rlm.core.sub_rlm import SubRLMArtifactResult
        r = SubRLMArtifactResult(answer="nada", artifacts={})
        assert r.callables() == {}
        assert r.values() == {}
        assert r.as_custom_tools() == {}

    def test_lambda_is_callable(self):
        from rlm.core.sub_rlm import SubRLMArtifactResult
        lam = lambda x: x * 2
        r = SubRLMArtifactResult(answer="ok", artifacts={"double": lam})
        assert "double" in r.callables()

    def test_class_is_callable(self):
        from rlm.core.sub_rlm import SubRLMArtifactResult
        class MyTransformer:
            pass
        r = SubRLMArtifactResult(answer="ok", artifacts={"MyTransformer": MyTransformer})
        assert "MyTransformer" in r.callables()


# ===========================================================================
# sub_rlm(..., return_artifacts=False) — regressão: comportamento original
# ===========================================================================

class TestSubRLMReturnArtifactsFalse:

    def test_returns_string_by_default(self):
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=0, max_depth=2)
        mock_cls, _ = _make_mock_rlm_cls("resposta do filho")
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        result = fn("tarefa simples")
        assert isinstance(result, str)
        assert result == "resposta do filho"

    def test_returns_string_with_return_artifacts_false_explicit(self):
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=0, max_depth=2)
        mock_cls, _ = _make_mock_rlm_cls("resposta")
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        result = fn("tarefa", return_artifacts=False)
        assert isinstance(result, str)
        assert result == "resposta"

    def test_completion_called_without_capture_artifacts_when_false(self):
        """Quando return_artifacts=False, completion() NÃO deve receber capture_artifacts=True."""
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=0, max_depth=2)
        mock_cls, mock_instance = _make_mock_rlm_cls("resp")
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        fn("tarefa", return_artifacts=False)
        # capture_artifacts deve ser False (padrão)
        call_kwargs = mock_instance.completion.call_args
        if call_kwargs is not None:
            kwargs = call_kwargs.kwargs if hasattr(call_kwargs, 'kwargs') else {}
            ca = kwargs.get("capture_artifacts", False)
            assert ca is False


# ===========================================================================
# sub_rlm(..., return_artifacts=True) — novo comportamento
# ===========================================================================

class TestSubRLMReturnArtifactsTrue:

    def test_returns_SubRLMArtifactResult_when_true(self):
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMArtifactResult
        parent = _make_parent_mock(depth=0, max_depth=2)

        def my_fn(): return 42
        mock_cls, mock_instance = _make_mock_rlm_cls(
            response="resultado com artifacts",
            artifacts={"my_fn": my_fn, "data": [1, 2, 3]},
        )
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        result = fn("tarefa com artifacts", return_artifacts=True)

        assert isinstance(result, SubRLMArtifactResult)
        assert result.answer == "resultado com artifacts"
        assert "my_fn" in result.artifacts
        assert "data" in result.artifacts

    def test_depth_set_correctly_in_result(self):
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMArtifactResult
        parent = _make_parent_mock(depth=0, max_depth=3)
        mock_cls, _ = _make_mock_rlm_cls("ok", artifacts={"x": 1})
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        result = fn("tarefa", return_artifacts=True)

        assert isinstance(result, SubRLMArtifactResult)
        assert result.depth == 1  # child_depth = parent.depth + 1 = 0 + 1

    def test_completion_called_with_capture_artifacts_true(self):
        """Quando return_artifacts=True, completion() deve receber capture_artifacts=True."""
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=0, max_depth=2)
        mock_cls, mock_instance = _make_mock_rlm_cls("resp", artifacts={})
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        fn("tarefa", return_artifacts=True)

        call_kwargs = mock_instance.completion.call_args
        assert call_kwargs is not None
        # capture_artifacts=True deve ter sido passado
        passed_ca = (
            call_kwargs.kwargs.get("capture_artifacts")
            if hasattr(call_kwargs, "kwargs")
            else call_kwargs[1].get("capture_artifacts")
        )
        assert passed_ca is True

    def test_artifacts_empty_dict_when_none_returned(self):
        """artifacts=None no completion → SubRLMArtifactResult.artifacts == {}."""
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMArtifactResult
        parent = _make_parent_mock(depth=0, max_depth=2)
        mock_cls, _ = _make_mock_rlm_cls("ok", artifacts=None)
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        result = fn("tarefa", return_artifacts=True)

        assert isinstance(result, SubRLMArtifactResult)
        assert result.artifacts == {}

    def test_chained_usage_as_custom_tools(self):
        """Fluxo completo: filho sintetiza fn, pai a usa via as_custom_tools()."""
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMArtifactResult
        parent = _make_parent_mock(depth=0, max_depth=3)

        def parse_log(line: str) -> dict:
            return {"line": line}

        mock_cls, _ = _make_mock_rlm_cls(
            "parse_log criada e validada",
            artifacts={"parse_log": parse_log, "SCHEMA": {"line": "str"}},
        )
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        result = fn("Cria parse_log() para logs nginx", return_artifacts=True)

        assert isinstance(result, SubRLMArtifactResult)
        tools = result.as_custom_tools()
        assert callable(tools["parse_log"])
        # A função capturada deve funcionar
        assert tools["parse_log"]("GET /api 200") == {"line": "GET /api 200"}

    def test_depth_guard_still_works_with_return_artifacts(self):
        """Depth guard deve funcionar mesmo com return_artifacts=True."""
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMDepthError
        parent = _make_parent_mock(depth=1, max_depth=2)
        fn = make_sub_rlm_fn(parent)
        with pytest.raises(SubRLMDepthError):
            fn("tarefa", return_artifacts=True)

    def test_timeout_still_raises_with_return_artifacts(self):
        """Timeout deve funcionar mesmo com return_artifacts=True."""
        import time
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMTimeoutError

        parent = _make_parent_mock(depth=0, max_depth=2)

        def _slow_completion(*args, **kwargs):
            time.sleep(5.0)
            return MagicMock(response="ok", artifacts={})

        mock_instance = MagicMock()
        mock_instance.completion.side_effect = _slow_completion
        mock_cls = MagicMock(return_value=mock_instance)

        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        with pytest.raises(SubRLMTimeoutError):
            fn("tarefa", timeout_s=0.1, return_artifacts=True)


# ===========================================================================
# RLMChatCompletion — campo artifacts
# ===========================================================================

def _make_usage() -> "UsageSummary":
    """Cria UsageSummary mínimo compatível com o construtor real."""
    from rlm.core.types import UsageSummary
    return UsageSummary(model_usage_summaries={})


class TestRLMChatCompletionArtifacts:

    def test_artifacts_field_defaults_none(self):
        from rlm.core.types import RLMChatCompletion
        c = RLMChatCompletion(
            root_model="gpt-4o",
            prompt="teste",
            response="ok",
            usage_summary=_make_usage(),
            execution_time=0.1,
        )
        assert c.artifacts is None

    def test_artifacts_field_accepts_dict(self):
        from rlm.core.types import RLMChatCompletion

        def my_fn(): return 42

        c = RLMChatCompletion(
            root_model="gpt-4o",
            prompt="teste",
            response="ok",
            usage_summary=_make_usage(),
            execution_time=0.1,
            artifacts={"my_fn": my_fn, "value": 42},
        )
        assert c.artifacts is not None
        assert "my_fn" in c.artifacts

    def test_to_dict_includes_artifacts_repr(self):
        from rlm.core.types import RLMChatCompletion

        def my_fn(): return 42

        c = RLMChatCompletion(
            root_model="gpt-4o",
            prompt="teste",
            response="ok",
            usage_summary=_make_usage(),
            execution_time=0.1,
            artifacts={"my_fn": my_fn, "value": 42},
        )
        d = c.to_dict()
        assert "artifacts" in d
        assert d["artifacts"] is not None
        # Valores são repr() pois callables não são serializáveis em JSON
        assert "my_fn" in d["artifacts"]

    def test_to_dict_artifacts_none_when_not_set(self):
        from rlm.core.types import RLMChatCompletion
        c = RLMChatCompletion(
            root_model="gpt-4o",
            prompt="teste",
            response="ok",
            usage_summary=_make_usage(),
            execution_time=0.1,
        )
        d = c.to_dict()
        assert d.get("artifacts") is None

    def test_from_dict_does_not_restore_artifacts(self):
        """from_dict() não restaura artifacts (callables não são serializáveis)."""
        from rlm.core.types import RLMChatCompletion
        c = RLMChatCompletion(
            root_model="gpt-4o",
            prompt="teste",
            response="ok",
            usage_summary=_make_usage(),
            execution_time=0.1,
            artifacts={"x": 1},
        )
        d = c.to_dict()
        c2 = RLMChatCompletion.from_dict(d)
        # artifacts não é restaurado (por design — callables não são JSON)
        assert c2.artifacts is None


# ===========================================================================
# LocalREPL.extract_artifacts() — integração
# ===========================================================================

class TestExtractArtifacts:

    def _make_env(self) -> "object":
        """Cria LocalREPL com namespace mínimo usando mocks."""
        try:
            from rlm.environments.local_repl import LocalREPL
            env = object.__new__(LocalREPL)
            # Simular o namespace de locals diretamente
            env.locals = {}
            return env
        except Exception:
            pytest.skip("LocalREPL não disponível para teste unitário direto")

    def test_extract_artifacts_excludes_private_vars(self):
        from rlm.environments.local_repl import LocalREPL
        env = object.__new__(LocalREPL)
        env.locals = {
            "_private": "ignorado",
            "__dunder": "ignorado",
            "my_fn": lambda: 42,
            "result": [1, 2, 3],
        }
        arts = LocalREPL.extract_artifacts(env)
        assert "_private" not in arts
        assert "__dunder" not in arts
        assert "my_fn" in arts
        assert "result" in arts

    def test_extract_artifacts_excludes_context_vars(self):
        from rlm.environments.local_repl import LocalREPL
        env = object.__new__(LocalREPL)
        env.locals = {
            "context_1": "prompt orig",
            "context_2": "outro ctx",
            "history_1": "msg antiga",
            "context": "ctx geral",
            "history": "hist geral",
            "my_data": {"rows": 100},
        }
        arts = LocalREPL.extract_artifacts(env)
        assert "context_1" not in arts
        assert "context_2" not in arts
        assert "history_1" not in arts
        assert "context" not in arts
        assert "history" not in arts
        assert "my_data" in arts

    def test_extract_artifacts_returns_only_valid_locals(self):
        from rlm.environments.local_repl import LocalREPL
        env = object.__new__(LocalREPL)
        env.locals = {
            "clean_fn": lambda x: x,
            "result_df": {"col": [1, 2, 3]},
            "context_0": "excluído",
            "_internal": "excluído",
        }
        arts = LocalREPL.extract_artifacts(env)
        assert set(arts.keys()) == {"clean_fn", "result_df"}

    def test_extract_artifacts_empty_when_all_excluded(self):
        from rlm.environments.local_repl import LocalREPL
        env = object.__new__(LocalREPL)
        env.locals = {
            "_a": 1,
            "context_1": "texto",
            "history_1": "msg",
        }
        arts = LocalREPL.extract_artifacts(env)
        assert arts == {}

    def test_extract_artifacts_preserves_function_objects(self):
        """Funções são copiadas por referência — devem permanecer chamáveis."""
        from rlm.environments.local_repl import LocalREPL

        def my_transform(x):
            return x * 2

        env = object.__new__(LocalREPL)
        env.locals = {"my_transform": my_transform}
        arts = LocalREPL.extract_artifacts(env)
        assert callable(arts["my_transform"])
        assert arts["my_transform"](5) == 10


# ===========================================================================
# Integração: sibling bus injetado nos env_kwargs ao usar return_artifacts
# ===========================================================================

class TestSiblingBusInjectionInSubRLM:

    def test_sibling_bus_injected_in_env_kwargs(self):
        """Quando _sibling_bus é passado, deve aparecer nos env_kwargs do filho."""
        from rlm.core.sub_rlm import make_sub_rlm_fn
        from rlm.core.sibling_bus import SiblingBus

        parent = _make_parent_mock(depth=0, max_depth=2)
        parent.environment_kwargs = {"timeout": 30}

        bus = SiblingBus()
        mock_cls, mock_instance = _make_mock_rlm_cls("ok")
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        fn("task", _sibling_bus=bus, _sibling_branch_id=1)

        # mock_cls foi chamado com environment_kwargs contendo _sibling_bus
        call_kwargs = mock_cls.call_args
        assert call_kwargs is not None
        env_kw = (
            call_kwargs.kwargs.get("environment_kwargs")
            if hasattr(call_kwargs, "kwargs")
            else call_kwargs[1].get("environment_kwargs")
        )
        assert env_kw is not None
        assert env_kw["_sibling_bus"] is bus
        assert env_kw["_sibling_branch_id"] == 1

    def test_no_sibling_bus_when_not_provided(self):
        """Sem _sibling_bus, env_kwargs não deve ter _sibling_bus."""
        from rlm.core.sub_rlm import make_sub_rlm_fn

        parent = _make_parent_mock(depth=0, max_depth=2)
        parent.environment_kwargs = {}

        mock_cls, _ = _make_mock_rlm_cls("ok")
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        fn("task")

        call_kwargs = mock_cls.call_args
        env_kw = (
            call_kwargs.kwargs.get("environment_kwargs")
            if hasattr(call_kwargs, "kwargs")
            else call_kwargs[1].get("environment_kwargs")
        )
        # Deve ser None ou não conter _sibling_bus
        if env_kw is not None:
            assert "_sibling_bus" not in env_kw
