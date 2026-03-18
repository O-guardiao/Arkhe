"""
Testes críticos — MCTS Branching (Evolution 6.3)

Cobre:
- default_score_fn: todos os cenários de pontuação
- BranchResult: criação, repr, campos
- SandboxREPL: instanciação, isolamento, cleanup
- MCTSOrchestrator: construtor, run() com sucesso, pruning, all-fail, empty input
- generate_branch_variants: parsing, padding, fallback sem code fence
- Thread-safety: execução paralela de branches
- Event bus: emissão correta de eventos

Execute:
    pytest tests/test_mcts.py -v
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from rlm.core.mcts import (
    apply_search_replace_blocks,
    BranchResult,
    build_strategy_prompt,
    default_recursive_strategies,
    EvaluationStage,
    generate_recursive_strategies,
    MCTSOrchestrator,
    generate_diff_mutation_variants,
    ProgramArchive,
    parse_search_replace_blocks,
    RecursiveStrategy,
    SandboxREPL,
    default_score_fn,
    evolutionary_branch_search,
    generate_refined_branch_variants,
    generate_branch_variants,
    summarize_branch_feedback,
)
from rlm.core.types import REPLResult


# ===========================================================================
# default_score_fn
# ===========================================================================

class TestDefaultScoreFn:
    """Testes exaustivos para a função de scoring heurístico."""

    def test_perfect_score(self):
        """Código longo, sem erro, stdout com dígitos → máximo ~4.0."""
        score = default_score_fn("Result: 42", "", "x = some_function_with_long_name()")
        assert score == pytest.approx(4.0)

    def test_no_error_no_stdout(self):
        """Sem erro mas sem saída, código ≥30 chars → +2.0 -1.0 = 1.0."""
        score = default_score_fn("", "", "x = compute_something_really_big()")
        assert score == pytest.approx(1.0)

    def test_no_error_no_stdout_short_code(self):
        """Sem erro, sem saída, código curto (<30 chars) → +2.0 -1.0 -0.5."""
        score = default_score_fn("", "", "x = 1")
        assert score == pytest.approx(0.5)

    def test_error_in_stderr(self):
        """Stderr com 'Error' → -2.0 em vez de +2.0."""
        score = default_score_fn("output 123", "NameError: x not defined", "x = foo_with_a_very_long_name_here()")
        # has_error → -2.0, stdout com dígito → +1.0 +1.0 = 0.0
        assert score == pytest.approx(0.0)

    def test_traceback_in_stderr(self):
        """Stderr com 'Traceback' → -2.0."""
        score = default_score_fn("", "Traceback (most recent call last):\n  ...", "a" * 30)
        # -2.0 (error) -1.0 (no stdout) = -3.0
        assert score == pytest.approx(-3.0)

    def test_stdout_no_digits(self):
        """Stdout não-vazio sem dígitos → +2.0 +1.0 = 3.0 (código longo)."""
        score = default_score_fn("hello world", "", "print('hello world')  # padded")
        assert score == pytest.approx(3.0)

    def test_stdout_only_whitespace(self):
        """Stdout com apenas espaços → tratado como vazio."""
        score = default_score_fn("   \n  ", "", "x = 1")
        # no error +2.0, stdout vazio -1.0, short code -0.5 = 0.5
        assert score == pytest.approx(0.5)

    def test_empty_stderr_not_error(self):
        """stderr vazio → sem penalidade."""
        score = default_score_fn("42", "", "x" * 30)
        assert score == pytest.approx(4.0)

    def test_none_stderr(self):
        """stderr=None (edge case de REPLResult) → sem crash."""
        score = default_score_fn("42", None, "x" * 30)
        # bool(None and ...) = False → no error
        assert score == pytest.approx(4.0)

    def test_stderr_without_error_keyword(self):
        """stderr com texto mas sem 'Error'/'Traceback' → sem penalidade."""
        score = default_score_fn("42", "Warning: deprecated", "x" * 30)
        assert score == pytest.approx(4.0)

    def test_minimum_score(self):
        """Pior cenário: error + no stdout + short code → -2.0 -1.0 -0.5 = -3.5."""
        score = default_score_fn("", "Traceback Error", "x=1")
        assert score == pytest.approx(-3.5)


# ===========================================================================
# BranchResult
# ===========================================================================

class TestBranchResult:
    def test_creation(self):
        br = BranchResult(
            branch_id=0,
            steps=[{"code": "x=1", "stdout": "1", "stderr": "", "score": 4.0}],
            total_score=4.0,
            final_code="x=1",
            repl_locals={"x": 1},
        )
        assert br.branch_id == 0
        assert br.total_score == 4.0
        assert len(br.steps) == 1
        assert br.repl_locals == {"x": 1}

    def test_repr(self):
        br = BranchResult(0, [{}], 3.5, "code", {})
        r = repr(br)
        assert "id=0" in r
        assert "score=3.50" in r
        assert "steps=1" in r

    def test_negative_score(self):
        br = BranchResult(2, [], -999, "", {})
        assert br.total_score == -999
        assert "id=2" in repr(br)


# ===========================================================================
# SandboxREPL
# ===========================================================================

class TestSandboxREPL:
    def test_creation_and_branch_id(self):
        """SandboxREPL recebe branch_id e cria temp_dir."""
        with SandboxREPL(branch_id=7) as s:
            assert s.branch_id == 7
            assert hasattr(s, "temp_dir")

    def test_repr(self):
        with SandboxREPL(branch_id=3) as s:
            r = repr(s)
            assert "branch=3" in r
            assert "tmpdir=" in r

    def test_isolation_between_branches(self):
        """Duas SandboxREPLs não compartilham namespace."""
        with SandboxREPL(branch_id=0) as a, SandboxREPL(branch_id=1) as b:
            a.execute_code("x = 42")
            b.execute_code("x = 99")
            assert a.locals.get("x") == 42
            assert b.locals.get("x") == 99

    def test_execute_code_returns_repl_result(self):
        with SandboxREPL(branch_id=0) as s:
            result = s.execute_code("print('hello')")
            assert isinstance(result, REPLResult)
            assert "hello" in result.stdout

    def test_execute_code_captures_stderr(self):
        with SandboxREPL(branch_id=0) as s:
            result = s.execute_code("1/0")
            assert "ZeroDivisionError" in result.stderr

    def test_cleanup_clears_locals(self):
        """Após cleanup, locals e globals ficam vazios."""
        s = SandboxREPL(branch_id=0)
        s.setup()
        s.execute_code("x = 42")
        assert "x" in s.locals
        s.cleanup()
        assert len(s.locals) == 0

    def test_context_manager_cleanup(self):
        """__exit__ chama cleanup."""
        import os
        with SandboxREPL(branch_id=0) as s:
            tmpdir = s.temp_dir
            assert os.path.exists(tmpdir)
        # Após sair do with, temp_dir é removido
        assert not os.path.exists(tmpdir)


# ===========================================================================
# MCTSOrchestrator — Constructor
# ===========================================================================

class TestMCTSOrchestratorInit:
    def test_defaults(self):
        orch = MCTSOrchestrator()
        assert orch.branches == 3
        assert orch.max_depth == 2
        assert orch.score_fn is default_score_fn
        assert orch.event_bus is None

    def test_min_branches(self):
        """branches < 1 → clamped to 1."""
        orch = MCTSOrchestrator(branches=0)
        assert orch.branches == 1
        orch2 = MCTSOrchestrator(branches=-5)
        assert orch2.branches == 1

    def test_min_depth(self):
        """max_depth < 1 → clamped to 1."""
        orch = MCTSOrchestrator(max_depth=0)
        assert orch.max_depth == 1

    def test_custom_score_fn(self):
        fn = lambda s, e, c: 99.0
        orch = MCTSOrchestrator(score_fn=fn)
        assert orch.score_fn is fn

    def test_evaluation_stages_defaults_to_empty(self):
        orch = MCTSOrchestrator()
        assert orch.evaluation_stages == []


# ===========================================================================
# MCTSOrchestrator.run() — com mock do SandboxREPL
# ===========================================================================

def _make_mock_repl(results_by_code=None, default_stdout="ok", default_stderr=""):
    """Cria um mock de SandboxREPL que retorna resultados configuráveis."""
    if results_by_code is None:
        results_by_code = {}

    class MockSandboxREPL:
        def __init__(self, branch_id, **kwargs):
            self.branch_id = branch_id
            self.locals = {}

        def __enter__(self):
            self.setup()
            return self

        def __exit__(self, *args):
            return False

        def setup(self):
            self.locals = {}

        def execute_code(self, code):
            if code in results_by_code:
                r = results_by_code[code]
                # Atualiza locals se especificado
                if "_locals" in r:
                    self.locals.update(r["_locals"])
                return REPLResult(
                    stdout=r.get("stdout", default_stdout),
                    stderr=r.get("stderr", default_stderr),
                    locals=self.locals.copy(),
                )
            return REPLResult(
                stdout=default_stdout,
                stderr=default_stderr,
                locals=self.locals.copy(),
            )

    return MockSandboxREPL


class TestMCTSOrchestratorRun:
    """Testes de run() usando mock para SandboxREPL."""

    @patch("rlm.core.mcts.SandboxREPL")
    def test_single_branch_success(self, mock_cls):
        """Uma branch com sucesso → retorna essa branch."""
        mock_cls.side_effect = _make_mock_repl(
            results_by_code={"print(42)": {"stdout": "42", "stderr": "", "_locals": {"x": 42}}}
        )
        orch = MCTSOrchestrator(branches=1, max_depth=1)
        result = orch.run([["print(42)"]])

        assert isinstance(result, BranchResult)
        assert result.branch_id == 0
        assert result.total_score > 0

    @patch("rlm.core.mcts.SandboxREPL")
    def test_best_branch_wins(self, mock_cls):
        """Com N branches, a de maior score é selecionada."""
        mock_cls.side_effect = _make_mock_repl(
            results_by_code={
                "bad": {"stdout": "", "stderr": "Error: fail"},
                "good": {"stdout": "Result: 42", "stderr": ""},
                "mid": {"stdout": "ok", "stderr": ""},
            }
        )
        orch = MCTSOrchestrator(branches=3, max_depth=1)
        result = orch.run([["bad"], ["good"], ["mid"]])

        assert result.branch_id == 1  # "good" tem maior score

    @patch("rlm.core.mcts.SandboxREPL")
    def test_pruning_first_step_negative(self, mock_cls):
        """Branch com score ≤ 0 no primeiro step → pruned (-999)."""
        mock_cls.side_effect = _make_mock_repl(
            results_by_code={
                "error_code": {"stdout": "", "stderr": "Traceback Error", "_locals": {}},
                "good_code": {"stdout": "42", "stderr": "", "_locals": {"x": 42}},
            }
        )
        orch = MCTSOrchestrator(branches=2, max_depth=2)
        result = orch.run([
            ["error_code", "more_code"],
            ["good_code", "good_code"],
        ])

        assert result.branch_id == 1  # branch 0 foi pruned
        assert result.total_score > -999

    @patch("rlm.core.mcts.SandboxREPL")
    def test_pruning_skips_remaining_steps(self, mock_cls):
        """Branch pruned no step 0 não executa step 1."""
        call_log = []

        class TrackingREPL:
            def __init__(self, branch_id, **kw):
                self.branch_id = branch_id
                self.locals = {}
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def execute_code(self, code):
                call_log.append((self.branch_id, code))
                if code == "fail":
                    return REPLResult(stdout="", stderr="Traceback here", locals={})
                return REPLResult(stdout="42", stderr="", locals={})

        mock_cls.side_effect = TrackingREPL
        orch = MCTSOrchestrator(branches=1, max_depth=3)
        orch.run([["fail", "step2", "step3"]])

        # Apenas "fail" executou, step2/step3 nunca rodaram
        branch0_calls = [code for bid, code in call_log if bid == 0]
        assert branch0_calls == ["fail"]

    @patch("rlm.core.mcts.SandboxREPL")
    def test_all_branches_fail_returns_least_bad(self, mock_cls):
        """Todas as branches pruned → retorna a menos ruim (todas -999)."""
        mock_cls.side_effect = _make_mock_repl(
            results_by_code={
                "fail_a": {"stdout": "", "stderr": "Error a"},
                "fail_b": {"stdout": "", "stderr": "Error b"},
            }
        )
        orch = MCTSOrchestrator(branches=2, max_depth=1)
        result = orch.run([["fail_a"], ["fail_b"]])

        assert result is not None
        assert result.total_score == -999

    @patch("rlm.core.mcts.SandboxREPL")
    def test_max_depth_limits_steps(self, mock_cls):
        """max_depth=2 → apenas 2 steps executam mesmo com 5 code blocks."""
        call_log = []

        class TrackingREPL:
            def __init__(self, branch_id, **kw):
                self.branch_id = branch_id
                self.locals = {}
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def execute_code(self, code):
                call_log.append(code)
                return REPLResult(stdout="42", stderr="", locals={})

        mock_cls.side_effect = TrackingREPL
        orch = MCTSOrchestrator(branches=1, max_depth=2)
        orch.run([["s1", "s2", "s3", "s4", "s5"]])

        assert call_log == ["s1", "s2"]

    @patch("rlm.core.mcts.SandboxREPL")
    def test_exception_in_branch_returns_minus_999(self, mock_cls):
        """Exception não-tratada em _run_branch → BranchResult com -999."""
        class ExplodingREPL:
            def __init__(self, branch_id, **kw):
                self.branch_id = branch_id
                self.locals = {}
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def execute_code(self, code):
                raise RuntimeError("kaboom")

        mock_cls.side_effect = ExplodingREPL
        orch = MCTSOrchestrator(branches=1, max_depth=1)
        result = orch.run([["code"]])

        assert result.total_score == -999
        assert result.steps[0].get("error") == "kaboom"

    @patch("rlm.core.mcts.SandboxREPL")
    def test_empty_branch_code_blocks_raises(self, mock_cls):
        """run([]) → ValueError de max() em sequência vazia."""
        orch = MCTSOrchestrator(branches=1, max_depth=1)
        with pytest.raises(ValueError):
            orch.run([])

    @patch("rlm.core.mcts.SandboxREPL")
    def test_event_bus_emits_events(self, mock_cls):
        """Event bus recebe mcts_branch_done e mcts_selected."""
        mock_cls.side_effect = _make_mock_repl(
            results_by_code={"c1": {"stdout": "42", "stderr": ""}}
        )
        bus = MagicMock()
        orch = MCTSOrchestrator(branches=1, max_depth=1, event_bus=bus)
        orch.run([["c1"]])

        events = [call[0][0] for call in bus.emit.call_args_list]
        assert "mcts_branch_done" in events
        assert "mcts_selected" in events

    @patch("rlm.core.mcts.SandboxREPL")
    def test_event_bus_prune_event(self, mock_cls):
        """Branch pruned → event bus recebe mcts_prune."""
        mock_cls.side_effect = _make_mock_repl(
            results_by_code={"fail": {"stdout": "", "stderr": "Traceback oops"}}
        )
        bus = MagicMock()
        orch = MCTSOrchestrator(branches=1, max_depth=1, event_bus=bus)
        orch.run([["fail"]])

        events = [call[0][0] for call in bus.emit.call_args_list]
        assert "mcts_prune" in events

    @patch("rlm.core.mcts.SandboxREPL")
    def test_repl_locals_captured(self, mock_cls):
        """repl_locals do BranchResult contém variáveis criadas."""
        mock_cls.side_effect = _make_mock_repl(
            results_by_code={
                "x = 42": {"stdout": "42", "stderr": "", "_locals": {"x": 42}},
            }
        )
        orch = MCTSOrchestrator(branches=1, max_depth=1)
        result = orch.run([["x = 42"]])
        assert result.repl_locals.get("x") == 42
        assert result.aggregated_metrics["heuristic"] > 0

    @patch("rlm.core.mcts.SandboxREPL")
    def test_steps_truncate_output(self, mock_cls):
        """stdout/stderr nos steps são truncados em 500 chars."""
        long_out = "A" * 1000
        mock_cls.side_effect = _make_mock_repl(
            results_by_code={"c": {"stdout": long_out, "stderr": ""}}
        )
        orch = MCTSOrchestrator(branches=1, max_depth=1)
        result = orch.run([["c"]])
        assert len(result.steps[0]["stdout"]) == 500

    @patch("rlm.core.mcts.SandboxREPL")
    def test_custom_score_fn_used(self, mock_cls):
        """score_fn customizada é realmente usada, não a default."""
        mock_cls.side_effect = _make_mock_repl()
        custom_fn = lambda s, e, c: 77.0
        orch = MCTSOrchestrator(branches=1, max_depth=1, score_fn=custom_fn)
        result = orch.run([["code"]])
        assert result.total_score == pytest.approx(77.0)

    @patch("rlm.core.mcts.SandboxREPL")
    def test_cumulative_score_across_steps(self, mock_cls):
        """Score total = soma dos scores de cada step."""
        mock_cls.side_effect = _make_mock_repl(
            default_stdout="42", default_stderr=""
        )
        # default_score_fn com stdout="42", stderr="", code padrão
        orch = MCTSOrchestrator(branches=1, max_depth=3)
        result = orch.run([["c1", "c2", "c3"]])
        # Cada step score = 4.0 (short code -0.5 se <30 chars) → 3.5 cada
        # c1, c2, c3 têm 2 chars → -0.5 cada → score per step = 3.5
        assert result.total_score == pytest.approx(3.5 * 3)

    @patch("rlm.core.mcts.SandboxREPL")
    def test_top_results_returns_sorted_branches(self, mock_cls):
        mock_cls.side_effect = _make_mock_repl(
            results_by_code={
                "bad": {"stdout": "", "stderr": "Traceback bad"},
                "mid": {"stdout": "ok", "stderr": ""},
                "good": {"stdout": "42", "stderr": ""},
            }
        )
        orch = MCTSOrchestrator(branches=3, max_depth=1)
        orch.run([["bad"], ["mid"], ["good"]])

        ranked = orch.top_results(2, include_pruned=True)
        assert [branch.branch_id for branch in ranked] == [2, 1]

    @patch("rlm.core.mcts.SandboxREPL")
    def test_evaluation_stage_contributes_to_score(self, mock_cls):
        """Stages extras somam ao score e ficam registrados nas métricas."""
        mock_cls.side_effect = _make_mock_repl(
            results_by_code={"good": {"stdout": "42", "stderr": "", "_locals": {"x": 42}}}
        )
        stage = EvaluationStage(
            name="has_x",
            evaluator=lambda snapshot: 2.0 if snapshot["locals"].get("x") == 42 else -1.0,
            weight=1.5,
        )
        orch = MCTSOrchestrator(branches=1, max_depth=1, evaluation_stages=[stage])
        result = orch.run([["good"]])

        assert result.total_score == pytest.approx(3.5 + 3.0)
        assert result.aggregated_metrics["has_x"] == pytest.approx(2.0)
        assert result.steps[0]["metrics"]["has_x"] == pytest.approx(2.0)

    @patch("rlm.core.mcts.SandboxREPL")
    def test_evaluation_stage_can_prune_branch(self, mock_cls):
        """Stage com threshold falhando derruba a branch cedo."""
        mock_cls.side_effect = _make_mock_repl(
            results_by_code={"good": {"stdout": "42", "stderr": "", "_locals": {"x": 42}}}
        )
        reject_stage = EvaluationStage(
            name="reject",
            evaluator=lambda snapshot: -1.0,
            min_score=0.0,
        )
        accept_stage = EvaluationStage(
            name="accept",
            evaluator=lambda snapshot: 1.0,
        )
        orch = MCTSOrchestrator(branches=2, max_depth=1, evaluation_stages=[reject_stage, accept_stage])
        result = orch.run([["good"], ["good"]])

        assert result.total_score == -999
        assert result.pruned_reason == "stage:reject"

    @patch("rlm.core.mcts.SandboxREPL")
    def test_stage_events_emitted(self, mock_cls):
        """Event bus recebe eventos de score e prune de stages."""
        mock_cls.side_effect = _make_mock_repl(
            results_by_code={"good": {"stdout": "42", "stderr": "", "_locals": {"x": 42}}}
        )
        bus = MagicMock()
        stage = EvaluationStage(
            name="reject",
            evaluator=lambda snapshot: -1.0,
            min_score=0.0,
        )
        orch = MCTSOrchestrator(branches=1, max_depth=1, event_bus=bus, evaluation_stages=[stage])
        orch.run([["good"]])

        events = [call[0][0] for call in bus.emit.call_args_list]
        assert "mcts_stage_scored" in events
        assert "mcts_stage_prune" in events


# ===========================================================================
# MCTSOrchestrator — Thread safety
# ===========================================================================

class TestMCTSOrchestratorThreadSafety:
    @patch("rlm.core.mcts.SandboxREPL")
    def test_parallel_branches_no_race(self, mock_cls):
        """N branches concorrentes não produzem race condition."""
        counter = {"value": 0}
        lock = threading.Lock()

        class ThreadSafeREPL:
            def __init__(self, branch_id, **kw):
                self.branch_id = branch_id
                self.locals = {}
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def execute_code(self, code):
                with lock:
                    counter["value"] += 1
                return REPLResult(stdout="42", stderr="", locals={})

        mock_cls.side_effect = ThreadSafeREPL
        orch = MCTSOrchestrator(branches=5, max_depth=2)
        result = orch.run([["c1", "c2"]] * 5)

        assert counter["value"] == 10  # 5 branches × 2 steps
        assert result is not None
        assert result.total_score > -999


# ===========================================================================
# generate_branch_variants
# ===========================================================================

class TestGenerateBranchVariants:
    def test_parses_fenced_code_blocks(self):
        """Blocos ```python separados por ---BRANCH--- são parseados."""
        response = (
            "```python\nx = 1\nprint(x)\n```\n"
            "---BRANCH---\n"
            "```python\ny = 2\nprint(y)\n```\n"
            "---BRANCH---\n"
            "```python\nz = 3\nprint(z)\n```"
        )
        mock_llm = lambda prompt: response
        result = generate_branch_variants("task", 3, mock_llm)

        assert len(result) == 3
        assert "x = 1" in result[0]
        assert "y = 2" in result[1]
        assert "z = 3" in result[2]

    def test_padding_when_fewer_variants(self):
        """Se LLM retorna menos variantes, pad com fallback."""
        response = "```python\nx = 1\n```"
        mock_llm = lambda prompt: response
        result = generate_branch_variants("task", 3, mock_llm)

        assert len(result) == 3
        assert "x = 1" in result[0]
        # Padding branches têm texto indicativo
        assert "Branch" in result[1]
        assert "Branch" in result[2]

    def test_trimming_when_more_variants(self):
        """Se LLM retorna mais variantes, trim para n_variants."""
        blocks = "---BRANCH---".join(
            [f"```python\nx = {i}\n```" for i in range(10)]
        )
        mock_llm = lambda prompt: blocks
        result = generate_branch_variants("task", 3, mock_llm)

        assert len(result) == 3

    def test_raw_content_fallback(self):
        """Sem code fence → usa texto cru como código."""
        response = "x = 1\n---BRANCH---\ny = 2"
        mock_llm = lambda prompt: response
        result = generate_branch_variants("task", 2, mock_llm)

        assert len(result) == 2
        assert result[0].strip() == "x = 1"
        assert result[1].strip() == "y = 2"

    def test_empty_response(self):
        """Resposta vazia → tudo padding."""
        mock_llm = lambda prompt: ""
        result = generate_branch_variants("task", 3, mock_llm)
        assert len(result) == 3
        for r in result:
            assert "Branch" in r

    def test_mixed_fenced_and_raw(self):
        """Mistura de blocos com e sem fence."""
        response = (
            "```python\nx = 1\n```\n"
            "---BRANCH---\n"
            "y = 2\nprint(y)"
        )
        mock_llm = lambda prompt: response
        result = generate_branch_variants("task", 2, mock_llm)

        assert len(result) == 2
        assert "x = 1" in result[0]
        # Segundo bloco: raw fallback
        assert "y = 2" in result[1]

    def test_prompt_contains_task(self):
        """O prompt enviado ao LLM contém a task e n_variants."""
        captured = {}
        def mock_llm(prompt):
            captured["prompt"] = prompt
            return "```python\nx=1\n```"

        generate_branch_variants("calcular fibonacci", 5, mock_llm)
        assert "calcular fibonacci" in captured["prompt"]
        assert "5" in captured["prompt"]

    def test_code_fence_without_python_tag(self):
        """Code fence sem 'python' label → ainda é parseado."""
        response = "```\nx = 1\nprint(x)\n```"
        mock_llm = lambda prompt: response
        result = generate_branch_variants("task", 1, mock_llm)

        assert len(result) == 1
        assert "x = 1" in result[0]


class TestEvolutionaryHelpers:
    def test_generate_recursive_strategies_parses_json(self):
        result = generate_recursive_strategies(
            "solve a novel problem",
            1,
            lambda prompt: '[{"name":"probe","recursion_prompt":"probe first","decomposition_plan":["probe","delegate"],"coordination_policy":"stop_on_solution","stop_condition":"stop when solved","repl_search_mode":"probe","meta_prompt":"be empirical"}]',
        )
        assert len(result) == 1
        assert result[0].name == "probe"

    def test_build_strategy_prompt_contains_recursive_fields(self):
        strategy = RecursiveStrategy(
            name="parallel",
            recursion_prompt="decompose",
            decomposition_plan=["step a", "step b"],
            coordination_policy="stop_on_solution",
            stop_condition="stop early",
            repl_search_mode="parallel_branch_search",
            meta_prompt="test",
        )
        prompt = build_strategy_prompt("solve task", strategy)
        assert "Recursive strategy name: parallel" in prompt
        assert "Coordination policy: stop_on_solution" in prompt

    def test_parse_and_apply_search_replace_blocks(self):
        raw = """<<<<<<< SEARCH
print('old')
=======
print('new')
>>>>>>> REPLACE"""
        blocks = parse_search_replace_blocks(raw)
        assert blocks == [("print('old')", "print('new')")]
        updated = apply_search_replace_blocks("x = 1\nprint('old')\n", blocks)
        assert "print('new')" in updated

    def test_program_archive_keeps_best_per_niche(self):
        archive = ProgramArchive(max_size=4, niche_fn=lambda branch: branch.final_code.split("(")[0])
        worse = BranchResult(0, [], 2.0, "print('a')", {}, {"heuristic": 2.0})
        better = BranchResult(1, [], 5.0, "print('a')", {}, {"heuristic": 5.0})
        other = BranchResult(2, [], 4.0, "value = 1", {}, {"heuristic": 4.0})

        archive.update([worse, better, other])
        sampled = archive.sample()

        assert [branch.total_score for branch in sampled] == [5.0, 4.0]

    def test_summarize_branch_feedback_renders_metrics(self):
        branch = BranchResult(
            branch_id=1,
            steps=[],
            total_score=7.5,
            final_code="print(42)",
            repl_locals={},
            aggregated_metrics={"heuristic": 3.5, "simplicity": 2.0},
        )
        summary = summarize_branch_feedback([branch])
        assert "Branch 1" in summary
        assert "heuristic=3.50" in summary
        assert "simplicity=2.00" in summary

    def test_generate_refined_branch_variants_includes_feedback(self):
        branch = BranchResult(
            branch_id=0,
            steps=[],
            total_score=5.0,
            final_code="print('elite')",
            repl_locals={},
            aggregated_metrics={"heuristic": 5.0},
        )
        captured = {}

        def mock_llm(prompt):
            captured["prompt"] = prompt
            return "```python\nprint('new idea')\n```"

        result = generate_refined_branch_variants("solve task", [branch], 1, mock_llm)
        assert "Current elite code" in captured["prompt"]
        assert "print('elite')" in captured["prompt"]
        assert result == ["print('new idea')"]

    def test_generate_diff_mutation_variants_applies_search_replace(self):
        elite = BranchResult(
            branch_id=0,
            steps=[],
            total_score=5.0,
            final_code="value = 1\nprint(value)",
            repl_locals={},
            aggregated_metrics={"heuristic": 5.0},
        )

        result = generate_diff_mutation_variants(
            "improve candidate",
            [elite],
            1,
            lambda prompt: """<<<<<<< SEARCH
value = 1
=======
value = 2
>>>>>>> REPLACE""",
        )
        assert result == ["value = 2\nprint(value)"]

    @patch("rlm.core.mcts.SandboxREPL")
    def test_evolutionary_branch_search_uses_second_round_feedback(self, mock_cls):
        mock_cls.side_effect = _make_mock_repl(
            results_by_code={
                "print('draft')": {"stdout": "draft", "stderr": ""},
                "print('improved 42')": {"stdout": "42", "stderr": "", "_locals": {"answer": 42}},
            }
        )
        prompts = []

        def mock_llm(prompt):
            prompts.append(prompt)
            if "Return a JSON array only." in prompt:
                return '[{"name":"probe","recursion_prompt":"probe first","decomposition_plan":["probe"],"coordination_policy":"stop_on_solution","stop_condition":"stop when solved","repl_search_mode":"probe","meta_prompt":"empirical"}]'
            if "Return 1 strategy JSON objects" in prompt:
                return '[{"name":"refine","recursion_prompt":"refine strategy","decomposition_plan":["refine"],"coordination_policy":"switch_strategy","stop_condition":"switch when stalled","repl_search_mode":"serial","meta_prompt":"improve"}]'
            if "Current elite code" in prompt:
                return """<<<<<<< SEARCH
print('draft')
=======
print('improved 42')
>>>>>>> REPLACE"""
            return "```python\nprint('draft')\n```"

        orch = MCTSOrchestrator(branches=1, max_depth=1)
        result = evolutionary_branch_search(
            "find something novel",
            1,
            mock_llm,
            orch,
            rounds=2,
            elite_count=1,
        )

        assert len(result["history"]) == 2
        assert result["best_branch"].final_code == "print('improved 42')"
        assert result["best_branch"].strategy_name == "refine"
        assert any("Current elite code" in prompt for prompt in prompts)

    @patch("rlm.core.mcts.SandboxREPL")
    def test_evolutionary_branch_search_updates_archive(self, mock_cls):
        mock_cls.side_effect = _make_mock_repl(
            results_by_code={"print('42')": {"stdout": "42", "stderr": ""}}
        )
        archive = ProgramArchive(max_size=4)
        orch = MCTSOrchestrator(branches=1, max_depth=1)

        result = evolutionary_branch_search(
            "find novelty",
            1,
            lambda prompt: (
                '[{"name":"probe","recursion_prompt":"probe","decomposition_plan":["probe"],"coordination_policy":"stop_on_solution","stop_condition":"stop","repl_search_mode":"probe","meta_prompt":""}]'
                if "Return a JSON array only." in prompt
                else "```python\nprint('42')\n```"
            ),
            orch,
            rounds=1,
            elite_count=1,
            archive=archive,
        )

        assert result["archive"].size() >= 1


# ===========================================================================
# Integração: SandboxREPL real (não mockado)
# ===========================================================================

class TestSandboxREPLIntegration:
    """Testes que usam SandboxREPL real com execute_code."""

    def test_arithmetic(self):
        with SandboxREPL(branch_id=0) as s:
            r = s.execute_code("result = 2 + 3\nprint(result)")
            assert "5" in r.stdout
            assert s.locals["result"] == 5

    def test_multiline_code(self):
        with SandboxREPL(branch_id=0) as s:
            code = "nums = [1, 2, 3]\ntotal = sum(nums)\nprint(total)"
            r = s.execute_code(code)
            assert "6" in r.stdout

    def test_error_captured(self):
        with SandboxREPL(branch_id=0) as s:
            r = s.execute_code("undefined_var")
            assert "NameError" in r.stderr

    def test_state_persists_across_executions(self):
        with SandboxREPL(branch_id=0) as s:
            s.execute_code("x = 10")
            r = s.execute_code("print(x * 2)")
            assert "20" in r.stdout

    def test_two_sandboxes_independent(self):
        """Variáveis de uma sandbox não vazam para outra."""
        with SandboxREPL(branch_id=0) as a:
            a.execute_code("secret = 'only_in_a'")

        with SandboxREPL(branch_id=1) as b:
            r = b.execute_code("print(secret)")
            assert "NameError" in r.stderr


# ===========================================================================
# Integração: MCTSOrchestrator com SandboxREPL real
# ===========================================================================

class TestMCTSOrchestratorIntegration:
    """Testes end-to-end com REPL real (sem mock)."""

    def test_simple_run(self):
        orch = MCTSOrchestrator(branches=2, max_depth=1)
        result = orch.run([
            ["print(2 + 2)"],
            ["print(3 + 3)"],
        ])
        assert isinstance(result, BranchResult)
        assert result.total_score > -999

    def test_best_branch_selected(self):
        orch = MCTSOrchestrator(branches=2, max_depth=1)
        result = orch.run([
            ["undefined_var"],        # NameError → score negativo
            ["print(42)"],            # Sucesso → score positivo
        ])
        # Branch 1 deve vencer
        assert result.branch_id == 1
        assert result.total_score > 0

    def test_pruned_error_branch(self):
        """Branch com erro no step 0 é pruned, branch boa vence."""
        orch = MCTSOrchestrator(branches=2, max_depth=2)
        result = orch.run([
            ["1/0", "print('never')"],        # ZeroDivisionError → pruned
            ["x = 42\nprint(x)", "print(x)"], # Sucesso
        ])
        assert result.branch_id == 1

    def test_all_branches_error(self):
        """Todas as branches com erro → retorna alguma (não crash)."""
        orch = MCTSOrchestrator(branches=2, max_depth=1)
        result = orch.run([
            ["undefined_a"],
            ["undefined_b"],
        ])
        assert result is not None
        assert isinstance(result, BranchResult)

    def test_multi_step_accumulation(self):
        """Score é acumulado ao longo de múltiplos steps."""
        orch = MCTSOrchestrator(branches=1, max_depth=3)
        result = orch.run([
            ["x = 1\nprint(x)", "x += 1\nprint(x)", "x += 1\nprint(x)"]
        ])
        assert len(result.steps) == 3
        assert result.total_score > 0
        # Cada step tem score individual registrado
        for step in result.steps:
            assert "score" in step

    def test_event_bus_real_run(self):
        """Event bus recebe eventos em execução real."""
        bus = MagicMock()
        orch = MCTSOrchestrator(branches=2, max_depth=1, event_bus=bus)
        orch.run([["print(1)"], ["print(2)"]])

        events = [call[0][0] for call in bus.emit.call_args_list]
        assert "mcts_selected" in events
        assert events.count("mcts_branch_done") == 2


# ===========================================================================
# Bug: run([]) → ValueError não tratado
# ===========================================================================

class TestKnownBugs:
    def test_empty_code_blocks_crashes(self):
        """run([]) falha cedo com erro explícito."""
        orch = MCTSOrchestrator()
        with pytest.raises(ValueError, match="at least one branch"):
            orch.run([])

    @patch("rlm.core.mcts.SandboxREPL")
    def test_all_none_results_crashes(self, mock_cls):
        """BUG POTENCIAL: se todas as futures falharem com exceções
        que não são capturadas, results seria [None] e max() crasharia.
        Na implementação atual, exceptions são capturadas → BranchResult(-999)."""
        class CrashREPL:
            def __init__(self, branch_id, **kw):
                self.branch_id = branch_id
                self.locals = {}
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def execute_code(self, code):
                raise RuntimeError("total failure")

        mock_cls.side_effect = CrashREPL
        orch = MCTSOrchestrator(branches=2, max_depth=1)
        result = orch.run([["c1"], ["c2"]])
        # Não crasha, retorna algum resultado (ambos -999)
        assert result.total_score == -999
