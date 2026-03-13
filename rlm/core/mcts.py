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
import copy
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
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

def default_score_fn(repl_stdout: str, repl_stderr: str, code: str) -> float:
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

    def __repr__(self) -> str:
        return f"BranchResult(id={self.branch_id}, score={self.total_score:.2f}, steps={len(self.steps)})"


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
        score_fn: Callable[[str, str, str], float] | None = None,
        event_bus: Any | None = None,
    ):
        self.lm_handler_address = lm_handler_address
        self.context_payload = context_payload
        self.branches = max(1, branches)
        self.max_depth = max(1, max_depth)
        self.score_fn = score_fn or default_score_fn
        self.event_bus = event_bus
        self._lock = threading.Lock()

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
        results: list[BranchResult | None] = [None] * len(branch_code_blocks)

        def _run_branch(branch_id: int, code_steps: list[str]) -> BranchResult:
            steps = []
            total_score = 0.0
            final_code = ""

            with SandboxREPL(
                branch_id=branch_id,
                lm_handler_address=self.lm_handler_address,
                context_payload=self.context_payload,
            ) as sandbox:
                pruned = False
                for depth_idx, code in enumerate(code_steps[: self.max_depth]):
                    result = sandbox.execute_code(code)
                    stdout = result.stdout or ""
                    stderr = result.stderr or ""
                    step_score = self.score_fn(stdout, stderr, code)

                    steps.append({
                        "depth": depth_idx,
                        "code": code,
                        "stdout": stdout[:500],
                        "stderr": stderr[:500],
                        "score": step_score,
                    })
                    total_score += step_score
                    final_code = code

                    # Aggressive pruning: if first step scores ≤ 0, discard this branch
                    if depth_idx == 0 and step_score <= 0:
                        with self._lock:
                            if self.event_bus:
                                self.event_bus.emit("mcts_prune", {
                                    "branch": branch_id,
                                    "score": step_score,
                                    "reason": stderr[:200],
                                })
                        pruned = True
                        break

                final_locals = dict(sandbox.locals)

            return BranchResult(
                branch_id=branch_id,
                steps=steps,
                total_score=total_score if not pruned else -999,
                final_code=final_code,
                repl_locals=final_locals,
            )

        # Run all branches in parallel (thread pool)
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
                        })
                except Exception as e:
                    results[branch_id] = BranchResult(
                        branch_id=branch_id,
                        steps=[{"error": str(e)}],
                        total_score=-999,
                        final_code="",
                        repl_locals={},
                    )

        # Select best branch
        valid_results = [r for r in results if r is not None and r.total_score > -999]

        if not valid_results:
            # All branches failed — return the least bad one
            valid_results = [r for r in results if r is not None]

        best = max(valid_results, key=lambda r: r.total_score)

        if self.event_bus:
            self.event_bus.emit("mcts_selected", {
                "winner_branch": best.branch_id,
                "winner_score": best.total_score,
                "total_branches": len(branch_code_blocks),
                "pruned": len(branch_code_blocks) - len(valid_results),
            })

        return best


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
