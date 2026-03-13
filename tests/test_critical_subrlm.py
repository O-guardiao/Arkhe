"""
Testes críticos — Fase 9.3: sub_rlm (decomposição explícita via REPL)

Cobre:
- Exceções: SubRLMDepthError, SubRLMTimeoutError, SubRLMError
- make_sub_rlm_fn() factory
- Depth guard (max_depth enforcement)
- Timeout mecanism (threading.Thread + join)
- Execução bem-sucedida (mock do filho)
- Injeção no rlm.py (sub_rlm presente em globals do ambiente)
- Integridade: imports, assinatura da função

Execute:
    pytest tests/test_critical_subrlm.py -v
"""
from __future__ import annotations

import pathlib
import threading
import time

import pytest
from unittest.mock import MagicMock, patch


# ===========================================================================
# Helpers
# ===========================================================================

def _make_parent_mock(depth: int = 0, max_depth: int = 2) -> MagicMock:
    """Cria mock de instância RLM pai com depth/max_depth configurados."""
    parent = MagicMock()
    parent.depth = depth
    parent.max_depth = max_depth
    parent.backend = "openai"
    parent.backend_kwargs = {"model_name": "gpt-4o-mini"}
    parent.environment_type = "local"
    parent.environment_kwargs = {}
    return parent


def _make_mock_rlm_cls(response: str = "resultado final"):
    """Cria classe RLM mockada que retorna response imediatamente."""
    mock_completion = MagicMock()
    mock_completion.response = response
    mock_instance = MagicMock()
    mock_instance.completion.return_value = mock_completion
    mock_cls = MagicMock(return_value=mock_instance)
    return mock_cls, mock_instance


# ===========================================================================
# SubRLM Exceptions
# ===========================================================================

class TestSubRLMExceptions:

    def test_SubRLMError_is_runtime_error(self):
        from rlm.core.sub_rlm import SubRLMError
        assert issubclass(SubRLMError, RuntimeError)

    def test_SubRLMDepthError_is_SubRLMError(self):
        from rlm.core.sub_rlm import SubRLMDepthError, SubRLMError
        assert issubclass(SubRLMDepthError, SubRLMError)

    def test_SubRLMTimeoutError_is_SubRLMError(self):
        from rlm.core.sub_rlm import SubRLMTimeoutError, SubRLMError
        assert issubclass(SubRLMTimeoutError, SubRLMError)

    def test_exceptions_can_be_raised_with_message(self):
        from rlm.core.sub_rlm import SubRLMDepthError, SubRLMTimeoutError, SubRLMError
        with pytest.raises(SubRLMError, match="falhou"):
            raise SubRLMError("falhou")
        with pytest.raises(SubRLMDepthError, match="profundidade"):
            raise SubRLMDepthError("profundidade máxima")
        with pytest.raises(SubRLMTimeoutError, match="timeout"):
            raise SubRLMTimeoutError("timeout")


# ===========================================================================
# make_sub_rlm_fn factory
# ===========================================================================

class TestMakeSubRLMFn:

    def test_returns_callable(self):
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=0, max_depth=2)
        fn = make_sub_rlm_fn(parent)
        assert callable(fn)

    def test_callable_named_sub_rlm(self):
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=0, max_depth=2)
        fn = make_sub_rlm_fn(parent)
        assert fn.__name__ == "sub_rlm"

    def test_has_parent_depth_attribute(self):
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=1, max_depth=3)
        fn = make_sub_rlm_fn(parent)
        assert fn._parent_depth == 1
        assert fn._parent_max_depth == 3

    def test_different_parents_produce_independent_functions(self):
        from rlm.core.sub_rlm import make_sub_rlm_fn
        p1 = _make_parent_mock(depth=0, max_depth=2)
        p2 = _make_parent_mock(depth=1, max_depth=3)
        fn1 = make_sub_rlm_fn(p1)
        fn2 = make_sub_rlm_fn(p2)
        assert fn1._parent_depth == 0
        assert fn2._parent_depth == 1
        assert fn1 is not fn2


# ===========================================================================
# Depth Guard
# ===========================================================================

class TestDepthGuard:

    def test_depth_1_max_depth_2_allows_spawn(self):
        """depth=0, child_depth=1 < max_depth=2 → OK."""
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMDepthError
        parent = _make_parent_mock(depth=0, max_depth=2)
        mock_cls, _ = _make_mock_rlm_cls("resposta do filho")
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        result = fn("tarefa simples")
        assert result == "resposta do filho"

    def test_depth_1_max_depth_2_blocks_spawn(self):
        """depth=1, child_depth=2 >= max_depth=2 → SubRLMDepthError."""
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMDepthError
        parent = _make_parent_mock(depth=1, max_depth=2)
        fn = make_sub_rlm_fn(parent)
        with pytest.raises(SubRLMDepthError, match="profundidade máxima"):
            fn("tarefa que não pode ser delegada")

    def test_depth_0_max_depth_1_blocks_spawn(self):
        """depth=0, child_depth=1 >= max_depth=1 → SubRLMDepthError."""
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMDepthError
        parent = _make_parent_mock(depth=0, max_depth=1)
        fn = make_sub_rlm_fn(parent)
        with pytest.raises(SubRLMDepthError):
            fn("tarefa")

    def test_error_message_contains_depths(self):
        """Mensagem de erro deve mostrar depth atual e max_depth."""
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMDepthError
        parent = _make_parent_mock(depth=2, max_depth=3)
        fn = make_sub_rlm_fn(parent)
        with pytest.raises(SubRLMDepthError) as exc_info:
            fn("tarefa")
        msg = str(exc_info.value)
        assert "depth=2" in msg
        assert "max_depth=3" in msg

    def test_child_spawned_with_incremented_depth(self):
        """Filho é criado com depth = parent.depth + 1."""
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=0, max_depth=3)
        mock_cls, _ = _make_mock_rlm_cls("ok")
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        fn("tarefa")
        call_kwargs = mock_cls.call_args
        # child deve ter depth=1
        passed_depth = (
            call_kwargs.kwargs.get("depth")
            if call_kwargs.kwargs
            else call_kwargs[1].get("depth")
        )
        assert passed_depth == 1


# ===========================================================================
# Timeout Mechanism
# ===========================================================================

class TestTimeoutMechanism:

    def test_timeout_raises_SubRLMTimeoutError(self):
        """Filho que demora mais que timeout_s levanta SubRLMTimeoutError."""
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMTimeoutError
        parent = _make_parent_mock(depth=0, max_depth=2)

        slow_completion = MagicMock()
        slow_completion.response = "nunca chega"
        mock_instance = MagicMock()
        mock_instance.completion.side_effect = lambda p: (time.sleep(10), slow_completion)[1]
        mock_cls = MagicMock(return_value=mock_instance)

        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        with pytest.raises(SubRLMTimeoutError, match="não terminou"):
            fn("tarefa lenta", timeout_s=0.05)  # 50ms timeout

    def test_timeout_message_contains_timeout_value(self):
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMTimeoutError
        parent = _make_parent_mock(depth=0, max_depth=2)

        mock_instance = MagicMock()
        mock_instance.completion.side_effect = lambda p: time.sleep(10)
        mock_cls = MagicMock(return_value=mock_instance)

        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        with pytest.raises(SubRLMTimeoutError) as exc_info:
            fn("tarefa", timeout_s=0.05)
        assert "s" in str(exc_info.value)

    def test_fast_execution_does_not_timeout(self):
        """Chamada rápida (mock instantâneo) não deve levantar timeout."""
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=0, max_depth=2)
        mock_cls, _ = _make_mock_rlm_cls("rápido")
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        result = fn("tarefa rápida", timeout_s=5.0)
        assert result == "rápido"


# ===========================================================================
# Successful Execution
# ===========================================================================

class TestSuccessfulExecution:

    def test_returns_response_string(self):
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=0, max_depth=2)
        mock_cls, _ = _make_mock_rlm_cls("resultado ETL")
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        result = fn("processa CSV /tmp/dados.csv")
        assert result == "resultado ETL"
        assert isinstance(result, str)

    def test_context_prepended_to_prompt(self):
        """context deve ser prefixado antes do task no prompt enviado ao filho."""
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=0, max_depth=2)

        received_prompts = []
        mock_completion = MagicMock()
        mock_completion.response = "ok"
        mock_instance = MagicMock()
        def _capture(prompt):
            received_prompts.append(prompt)
            return mock_completion
        mock_instance.completion.side_effect = _capture
        mock_cls = MagicMock(return_value=mock_instance)

        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        fn("calcula KPIs", context="Dados de vendas de março 2026.")
        assert len(received_prompts) == 1
        assert "Dados de vendas" in received_prompts[0]
        assert "calcula KPIs" in received_prompts[0]
        # context deve aparecer antes da task
        assert received_prompts[0].index("Dados") < received_prompts[0].index("calcula")

    def test_max_iterations_clamped_to_50(self):
        """max_iterations acima de 50 é reduzido a 50."""
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=0, max_depth=2)
        mock_cls, _ = _make_mock_rlm_cls("ok")
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        fn("tarefa", max_iterations=9999)
        call_kwargs = mock_cls.call_args
        passed_max_iter = (
            call_kwargs.kwargs.get("max_iterations")
            if call_kwargs.kwargs
            else call_kwargs[1].get("max_iterations")
        )
        assert passed_max_iter == 50

    def test_max_iterations_clamped_to_1_minimum(self):
        """max_iterations < 1 é elevado a 1."""
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=0, max_depth=2)
        mock_cls, _ = _make_mock_rlm_cls("ok")
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        fn("tarefa", max_iterations=0)
        call_kwargs = mock_cls.call_args
        passed_max_iter = (
            call_kwargs.kwargs.get("max_iterations")
            if call_kwargs.kwargs
            else call_kwargs[1].get("max_iterations")
        )
        assert passed_max_iter == 1

    def test_child_is_verbose_false(self):
        """Filho deve ser silencioso (verbose=False) por padrão."""
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=0, max_depth=2)
        mock_cls, _ = _make_mock_rlm_cls("ok")
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        fn("tarefa")
        call_kwargs = mock_cls.call_args
        passed_verbose = (
            call_kwargs.kwargs.get("verbose")
            if call_kwargs.kwargs
            else call_kwargs[1].get("verbose")
        )
        assert passed_verbose is False

    def test_exception_in_child_raises_SubRLMError(self):
        """Se o filho levantar exceção, sub_rlm deve levantar SubRLMError."""
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMError
        parent = _make_parent_mock(depth=0, max_depth=2)
        mock_instance = MagicMock()
        mock_instance.completion.side_effect = ConnectionError("socket morreu")
        mock_cls = MagicMock(return_value=mock_instance)
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        with pytest.raises(SubRLMError, match="filho falhou"):
            fn("tarefa com erro")

    def test_child_inherits_backend_from_parent(self):
        """Filho deve usar o mesmo backend do pai."""
        from rlm.core.sub_rlm import make_sub_rlm_fn
        parent = _make_parent_mock(depth=0, max_depth=2)
        parent.backend = "anthropic"
        mock_cls, _ = _make_mock_rlm_cls("ok")
        fn = make_sub_rlm_fn(parent, _rlm_cls=mock_cls)
        fn("tarefa")
        call_kwargs = mock_cls.call_args
        passed_backend = (
            call_kwargs.kwargs.get("backend")
            if call_kwargs.kwargs
            else call_kwargs[1].get("backend")
        )
        assert passed_backend == "anthropic"


# ===========================================================================
# Integridade estática (sem execução de LLM)
# ===========================================================================

class TestStaticIntegrity:

    def test_sub_rlm_module_importable(self):
        from rlm.core.sub_rlm import (
            SubRLMError,
            SubRLMDepthError,
            SubRLMTimeoutError,
            SubRLMResult,
            make_sub_rlm_fn,
        )
        assert all([SubRLMError, SubRLMDepthError, SubRLMTimeoutError,
                    SubRLMResult, make_sub_rlm_fn])

    def test_make_sub_rlm_fn_imported_in_rlm_py(self):
        rlm_src = pathlib.Path(__file__).parent.parent / "rlm" / "core" / "rlm.py"
        text = rlm_src.read_text(encoding="utf-8")
        assert "from rlm.core.sub_rlm import make_sub_rlm_fn" in text

    def test_sub_rlm_injected_in_environment_globals(self):
        rlm_src = pathlib.Path(__file__).parent.parent / "rlm" / "core" / "rlm.py"
        text = rlm_src.read_text(encoding="utf-8")
        assert 'environment.globals["sub_rlm"]' in text
        assert "make_sub_rlm_fn(self)" in text

    def test_sub_rlm_py_has_docstring(self):
        sub_src = pathlib.Path(__file__).parent.parent / "rlm" / "core" / "sub_rlm.py"
        text = sub_src.read_text(encoding="utf-8")
        assert "Decomposição Explícita" in text or "sub_rlm" in text[:300]

    def test_SubRLMResult_dataclass_fields(self):
        from rlm.core.sub_rlm import SubRLMResult
        import dataclasses
        fields = {f.name for f in dataclasses.fields(SubRLMResult)}
        assert "task" in fields
        assert "answer" in fields
        assert "depth" in fields
        assert "elapsed_s" in fields
        assert "timed_out" in fields

    def test_depth_1_max_depth_3_allows_two_levels(self):
        """Verifica que cenário ETL (parent=0, filho=1, max=2) funciona."""
        from rlm.core.sub_rlm import make_sub_rlm_fn, SubRLMDepthError
        parent_depth0 = _make_parent_mock(depth=0, max_depth=2)
        mock_cls, _ = _make_mock_rlm_cls("dados limpos")
        fn = make_sub_rlm_fn(parent_depth0, _rlm_cls=mock_cls)
        # Deve funcionar: depth=0, child=1 < max=2
        result = fn("limpa CSV /tmp/vendas.csv")
        assert result == "dados limpos"
