"""
Evolution 6.3: MCTS Branching — Parallel Tree Search over the REPL

Instead of executing one greedy path (iteration 0 → 1 → 2 → ... → answer),
the MCTSOrchestrator generates N independent branches in parallel for the
first D "depth" steps. The branch with the best score (as measured by a
concrete score_fn) is expanded into the final answer.

Cost model (conservative defaults):
    branches=3, max_depth=2 → at most 3 × 2 = 6 extra exploration calls
    + 1 final answer call = 7 calls total (same as running the RLM once
    with max_iterations=7, but spending tokens smarter).

Design principles:
    • Each branch is completely isolated (SandboxREPL in its own tmpdir).
    • Branches that score 0 on the first step are pruned immediately (aggressive pruning).
    • The Orchestrator is purely additive — rlm.py's main loop is untouched
      unless `mcts_branches > 0` is passed to completion().
    • SandboxREPL is a thin wrapper that adds `branch_id` tracking and
      auto-cleanup, otherwise identical to LocalREPL.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import threading
from dataclasses import dataclass, field
from numbers import Real
from typing import Any, Callable

from rlm.environments.local_repl import LocalREPL


# ============================================================================
# SandboxREPL — isolated clone of LocalREPL for a single MCTS branch
# ============================================================================

class SandboxREPL(LocalREPL):
    """
    An isolated REPL instance that lives in its own temporary directory.
    Used by MCTSOrchestrator to run independent branches without
    polluting the main REPL's namespace or file system.

    Identical to LocalREPL, but:
    - Tagged with a `branch_id` for debugging/logging.
    - Cleaned up immediately when the branch is discarded (cleanup() on __exit__).
    """

    def __init__(self, branch_id: int, **kwargs):
        self.branch_id = branch_id
        # Force a separate temp dir — LocalREPL already creates one, this
        # is just for the label.
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"SandboxREPL(branch={self.branch_id}, tmpdir={self.temp_dir!r})"


# ============================================================================
# Score function helpers
# ============================================================================

def default_score_fn(repl_stdout: str, repl_stderr: str | None, code: str) -> float:
    """
    Default branch scoring function.  Higher = better branch.

    Rules (additive):
        +2.0  — code compiled and ran with no error in stderr
        +1.0  — stdout is non-empty (something was actually computed)
        +1.0  — stdout contains at least one digit (numeric output)
        -2.0  — stderr contains 'Error' or 'Traceback'
        -1.0  — stdout is suspiciously short (< 5 chars, likely a stub)
        -0.5  — code is fewer than 30 chars (trivially short exploration)

    Returns a float in roughly [-3, 4].
    """
    score = 0.0
    has_error = bool(repl_stderr and ("Error" in repl_stderr or "Traceback" in repl_stderr))

    if not has_error:
        score += 2.0
    else:
        score -= 2.0

    if repl_stdout and repl_stdout.strip():
        score += 1.0
        if any(c.isdigit() for c in repl_stdout):
            score += 1.0
    else:
        score -= 1.0

    if len(code.strip()) < 30:
        score -= 0.5

    return score


@dataclass
class EvaluationStage:
    """
    Extra evaluation stage inspired by AlphaEvolve-style cascades.

    The evaluator receives a snapshot with branch metadata, execution outputs,
    locals, and accumulated metrics. It must return a numeric score.
    Stages can both contribute to the total score and prune branches early.
    """

    name: str
    evaluator: Callable[[dict[str, Any]], float]
    min_score: float | None = None
    weight: float = 1.0

    def evaluate(self, snapshot: dict[str, Any]) -> float:
        value = self.evaluator(snapshot)
        if not isinstance(value, Real):
            raise TypeError(
                f"Evaluation stage '{self.name}' must return a numeric score, got {type(value).__name__}."
            )
        return float(value)


# ============================================================================
# BranchResult — result of one full branch expansion
# ============================================================================

@dataclass
class BranchResult:
    branch_id: int
    steps: list[dict]   # Each step: {"code": ..., "stdout": ..., "stderr": ..., "score": ...}
    total_score: float
    final_code: str     # Last code block run in this branch
    repl_locals: dict   # Final namespace after branch completes
    aggregated_metrics: dict[str, float] = field(default_factory=dict)
    pruned_reason: str | None = None
    strategy_name: str | None = None
    strategy: dict[str, Any] | None = None

    def __repr__(self) -> str:
        return f"BranchResult(id={self.branch_id}, score={self.total_score:.2f}, steps={len(self.steps)})"


class ProgramArchive:
    """A tiny MAP-Elites-like archive for resurfacing useful prior branches."""

    def __init__(
        self,
        *,
        max_size: int = 24,
        niche_fn: Callable[[BranchResult], str] | None = None,
    ):
        self.max_size = max(1, max_size)
        self.niche_fn = niche_fn or self._default_niche
        self._entries: dict[str, BranchResult] = {}

    @staticmethod
    def _default_niche(branch: BranchResult) -> str:
        metric_names = ",".join(sorted(branch.aggregated_metrics.keys())) or "heuristic-only"
        dominant_metric = "heuristic"
        if branch.aggregated_metrics:
            dominant_metric = max(
                branch.aggregated_metrics.items(),
                key=lambda item: abs(item[1]),
            )[0]
        code_len = len(branch.final_code.strip())
        if code_len < 80:
            code_bucket = "short"
        elif code_len < 240:
            code_bucket = "medium"
        else:
            code_bucket = "long"
        symbol_bucket = "assign"
        stripped = branch.final_code.strip()
        if "def " in stripped:
            symbol_bucket = "function"
        elif "class " in stripped:
            symbol_bucket = "class"
        elif "for " in stripped or "while " in stripped:
            symbol_bucket = "loop"

        output_bucket = "silent"
        if branch.steps:
            stdout = str(branch.steps[-1].get("stdout", "")).strip()
            if any(ch.isdigit() for ch in stdout):
                output_bucket = "numeric"
            elif stdout:
                output_bucket = "textual"

        strategy_bucket = branch.strategy_name or "no-strategy"

        return (
            f"{metric_names}|dominant={dominant_metric}|{code_bucket}|{symbol_bucket}|"
            f"{output_bucket}|strategy={strategy_bucket}|{'pruned' if branch.pruned_reason else 'ok'}"
        )

    def update(self, branches: list[BranchResult]) -> None:
        for branch in branches:
            if branch.total_score <= -999:
                continue
            niche = self.niche_fn(branch)
            current = self._entries.get(niche)
            if current is None or branch.total_score > current.total_score:
                self._entries[niche] = branch

        if len(self._entries) > self.max_size:
            ranked = sorted(self._entries.items(), key=lambda item: item[1].total_score, reverse=True)
            self._entries = dict(ranked[: self.max_size])

    def sample(self, limit: int | None = None) -> list[BranchResult]:
        ranked = sorted(self._entries.values(), key=lambda branch: branch.total_score, reverse=True)
        if limit is None:
            return ranked
        return ranked[: max(0, limit)]

    def size(self) -> int:
        return len(self._entries)


@dataclass
class RecursiveStrategy:
    name: str
    recursion_prompt: str
    decomposition_plan: list[str]
    coordination_policy: str
    stop_condition: str
    repl_search_mode: str
    meta_prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "recursion_prompt": self.recursion_prompt,
            "decomposition_plan": list(self.decomposition_plan),
            "coordination_policy": self.coordination_policy,
            "stop_condition": self.stop_condition,
            "repl_search_mode": self.repl_search_mode,
            "meta_prompt": self.meta_prompt,
        }


def _strategy_from_payload(payload: dict[str, Any]) -> RecursiveStrategy:
    return RecursiveStrategy(
        name=str(payload.get("name", "strategy")).strip() or "strategy",
        recursion_prompt=str(payload.get("recursion_prompt", "")).strip(),
        decomposition_plan=[str(item).strip() for item in payload.get("decomposition_plan", []) if str(item).strip()],
        coordination_policy=str(payload.get("coordination_policy", "stop_on_solution")).strip() or "stop_on_solution",
        stop_condition=str(payload.get("stop_condition", "stop when one branch proves a viable path")).strip(),
        repl_search_mode=str(payload.get("repl_search_mode", "iterative_repl_search")).strip() or "iterative_repl_search",
        meta_prompt=str(payload.get("meta_prompt", "")).strip(),
    )


def default_recursive_strategies(prompt: str, n_variants: int) -> list[RecursiveStrategy]:
    base = [
        RecursiveStrategy(
            name="parallel_decompose",
            recursion_prompt="Break the task into independent branches and compare outputs.",
            decomposition_plan=[
                "Identify 2-3 independent subproblems.",
                "Use sub_rlm_parallel for the expensive branches.",
                "Aggregate the best result in the parent REPL.",
            ],
            coordination_policy="stop_on_solution",
            stop_condition="Stop redundant siblings when one branch demonstrates a viable solution.",
            repl_search_mode="parallel_branch_search",
            meta_prompt=f"Favor decomposition when the task appears novel: {prompt[:120]}",
        ),
        RecursiveStrategy(
            name="serial_refine",
            recursion_prompt="Use a narrow serial chain of subagents that progressively refine hypotheses.",
            decomposition_plan=[
                "Produce a quick hypothesis in the parent REPL.",
                "Delegate one focused refinement subtask at a time.",
                "Feed each result back into the next refinement step.",
            ],
            coordination_policy="switch_strategy",
            stop_condition="Switch when refinement stops improving evidence.",
            repl_search_mode="serial_refinement_search",
            meta_prompt="Prefer lower coordination overhead when subproblems depend on each other.",
        ),
        RecursiveStrategy(
            name="repl_probe_then_delegate",
            recursion_prompt="Probe the problem in the REPL first, then delegate only validated subproblems.",
            decomposition_plan=[
                "Use the REPL to inspect or simulate the problem quickly.",
                "Extract concrete subquestions from observed evidence.",
                "Delegate only the subquestions that need recursive search.",
            ],
            coordination_policy="consensus_reached",
            stop_condition="Stop when the REPL evidence and recursive branches converge.",
            repl_search_mode="probe_and_delegate",
            meta_prompt="Prefer evidence-first exploration for unfamiliar tasks.",
        ),
    ]
    if n_variants <= len(base):
        return base[:n_variants]
    expanded = list(base)
    while len(expanded) < n_variants:
        idx = len(expanded) + 1
        template = base[(idx - 1) % len(base)]
        expanded.append(
            RecursiveStrategy(
                name=f"{template.name}_{idx}",
                recursion_prompt=template.recursion_prompt,
                decomposition_plan=list(template.decomposition_plan),
                coordination_policy=template.coordination_policy,
                stop_condition=template.stop_condition,
                repl_search_mode=template.repl_search_mode,
                meta_prompt=template.meta_prompt,
            )
        )
    return expanded


def _parse_json_payload(text: str) -> Any | None:
    candidates = []
    stripped = (text or "").strip()
    if stripped:
        candidates.append(stripped)
    fenced = re.findall(r"```(?:json)?\s*\n(.*?)\n```", text or "", re.DOTALL)
    candidates.extend(fenced)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def generate_recursive_strategies(
    prompt: str,
    n_variants: int,
    llm_query_fn: Callable[[str], str],
) -> list[RecursiveStrategy]:
    strategy_prompt = f"""Design {n_variants} diverse recursive strategies for solving the following task in an RLM-style system.

Task:
{prompt}

Each strategy must be a JSON object with fields:
- name
- recursion_prompt
- decomposition_plan (array of short steps)
- coordination_policy
- stop_condition
- repl_search_mode
- meta_prompt

Return a JSON array only.
"""
    response = llm_query_fn(strategy_prompt)
    payload = _parse_json_payload(response)
    strategies: list[RecursiveStrategy] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                strategies.append(_strategy_from_payload(item))
    elif isinstance(payload, dict):
        strategies.append(_strategy_from_payload(payload))

    if len(strategies) < n_variants:
        defaults = default_recursive_strategies(prompt, n_variants)
        existing = {strategy.name for strategy in strategies}
        for fallback in defaults:
            if fallback.name not in existing:
                strategies.append(fallback)
            if len(strategies) == n_variants:
                break
    return strategies[:n_variants]


def generate_refined_recursive_strategies(
    prompt: str,
    archived_branches: list[BranchResult],
    n_variants: int,
    llm_query_fn: Callable[[str], str],
) -> list[RecursiveStrategy]:
    archived_strategies = [branch.strategy for branch in archived_branches if branch.strategy]
    refine_prompt = f"""Refine recursive strategies for an RLM-style system solving this task:

Task:
{prompt}

Observed strong branches:
{summarize_branch_feedback(archived_branches)}

Observed strategies:
{json.dumps(archived_strategies[:4], ensure_ascii=False, indent=2) if archived_strategies else '[]'}

Return {n_variants} strategy JSON objects in a JSON array with the same schema as before.
Prefer changing decomposition, coordination, stopping rules, and REPL search mode.
"""
    response = llm_query_fn(refine_prompt)
    payload = _parse_json_payload(response)
    strategies: list[RecursiveStrategy] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                strategies.append(_strategy_from_payload(item))
    elif isinstance(payload, dict):
        strategies.append(_strategy_from_payload(payload))

    if len(strategies) < n_variants:
        defaults = default_recursive_strategies(prompt, n_variants)
        strategies.extend(defaults[len(strategies):n_variants])
    return strategies[:n_variants]


def build_strategy_prompt(prompt: str, strategy: RecursiveStrategy) -> str:
    decomposition = "\n".join(f"- {step}" for step in strategy.decomposition_plan)
    return f"""Task:
{prompt}

Recursive strategy name: {strategy.name}
Recursion prompt: {strategy.recursion_prompt}
Decomposition plan:
{decomposition or '- no decomposition specified'}
Coordination policy: {strategy.coordination_policy}
Stop condition: {strategy.stop_condition}
REPL search mode: {strategy.repl_search_mode}
Meta prompt: {strategy.meta_prompt}

Generate code or REPL logic that follows this recursive strategy. Favor explicit use of sub_rlm, sub_rlm_parallel, sibling coordination, and REPL search when useful.
"""


# ============================================================================
# MCTSOrchestrator
# ============================================================================

class MCTSOrchestrator:
    """
    Runs N parallel branches of the RLM for the first `max_depth` iterations.
    Selects the branch with the highest cumulative score for expansion.

    Args:
        lm_handler_address: The (host, port) of the LMHandler socket.
        context_payload: Passed directly to each SandboxREPL (e.g. codebase path).
        branches: Number of parallel branches (default 3).
        max_depth: How many REPL steps per branch before scoring (default 2).
        score_fn: Function(stdout, stderr, code) -> float. Default: default_score_fn.
        event_bus: Optional event bus to emit MCTS events (for observability).
    """

    def __init__(
        self,
        lm_handler_address: tuple[str, int] | None = None,
        context_payload: Any = None,
        branches: int = 3,
        max_depth: int = 2,
        score_fn: Callable[[str, str | None, str], float] | None = None,
        evaluation_stages: list[EvaluationStage] | None = None,
        event_bus: Any | None = None,
        extra_globals: dict[str, Any] | None = None,
    ):
        self.lm_handler_address = lm_handler_address
        self.context_payload = context_payload
        self.branches = max(1, branches)
        self.max_depth = max(1, max_depth)
        self.score_fn = score_fn or default_score_fn
        self.evaluation_stages = list(evaluation_stages or [])
        self.event_bus = event_bus
        self.extra_globals = extra_globals or {}
        self._lock = threading.Lock()
        self.last_results: list[BranchResult] = []

    def run(
        self,
        branch_code_blocks: list[list[str]],
    ) -> BranchResult:
        """
        Execute N branches in parallel, each running a sequence of code blocks.

        Args:
            branch_code_blocks: A list of N lists, where each inner list is
                a sequence of code strings for that branch (one per depth step).
                Example:
                    [
                        ["x = approach_a()\nprint(x)"],    # branch 0
                        ["x = approach_b()\nprint(x)"],    # branch 1
                        ["x = approach_c()\nprint(x)"],    # branch 2
                    ]

        Returns:
            The BranchResult with the highest total_score.
        """
        if not branch_code_blocks:
            raise ValueError("branch_code_blocks must contain at least one branch")

        results: list[BranchResult | None] = [None] * len(branch_code_blocks)

        def _run_branch(branch_id: int, code_steps: list[str]) -> BranchResult:
            steps = []
            total_score = 0.0
            final_code = ""
            aggregated_metrics: dict[str, float] = {"heuristic": 0.0}
            pruned_reason: str | None = None

            with SandboxREPL(
                branch_id=branch_id,
                lm_handler_address=self.lm_handler_address,
                context_payload=self.context_payload,
            ) as sandbox:
                # Inject runtime tools (sub_rlm_parallel, etc.) into branch sandbox
                if self.extra_globals:
                    sandbox.globals.update(self.extra_globals)
                pruned = False
                for depth_idx, code in enumerate(code_steps[: self.max_depth]):
                    result = sandbox.execute_code(code)
                    stdout = result.stdout or ""
                    stderr = result.stderr or ""
                    heuristic_score = self.score_fn(stdout, stderr, code)
                    step_score = heuristic_score
                    step_metrics = {"heuristic": heuristic_score}

                    aggregated_metrics["heuristic"] += heuristic_score

                    snapshot = {
                        "branch_id": branch_id,
                        "depth": depth_idx,
                        "code": code,
                        "stdout": stdout,
                        "stderr": stderr,
                        "locals": dict(sandbox.locals),
                        "step_metrics": dict(step_metrics),
                        "total_score": total_score,
                    }

                    steps.append({
                        "depth": depth_idx,
                        "code": code,
                        "stdout": stdout[:500],
                        "stderr": stderr[:500],
                        "score": step_score,
                        "metrics": dict(step_metrics),
                    })
                    final_code = code

                    # Aggressive pruning: if first step scores ≤ 0, discard this branch
                    if depth_idx == 0 and heuristic_score <= 0:
                        with self._lock:
                            if self.event_bus:
                                self.event_bus.emit("mcts_prune", {
                                    "branch": branch_id,
                                    "score": heuristic_score,
                                    "reason": stderr[:200],
                                })
                        pruned_reason = "heuristic-first-step"
                        pruned = True
                        steps[-1]["pruned_reason"] = pruned_reason
                        break

                    stage_failed = False
                    for stage in self.evaluation_stages:
                        stage_score = stage.evaluate(snapshot)
                        step_metrics[stage.name] = stage_score
                        aggregated_metrics[stage.name] = aggregated_metrics.get(stage.name, 0.0) + stage_score
                        step_score += stage.weight * stage_score
                        snapshot["step_metrics"] = dict(step_metrics)

                        if self.event_bus:
                            with self._lock:
                                self.event_bus.emit("mcts_stage_scored", {
                                    "branch": branch_id,
                                    "depth": depth_idx,
                                    "stage": stage.name,
                                    "score": stage_score,
                                    "weighted_score": stage.weight * stage_score,
                                })

                        if stage.min_score is not None and stage_score < stage.min_score:
                            pruned_reason = f"stage:{stage.name}"
                            steps[-1]["pruned_reason"] = pruned_reason
                            if self.event_bus:
                                with self._lock:
                                    self.event_bus.emit("mcts_stage_prune", {
                                        "branch": branch_id,
                                        "depth": depth_idx,
                                        "stage": stage.name,
                                        "score": stage_score,
                                        "threshold": stage.min_score,
                                    })
                            pruned = True
                            stage_failed = True
                            break

                    steps[-1]["score"] = step_score
                    steps[-1]["metrics"] = dict(step_metrics)
                    total_score += step_score

                    if stage_failed:
                        break

                final_locals = dict(sandbox.locals)

            return BranchResult(
                branch_id=branch_id,
                steps=steps,
                total_score=total_score if not pruned else -999,
                final_code=final_code,
                repl_locals=final_locals,
                aggregated_metrics=aggregated_metrics,
                pruned_reason=pruned_reason,
            )

        # Run all branches in parallel (thread pool)
        # Early termination: if a branch scores >= 1.0, cancel remaining.
        _early_winner: BranchResult | None = None
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.branches) as executor:
            futures = {
                executor.submit(_run_branch, i, codes): i
                for i, codes in enumerate(branch_code_blocks)
            }
            for future in concurrent.futures.as_completed(futures):
                branch_id = futures[future]
                try:
                    result = future.result()
                    results[branch_id] = result
                    if self.event_bus:
                        self.event_bus.emit("mcts_branch_done", {
                            "branch": branch_id,
                            "score": result.total_score,
                            "steps": len(result.steps),
                            "pruned_reason": result.pruned_reason,
                            "metrics": result.aggregated_metrics,
                        })
                    # Early termination: perfect score, cancel remaining
                    if result.total_score >= self.max_depth * 4.0 and _early_winner is None:
                        _early_winner = result
                        for f, bid in futures.items():
                            if not f.done():
                                f.cancel()
                        if self.event_bus:
                            self.event_bus.emit("mcts_early_terminate", {
                                "winner_branch": branch_id,
                                "score": result.total_score,
                                "cancelled_branches": [
                                    bid for f, bid in futures.items()
                                    if not f.done() and bid != branch_id
                                ],
                            })
                except Exception as e:
                    results[branch_id] = BranchResult(
                        branch_id=branch_id,
                        steps=[{"error": str(e)}],
                        total_score=-999,
                        final_code="",
                        repl_locals={},
                        pruned_reason="exception",
                    )

        # Select best branch
        valid_results = [r for r in results if r is not None and r.total_score > -999]

        if not valid_results:
            # All branches failed — return the least bad one
            valid_results = [r for r in results if r is not None]

        self.last_results = sorted(
            [r for r in results if r is not None],
            key=lambda branch: branch.total_score,
            reverse=True,
        )

        best = max(valid_results, key=lambda r: r.total_score)

        if self.event_bus:
            self.event_bus.emit("mcts_selected", {
                "winner_branch": best.branch_id,
                "winner_score": best.total_score,
                "total_branches": len(branch_code_blocks),
                "pruned": len(branch_code_blocks) - len(valid_results),
                "winner_metrics": best.aggregated_metrics,
            })

        return best

    def top_results(self, limit: int | None = None, *, include_pruned: bool = False) -> list[BranchResult]:
        ranked = self.last_results if include_pruned else [r for r in self.last_results if r.total_score > -999]
        if limit is None:
            return list(ranked)
        return list(ranked[: max(0, limit)])


# ============================================================================
# Helper: build branch variants from a base prompt using an LLM
# ============================================================================

def generate_branch_variants(
    prompt: str,
    n_variants: int,
    llm_query_fn: Callable[[str], str],
) -> list[str]:
    """
    Ask the LLM to generate N alternative approaches to the same prompt.
    Each variant is a different code strategy.

    Returns a list of code strings (one per branch).
    """
    branch_prompt = f"""You must generate {n_variants} completely DIFFERENT Python code approaches to solve the following task.
Each approach should be a valid code strategy that could work.
DO NOT repeat the same approach. Make them structurally different.

Task: {prompt}

Format: output exactly {n_variants} code blocks, separated by the marker ---BRANCH---.
Each block must be valid, runnable Python.

Example format:
```python
# Approach 1
x = method_a()
print(x)
```
---BRANCH---
```python
# Approach 2
x = method_b()
print(x)
```
"""
    response = llm_query_fn(branch_prompt)

    # Parse out branches
    import re
    blocks = response.split("---BRANCH---")
    code_blocks = []
    code_pattern = re.compile(r"```(?:python)?\s*\n(.*?)\n```", re.DOTALL)

    for block in blocks:
        match = code_pattern.search(block)
        if match:
            code_blocks.append(match.group(1).strip())
        elif block.strip():
            # Try to use raw content if no fenced block
            code_blocks.append(block.strip())

    # Pad or trim to exactly n_variants
    while len(code_blocks) < n_variants:
        code_blocks.append(f"print('Branch {len(code_blocks)+1}: no approach generated')")

    return code_blocks[:n_variants]


SEARCH_REPLACE_BLOCK_RE = re.compile(
    r"<<<<<<< SEARCH\s*\n(.*?)\n=======\s*\n(.*?)\n>>>>>>> REPLACE",
    re.DOTALL,
)


def parse_search_replace_blocks(text: str) -> list[tuple[str, str]]:
    return [
        (search.strip("\n"), replace.strip("\n"))
        for search, replace in SEARCH_REPLACE_BLOCK_RE.findall(text or "")
    ]


def apply_search_replace_blocks(base_code: str, blocks: list[tuple[str, str]]) -> str:
    updated = base_code
    for search, replace in blocks:
        if not search:
            continue
        if search not in updated:
            raise ValueError("search block not found in elite code")
        updated = updated.replace(search, replace, 1)
    return updated


def generate_diff_mutation_variants(
    prompt: str,
    elite_branches: list[BranchResult],
    n_variants: int,
    llm_query_fn: Callable[[str], str],
) -> list[str]:
    """Mutate elite code via SEARCH/REPLACE diffs before falling back to full rewrites."""
    if not elite_branches:
        return []

    variants: list[str] = []
    for index in range(n_variants):
        elite = elite_branches[index % len(elite_branches)]
        mutation_prompt = f"""You are mutating an elite Python candidate for a hard, unfamiliar task.
Improve the code by editing the elite program directly.

Task:
{prompt}

Elite branch summary:
{summarize_branch_feedback([elite], max_branches=1)}

Current elite code:
```python
{elite.final_code}
```

Return either:
1. One or more SEARCH/REPLACE diff blocks against the current elite code, or
2. A full replacement Python code block if a rewrite is clearly better.

Prefer small, meaningful mutations over restarts.
"""
        response = llm_query_fn(mutation_prompt)
        blocks = parse_search_replace_blocks(response)
        if blocks:
            try:
                variants.append(apply_search_replace_blocks(elite.final_code, blocks))
                continue
            except ValueError:
                pass

        parsed = generate_branch_variants(mutation_prompt, 1, lambda _: response)
        if parsed:
            variants.append(parsed[0])

    return variants[:n_variants]


def summarize_branch_feedback(
    branches: list[BranchResult],
    *,
    max_branches: int = 3,
    code_preview_chars: int = 220,
) -> str:
    """Render branch outcomes into a compact text summary for prompt reuse."""
    if not branches:
        return "No successful branches were available for feedback reuse."

    lines: list[str] = []
    for branch in branches[:max_branches]:
        metrics = ", ".join(
            f"{name}={value:.2f}" for name, value in sorted(branch.aggregated_metrics.items())
        ) or "no-metrics"
        preview = branch.final_code.strip().replace("\r", " ").replace("\n", " ")[:code_preview_chars]
        lines.append(
            f"- Branch {branch.branch_id}: total_score={branch.total_score:.2f}; metrics=[{metrics}]; "
            f"pruned_reason={branch.pruned_reason or 'none'}; final_code={preview}"
        )
    return "\n".join(lines)


def generate_refined_branch_variants(
    prompt: str,
    elite_branches: list[BranchResult],
    n_variants: int,
    llm_query_fn: Callable[[str], str],
) -> list[str]:
    """
    Generate improved variants using elite branches and their feedback.

    This is the missing link between one-shot branching and short-horizon
    evolutionary search: the next round sees what worked and what failed.
    """
    elite_summary = summarize_branch_feedback(elite_branches, max_branches=max(1, min(4, len(elite_branches))))
    refine_prompt = f"""You are improving candidate Python programs for a hard problem where novelty matters.
Use the elite branch feedback below to generate {n_variants} NEW candidate code approaches.

Task:
{prompt}

Prior high-scoring branches:
{elite_summary}

Instructions:
- Do not merely restate the previous code.
- Preserve ideas that scored well, but change structure or search strategy.
- Prefer approaches that would generalize to unfamiliar problems, not prompt memorization.
- If previous branches were pruned, avoid repeating the failure pattern.
- Return diverse candidates, not minor renames.

Format: output exactly {n_variants} code blocks, separated by ---BRANCH---.
Each block must be valid runnable Python.
"""
    diff_mutations = generate_diff_mutation_variants(prompt, elite_branches, n_variants, llm_query_fn)
    if len(diff_mutations) >= n_variants:
        return diff_mutations[:n_variants]

    novel_variants = generate_branch_variants(refine_prompt, n_variants, llm_query_fn)
    merged: list[str] = []
    for candidate in diff_mutations + novel_variants:
        normalized = candidate.strip()
        if not normalized:
            continue
        if normalized in merged:
            continue
        merged.append(normalized)
        if len(merged) == n_variants:
            break
    return merged


def evolutionary_branch_search(
    prompt: str,
    n_variants: int,
    llm_query_fn: Callable[[str], str],
    orchestrator: MCTSOrchestrator,
    *,
    rounds: int = 2,
    elite_count: int = 2,
    archive: ProgramArchive | None = None,
) -> dict[str, Any]:
    """
    Run a short AlphaEvolve-style loop: sample, evaluate, keep elites, refine.

    Returns a dict with the best branch and round-by-round metadata.
    """
    total_rounds = max(1, rounds)
    elite_limit = max(1, elite_count)
    round_history: list[dict[str, Any]] = []
    best_overall: BranchResult | None = None
    archive = archive or ProgramArchive(max_size=max(8, elite_limit * 4))
    current_strategies = generate_recursive_strategies(prompt, n_variants, llm_query_fn)
    current_variants = [
        generate_branch_variants(build_strategy_prompt(prompt, strategy), 1, llm_query_fn)[0]
        for strategy in current_strategies
    ]

    for round_index in range(total_rounds):
        branch_code_blocks = [[variant] for variant in current_variants]
        best_this_round = orchestrator.run(branch_code_blocks)
        for branch in orchestrator.last_results:
            if 0 <= branch.branch_id < len(current_strategies):
                strategy = current_strategies[branch.branch_id]
                branch.strategy_name = strategy.name
                branch.strategy = strategy.to_dict()
        elites = orchestrator.top_results(elite_limit)
        archive.update(elites)
        archived = archive.sample(max(elite_limit, 3))
        best_strategy = None
        if 0 <= best_this_round.branch_id < len(current_strategies):
            best_strategy = current_strategies[best_this_round.branch_id]
            best_this_round.strategy_name = best_strategy.name
            best_this_round.strategy = best_strategy.to_dict()
        round_summary = {
            "round": round_index + 1,
            "best_branch": best_this_round.branch_id,
            "best_score": best_this_round.total_score,
            "best_strategy": best_strategy.to_dict() if best_strategy is not None else None,
            "elite_count": len(elites),
            "archive_size": archive.size(),
            "elite_feedback": summarize_branch_feedback(elites),
            "archive_feedback": summarize_branch_feedback(archived),
        }
        round_history.append(round_summary)

        if best_overall is None or best_this_round.total_score > best_overall.total_score:
            best_overall = best_this_round

        if orchestrator.event_bus:
            orchestrator.event_bus.emit("mcts_round_complete", round_summary)

        if round_index == total_rounds - 1:
            break

        if not archived:
            current_strategies = generate_recursive_strategies(prompt, n_variants, llm_query_fn)
            current_variants = [
                generate_branch_variants(build_strategy_prompt(prompt, strategy), 1, llm_query_fn)[0]
                for strategy in current_strategies
            ]
            continue

        current_strategies = generate_refined_recursive_strategies(
            prompt,
            archived,
            n_variants,
            llm_query_fn,
        )
        current_variants = [
            generate_refined_branch_variants(
                build_strategy_prompt(prompt, strategy),
                archived,
                1,
                llm_query_fn,
            )[0]
            for strategy in current_strategies
        ]

    if best_overall is None:
        raise ValueError("evolutionary search produced no branch results")

    if orchestrator.event_bus:
        orchestrator.event_bus.emit(
            "mcts_evolution_complete",
            {
                "rounds": total_rounds,
                "best_branch": best_overall.branch_id,
                "best_score": best_overall.total_score,
            },
        )

    return {
        "best_branch": best_overall,
        "history": round_history,
        "final_variants": current_variants,
        "final_strategies": [strategy.to_dict() for strategy in current_strategies],
        "archive": archive,
    }
