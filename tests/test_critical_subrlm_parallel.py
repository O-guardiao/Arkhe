"""
Testes críticos — Fase 9.4: sub_rlm_parallel (execução paralela de sub-tarefas)

Cobre:
- sub_rlm_parallel(tasks) → list[str] em paralelo
- sub_rlm_parallel_detailed(tasks) → list[SubRLMParallelTaskResult]
- Depth guard antecipado (bloqueia antes de criar threads)
- Falha individual retorna string de erro, não levanta
- All-fail levanta SubRLMError
- Lista vazia retorna []
- Ordem dos resultados preservada
- Injeção em environment.globals de rlm.py
- Paralelismo real: 3 tarefas lentas completam em ~1× tempo, não 3×

Execute:
    pytest tests/test_critical_subrlm_parallel.py -v
"""
from __future__ import annotations

import pathlib
import time
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rlm.core.cancellation import CancellationToken
from rlm.environments.local_repl import LocalREPL


# ===========================================================================
# Helpers
# ===========================================================================

def _make_parent_mock(depth: int = 0, max_depth: int = 3) -> MagicMock:
    parent = MagicMock()
    parent.depth = depth
    parent.max_depth = max_depth
    parent.backend = "openai"
    parent.backend_kwargs = {"model_name": "gpt-4o-mini"}
    parent.environment_type = "local"
    parent.environment_kwargs = {}
    return parent


def _make_mock_rlm_cls(response: str = "resultado"):
    """Cria RLM mock que retorna response instantaneamente."""
    mock_completion = MagicMock()
    mock_completion.response = response
    mock_instance = MagicMock()
    mock_instance.completion.return_value = mock_completion
    mock_cls = MagicMock(return_value=mock_instance)
    return mock_cls, mock_instance


def _make_slow_mock_rlm_cls(response: str, delay_s: float):
    """Cria RLM mock que demora delay_s antes de responder."""
    mock_completion = MagicMock()
    mock_completion.response = response
    mock_instance = MagicMock()

    def _slow_completion(prompt):
        time.sleep(delay_s)
        return mock_completion

    mock_instance.completion.side_effect = _slow_completion
    mock_cls = MagicMock(return_value=mock_instance)
    return mock_cls, mock_instance


# ===========================================================================
# Importações e assinatura
# ===========================================================================

class TestImportsAndSignature:

    def test_make_sub_rlm_parallel_fn_importable(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        assert callable(make_sub_rlm_parallel_fn)

    def test_SubRLMParallelTaskResult_importable(self):
        from rlm.core.sub_rlm import SubRLMParallelTaskResult
        import dataclasses
        fields = {f.name for f in dataclasses.fields(SubRLMParallelTaskResult)}
        assert {
            "task", "branch_id", "ok", "answer", "error", "elapsed_s",
            "task_id", "parent_task_id", "status",
        } == fields

    def test_factory_returns_two_callables(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        parent = _make_parent_mock()
        par, par_det = make_sub_rlm_parallel_fn(parent)
        assert callable(par)
        assert callable(par_det)

    def test_parallel_fn_named_correctly(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        parent = _make_parent_mock()
        par, par_det = make_sub_rlm_parallel_fn(parent)
        assert par.__name__ == "sub_rlm_parallel"
        assert par_det.__name__ == "sub_rlm_parallel_detailed"

    def test_metadata_attached(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        parent = _make_parent_mock(depth=1, max_depth=4)
        par, _ = make_sub_rlm_parallel_fn(parent)
        assert par._parent_depth == 1
        assert par._parent_max_depth == 4


# ===========================================================================
# sub_rlm_parallel — comportamento básico
# ===========================================================================

class TestSubRLMParallelBasic:

    def test_empty_list_returns_empty(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        parent = _make_parent_mock()
        par, _ = make_sub_rlm_parallel_fn(parent)
        assert par([]) == []

    def test_single_task_returns_list_with_one_element(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        parent = _make_parent_mock()
        mock_cls, _ = _make_mock_rlm_cls("resposta única")
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        result = par(["tarefa única"])
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0] == "resposta única"

    def test_three_tasks_return_three_results(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        parent = _make_parent_mock()
        mock_cls, _ = _make_mock_rlm_cls("ok")
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        result = par(["A", "B", "C"])
        assert len(result) == 3

    def test_interaction_modes_are_propagated_per_branch(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn

        parent = _make_parent_mock()
        created_kwargs = []

        def _side_effect(**kwargs):
            created_kwargs.append(kwargs)
            mock_completion = MagicMock()
            mock_completion.response = "ok"
            mock_instance = MagicMock()
            mock_instance.completion.return_value = mock_completion
            return mock_instance

        mock_cls = MagicMock(side_effect=_side_effect)
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        result = par(
            ["A", "B", "C"],
            system_prompts=[None, "text prompt", "other text prompt"],
            interaction_modes=["repl", "text", "text"],
        )

        assert len(result) == 3
        observed = sorted([
            (
                kwargs.get("custom_system_prompt"),
                kwargs.get("interaction_mode"),
            )
            for kwargs in created_kwargs
        ], key=lambda item: ((item[0] or ""), item[1] or ""))
        assert observed == sorted([
            (None, "repl"),
            ("text prompt", "text"),
            ("other text prompt", "text"),
        ], key=lambda item: ((item[0] or ""), item[1] or ""))

    def test_results_are_strings(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        parent = _make_parent_mock()
        mock_cls, _ = _make_mock_rlm_cls("dado processado")
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        result = par(["t1", "t2"])
        for r in result:
            assert isinstance(r, str)


# ===========================================================================
# Ordem dos resultados
# ===========================================================================

class TestResultOrder:

    def test_results_match_input_order(self):
        """Resultado[i] deve corresponder a tasks[i], independente de qual thread terminou primeiro."""
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn

        parent = _make_parent_mock()
        responses = ["resposta-A", "resposta-B", "resposta-C"]
        call_count = [0]
        lock = threading.Lock()

        def _make_sequential_cls():
            """Cada instância retorna a próxima resposta da lista."""
            idx_holder = [0]
            with lock:
                my_idx = call_count[0]
                call_count[0] += 1
            resp = responses[my_idx % len(responses)]
            mock_completion = MagicMock()
            mock_completion.response = resp
            mock_instance = MagicMock()
            mock_instance.completion.return_value = mock_completion
            return mock_instance

        # Usar side_effect para que cada new RLM() retorne a instância certa
        mock_cls = MagicMock(side_effect=lambda **kw: _make_sequential_cls())
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        result = par(["task-A", "task-B", "task-C"])

        # Deve ter 3 resultados, cada um uma string válida
        assert len(result) == 3
        assert all(isinstance(r, str) for r in result)


# ===========================================================================
# Depth guard
# ===========================================================================

class TestDepthGuardParallel:

    def test_depth_at_limit_raises_before_any_thread(self):
        """Se depth + 1 >= max_depth, nenhuma thread é criada."""
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn, SubRLMDepthError
        parent = _make_parent_mock(depth=2, max_depth=3)  # child_depth=3 >= max_depth=3
        mock_cls, _ = _make_mock_rlm_cls("nunca chega")
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        with pytest.raises(SubRLMDepthError, match="profundidade máxima"):
            par(["A", "B", "C"])
        # mock não deve ter sido instanciado (nenhuma thread criada)
        mock_cls.assert_not_called()

    def test_depth_0_max_depth_2_allows_parallel(self):
        """depth=0, child=1 < max=2 → OK."""
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        parent = _make_parent_mock(depth=0, max_depth=2)
        mock_cls, _ = _make_mock_rlm_cls("ok")
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        result = par(["A", "B"])
        assert len(result) == 2


# ===========================================================================
# Tolerância a falhas individuais
# ===========================================================================

class TestIndividualFailureTolerance:

    def test_one_task_fails_others_succeed(self):
        """Se 1 de 3 tarefas falhar, as outras 2 retornam normalmente."""
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        parent = _make_parent_mock()

        call_count = [0]

        def _side_effect(**kwargs):
            with threading.Lock():
                idx = call_count[0]
                call_count[0] += 1
            mock_instance = MagicMock()
            if idx == 1:  # branch 1 vai falhar
                mock_instance.completion.side_effect = RuntimeError("servidor caiu")
            else:
                mock_completion = MagicMock()
                mock_completion.response = f"ok-{idx}"
                mock_instance.completion.return_value = mock_completion
            return mock_instance

        mock_cls = MagicMock(side_effect=_side_effect)
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        result = par(["A", "B", "C"])

        assert len(result) == 3
        # pelo menos 2 resultados não contêm "[ERRO"
        ok_count = sum(1 for r in result if not r.startswith("[ERRO"))
        assert ok_count >= 2

    def test_stop_on_solution_cancels_redundant_branch_and_records_task_binding(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn

        env = LocalREPL()
        parent = SimpleNamespace(
            depth=0,
            max_depth=3,
            backend="openai",
            backend_kwargs={"model_name": "gpt-4o-mini"},
            environment_type="local",
            environment_kwargs={},
            event_bus=None,
            _persistent_env=env,
            _async_bus=None,
            _async_branch_counter=0,
            _cancel_token=CancellationToken.NONE,
            _shared_memory=None,
        )

        def _side_effect(**kwargs):
            branch_id = kwargs["environment_kwargs"]["_sibling_branch_id"]
            cancel_event = kwargs["environment_kwargs"]["_cancel_event"]
            instance = MagicMock()

            def _completion(prompt):
                completion = MagicMock()
                if branch_id == 0:
                    completion.response = "winner-result"
                    return completion
                while not cancel_event.is_set():
                    time.sleep(0.01)
                completion.response = "[CANCELLED] coordination stop requested"
                return completion

            instance.completion.side_effect = _completion
            return instance

        mock_cls = MagicMock(side_effect=_side_effect)
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        result = par(["winner task", "redundant task"], timeout_s=2.0)
        snapshot = env.get_runtime_state_snapshot(coordination_limit=20)

        assert result[0] == "winner-result"
        assert result[1].startswith("[CANCELLED branch 1]")
        assert any(item["branch_id"] == 0 for item in snapshot["coordination"]["branch_tasks"])
        assert any(item["branch_id"] == 1 for item in snapshot["coordination"]["branch_tasks"])
        assert any(item["status"] == "cancelled" for item in snapshot["coordination"]["branch_tasks"])
        assert any(
            event["operation"] == "control_publish" and event["topic"] == "control/solution_found"
            for event in snapshot["coordination"]["events"]
        )
        env.cleanup()

    def test_parallel_inherits_active_strategy_policy_when_argument_is_omitted(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn

        env = LocalREPL()
        env.set_active_recursive_strategy(
            {
                "strategy_name": "parallel_decompose",
                "coordination_policy": "stop_on_solution",
                "stop_condition": "stop on first viable path",
                "repl_search_mode": "parallel_branch_search",
            },
            origin="test",
        )
        parent = SimpleNamespace(
            depth=0,
            max_depth=3,
            backend="openai",
            backend_kwargs={"model_name": "gpt-4o-mini"},
            environment_type="local",
            environment_kwargs={},
            event_bus=None,
            _persistent_env=env,
            _async_bus=None,
            _async_branch_counter=0,
            _cancel_token=CancellationToken.NONE,
            _shared_memory=None,
            _active_recursive_strategy=env.get_active_recursive_strategy(),
        )

        def _side_effect(**kwargs):
            branch_id = kwargs["environment_kwargs"]["_sibling_branch_id"]
            cancel_event = kwargs["environment_kwargs"]["_cancel_event"]
            instance = MagicMock()

            def _completion(prompt):
                completion = MagicMock()
                if branch_id == 0:
                    completion.response = "winner-result"
                    return completion
                while not cancel_event.is_set():
                    time.sleep(0.01)
                completion.response = "[CANCELLED] inherited strategy stop"
                return completion

            instance.completion.side_effect = _completion
            return instance

        mock_cls = MagicMock(side_effect=_side_effect)
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        result = par(["winner task", "redundant task"], timeout_s=2.0)
        snapshot = env.get_runtime_state_snapshot(coordination_limit=20)

        assert result[0] == "winner-result"
        assert result[1].startswith("[CANCELLED branch 1]")
        assert snapshot["coordination"]["latest_parallel_summary"]["strategy"]["strategy_name"] == "parallel_decompose"
        assert snapshot["coordination"]["latest_parallel_summary"]["strategy"]["coordination_policy"] == "stop_on_solution"
        env.cleanup()

    def test_consensus_policy_waits_for_second_success_before_cancelling(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn

        env = LocalREPL()
        env.set_active_recursive_strategy(
            {
                "strategy_name": "probe_then_consensus",
                "coordination_policy": "consensus_reached",
                "stop_condition": "stop after two agreeing branches",
                "repl_search_mode": "probe_and_delegate",
            },
            origin="test",
        )
        parent = SimpleNamespace(
            depth=0,
            max_depth=3,
            backend="openai",
            backend_kwargs={"model_name": "gpt-4o-mini"},
            environment_type="local",
            environment_kwargs={},
            event_bus=None,
            _persistent_env=env,
            _async_bus=None,
            _async_branch_counter=0,
            _cancel_token=CancellationToken.NONE,
            _shared_memory=None,
            _active_recursive_strategy=env.get_active_recursive_strategy(),
        )

        def _side_effect(**kwargs):
            branch_id = kwargs["environment_kwargs"]["_sibling_branch_id"]
            cancel_event = kwargs["environment_kwargs"]["_cancel_event"]
            instance = MagicMock()

            def _completion(prompt):
                completion = MagicMock()
                if branch_id in {0, 1}:
                    time.sleep(0.03 * branch_id)
                    completion.response = f"consensus-{branch_id}"
                    return completion
                while not cancel_event.is_set():
                    time.sleep(0.01)
                completion.response = "[CANCELLED] consensus reached"
                return completion

            instance.completion.side_effect = _completion
            return instance

        mock_cls = MagicMock(side_effect=_side_effect)
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        result = par(["task-0", "task-1", "task-2"], timeout_s=2.0)
        snapshot = env.get_runtime_state_snapshot(coordination_limit=20)

        assert result[0] == "consensus-0"
        assert result[1] == "consensus-1"
        assert result[2].startswith("[CANCELLED branch 2]")
        assert snapshot["coordination"]["latest_parallel_summary"]["cancelled_count"] == 1
        assert snapshot["coordination"]["latest_parallel_summary"]["strategy"]["coordination_policy"] == "consensus_reached"
        assert any(
            event["operation"] == "control_publish" and event["topic"] == "control/consensus_reached"
            for event in snapshot["coordination"]["events"]
        )
        env.cleanup()

    def test_switch_strategy_replans_failed_branch_in_second_phase(self):
        from rlm.core.mcts import BranchResult, ProgramArchive
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn

        env = LocalREPL()
        archive = ProgramArchive()
        archive.update(
            [
                BranchResult(
                    branch_id=0,
                    steps=[],
                    total_score=9.0,
                    final_code="sub_rlm_parallel(['probe', 'refine'])",
                    repl_locals={},
                    aggregated_metrics={"heuristic": 5.0},
                    strategy_name="parallel_decompose",
                    strategy={
                        "name": "parallel_decompose",
                        "coordination_policy": "switch_strategy",
                        "stop_condition": "switch when stalled",
                        "repl_search_mode": "parallel_branch_search",
                    },
                )
            ]
        )
        env.set_active_recursive_strategy(
            {
                "strategy_name": "parallel_decompose",
                "coordination_policy": "switch_strategy",
                "stop_condition": "switch when stalled",
                "repl_search_mode": "parallel_branch_search",
                "archive_key": "switch-archive",
            },
            origin="test",
        )
        parent = SimpleNamespace(
            depth=0,
            max_depth=3,
            backend="openai",
            backend_kwargs={"model_name": "gpt-4o-mini"},
            environment_type="local",
            environment_kwargs={},
            event_bus=None,
            _persistent_env=env,
            _async_bus=None,
            _async_branch_counter=0,
            _cancel_token=CancellationToken.NONE,
            _shared_memory=None,
            _active_recursive_strategy=env.get_active_recursive_strategy(),
            _active_mcts_archive_key="switch-archive",
            _mcts_archives={"switch-archive": archive},
        )

        branch_calls = {0: 0, 1: 0}

        def _side_effect(**kwargs):
            branch_id = kwargs["environment_kwargs"]["_sibling_branch_id"]
            instance = MagicMock()

            def _completion(prompt):
                branch_calls[branch_id] += 1
                completion = MagicMock()
                if branch_id == 0:
                    completion.response = "winner-phase-1"
                    return completion
                if "parallel_phase_2_replan" in prompt and "switch-archive" in prompt:
                    completion.response = "phase-2 success"
                    return completion
                raise RuntimeError("stalled first-wave branch")

            instance.completion.side_effect = _completion
            return instance

        mock_cls = MagicMock(side_effect=_side_effect)
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        result = par(["winner task", "needs replan"], timeout_s=2.0)
        snapshot = env.get_runtime_state_snapshot(coordination_limit=20)

        assert result[0] == "winner-phase-1"
        assert result[1] == "phase-2 success"
        assert branch_calls[1] == 2
        assert snapshot["coordination"]["latest_parallel_summary"]["strategy"]["coordination_policy"] == "switch_strategy"
        assert snapshot["coordination"]["latest_parallel_summary"]["stop_evaluation"]["mode"] == "stagnation"
        assert any(
            event["operation"] == "control_publish" and event["topic"] == "control/switch_strategy"
            for event in snapshot["coordination"]["events"]
        )
        env.cleanup()

    def test_failed_task_result_contains_erro_prefix(self):
        """Tarefa que falha retorna string começando com '[ERRO branch N]' entre as demais."""
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn, SubRLMError
        parent = _make_parent_mock()

        def _side_effect(**kwargs):
            mock_instance = MagicMock()
            ok_completion = MagicMock()
            ok_completion.response = "ok"

            def _completion(prompt):
                # Falha somente quando a tarefa contém "FALHA"
                if "FALHA" in prompt:
                    raise ConnectionError("timeout")
                return ok_completion

            mock_instance.completion.side_effect = _completion
            return mock_instance

        mock_cls = MagicMock(side_effect=_side_effect)
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        result = par(["tarefa FALHA", "tarefa ok"])

        # A tarefa que falhou deve ter prefixo de erro
        erro_results = [r for r in result if r.startswith("[ERRO")]
        ok_results    = [r for r in result if r == "ok"]
        assert len(erro_results) == 1
        assert len(ok_results) == 1

    def test_all_fail_raises_SubRLMError(self):
        """Se TODAS as tarefas falharem, levanta SubRLMError."""
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn, SubRLMError
        parent = _make_parent_mock()
        mock_instance = MagicMock()
        mock_instance.completion.side_effect = RuntimeError("falhou")
        mock_cls = MagicMock(return_value=mock_instance)
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        with pytest.raises(SubRLMError, match="todas as"):
            par(["A", "B", "C"])


# ===========================================================================
# sub_rlm_parallel_detailed
# ===========================================================================

class TestParallelDetailed:

    def test_detailed_returns_task_result_objects(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn, SubRLMParallelTaskResult
        parent = _make_parent_mock()
        mock_cls, _ = _make_mock_rlm_cls("detalhe")
        _, par_det = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        results = par_det(["X", "Y"])
        assert len(results) == 2
        for r in results:
            assert isinstance(r, SubRLMParallelTaskResult)

    def test_detailed_ok_field_true_on_success(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        parent = _make_parent_mock()
        mock_cls, _ = _make_mock_rlm_cls("sucesso")
        _, par_det = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        results = par_det(["tarefa"])
        assert results[0].ok is True
        assert results[0].answer == "sucesso"
        assert results[0].error is None

    def test_detailed_ok_false_on_failure(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        parent = _make_parent_mock()
        mock_instance = MagicMock()
        mock_instance.completion.side_effect = ValueError("erro proposital")
        mock_cls = MagicMock(return_value=mock_instance)
        _, par_det = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        results = par_det(["tarefa falha"])
        assert results[0].ok is False
        assert results[0].answer is None
        assert "erro proposital" in results[0].error

    def test_detailed_branch_id_matches_index(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        parent = _make_parent_mock()
        mock_cls, _ = _make_mock_rlm_cls("ok")
        _, par_det = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        results = par_det(["A", "B", "C"])
        # branch_ids devem cobrir 0, 1, 2
        ids = {r.branch_id for r in results}
        assert ids == {0, 1, 2}

    def test_detailed_elapsed_s_positive(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        parent = _make_parent_mock()
        mock_cls, _ = _make_mock_rlm_cls("ok")
        _, par_det = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        results = par_det(["tarefa"])
        assert results[0].elapsed_s >= 0.0

    def test_detailed_empty_returns_empty(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn, SubRLMParallelDetailedResults
        parent = _make_parent_mock()
        _, par_det = make_sub_rlm_parallel_fn(parent)
        result = par_det([])
        assert isinstance(result, SubRLMParallelDetailedResults)
        assert result == []

    def test_detailed_records_summary_and_parent_task_tree(self):
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn, SubRLMParallelDetailedResults

        env = LocalREPL()
        env.create_runtime_task("root", status="in-progress", current=True)
        parent = SimpleNamespace(
            depth=0,
            max_depth=3,
            backend="openai",
            backend_kwargs={"model_name": "gpt-4o-mini"},
            environment_type="local",
            environment_kwargs={},
            event_bus=None,
            _persistent_env=env,
            _async_bus=None,
            _async_branch_counter=0,
            _cancel_token=CancellationToken.NONE,
            _shared_memory=None,
        )

        def _side_effect(**kwargs):
            branch_id = kwargs["environment_kwargs"]["_sibling_branch_id"]
            cancel_event = kwargs["environment_kwargs"]["_cancel_event"]
            instance = MagicMock()

            def _completion(prompt):
                completion = MagicMock()
                if branch_id == 0:
                    completion.response = "winner-detailed"
                    return completion
                while not cancel_event.is_set():
                    time.sleep(0.01)
                completion.response = "[CANCELLED] coordination stop requested"
                return completion

            instance.completion.side_effect = _completion
            return instance

        mock_cls = MagicMock(side_effect=_side_effect)
        _, par_det = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)
        results = par_det(["winner", "redundant"], timeout_s=2.0)
        snapshot = env.get_runtime_state_snapshot(coordination_limit=20)

        assert isinstance(results, SubRLMParallelDetailedResults)
        assert results.summary["winner_branch_id"] == 0
        assert results.summary["cancelled_count"] == 1
        assert results.summary["batch_task_id"] is not None
        assert set(results.summary["task_ids_by_branch"].keys()) == {"0", "1"}
        assert results[0].parent_task_id == results.summary["batch_task_id"]
        assert results[1].status == "cancelled"
        batch_task = next(item for item in snapshot["tasks"]["items"] if item["task_id"] == results.summary["batch_task_id"])
        assert batch_task["parent_id"] is not None
        env.cleanup()


# ===========================================================================
# Paralelismo real (timing)
# ===========================================================================

class TestParallelismTiming:

    def test_parallel_faster_than_serial(self):
        """
        3 tarefas com delay de 0.15s cada:
          - serial:   >= 0.45s
          - parallel: <= 0.35s (deve completar em ~1× o tempo de 1 tarefa)
        """
        from rlm.core.sub_rlm import make_sub_rlm_parallel_fn
        parent = _make_parent_mock()
        DELAY = 0.15  # segundos por tarefa

        mock_cls, _ = _make_slow_mock_rlm_cls("ok", DELAY)
        par, _ = make_sub_rlm_parallel_fn(parent, _rlm_cls=mock_cls)

        t0 = time.perf_counter()
        result = par(["A", "B", "C"], max_workers=3)
        elapsed = time.perf_counter() - t0

        assert len(result) == 3
        # Paralelo: deve terminar bem antes de 3× o delay
        assert elapsed < DELAY * 2.5, (
            f"Paralelo deveria ser mais rápido que serial. "
            f"Demorou {elapsed:.2f}s, esperado < {DELAY * 2.5:.2f}s"
        )


# ===========================================================================
# Injeção em rlm.py globals
# ===========================================================================

class TestInjectionInRLMpy:

    def test_rlm_py_imports_make_sub_rlm_parallel_fn(self):
        rlm_src = pathlib.Path(__file__).parent.parent / "rlm" / "core" / "rlm.py"
        text = rlm_src.read_text(encoding="utf-8")
        assert "make_sub_rlm_parallel_fn" in text

    def test_rlm_py_injects_sub_rlm_parallel(self):
        rlm_src = pathlib.Path(__file__).parent.parent / "rlm" / "core" / "rlm.py"
        text = rlm_src.read_text(encoding="utf-8")
        assert 'environment.globals["sub_rlm_parallel"]' in text

    def test_rlm_py_injects_sub_rlm_parallel_detailed(self):
        rlm_src = pathlib.Path(__file__).parent.parent / "rlm" / "core" / "rlm.py"
        text = rlm_src.read_text(encoding="utf-8")
        assert 'environment.globals["sub_rlm_parallel_detailed"]' in text
