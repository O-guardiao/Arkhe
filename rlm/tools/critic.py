"""
Evolution 6.2: Critic-Driven Fuzzing (Adversarial Arena)

Runs an Engineer vs. Adversary battle inside the REPL to pressure-test code
in unfamiliar/OOD environments. The Adversary's job is to break the Engineer's code.

Cost model: max_rounds * 1 llm_query call per round (cheap sub-model)
Default: 3 rounds maximum per critic_fuzz() call.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class FuzzRound:
    """Result of a single Engineer vs. Adversary round."""
    round_number: int
    candidate_code: str
    adversary_tests: str
    test_output: str        # stdout + stderr of running adversary tests
    winner: str             # "engineer" | "adversary"
    bugs_found: list[str] = field(default_factory=list)


@dataclass
class CriticReport:
    """Summary of a full fuzzing session."""
    rounds: list[FuzzRound]
    final_code: str
    winner: str             # "engineer" | "adversary" | "draw"
    discovered_rules: list[str] = field(default_factory=list)


# ============================================================================
# System prompts for each role
# ============================================================================

_ADVERSARY_PROMPT_TEMPLATE = textwrap.dedent("""\
You are a brutal adversarial code reviewer. Your ONLY job is to BREAK the following code.

THE CODE TO ATTACK:
```python
{code}
```

CONTEXT (what this code is supposed to do):
{context}

Write ONLY runnable Python test code (no prose, no markdown, just valid Python).
Your tests must:
1. Test at least 3 edge cases or failure modes
2. Use assert statements with clear error messages
3. Check: empty inputs, None values, boundary values, type errors, concurrent access (if relevant)
4. Each assert should FAIL if there is a bug

Start your response with: ```python
End your response with: ```

Be ruthless. Find the bugs.
""")

_ENGINEER_FIX_PROMPT_TEMPLATE = textwrap.dedent("""\
The following tests FAILED against your code:

YOUR ORIGINAL CODE:
```python
{code}
```

ADVERSARY TESTS THAT BROKE IT:
```python
{tests}
```

TEST OUTPUT (error messages):
{output}

Fix your code so ALL adversary tests pass. Output ONLY the corrected Python code.
Start your response with: ```python
End your response with: ```
""")


def run_critic_fuzzer(
    candidate_code: str,
    context: str,
    llm_query_fn: Callable[[str], str],
    execute_fn: Callable[[str], Any],
    max_rounds: int = 3,
    memory_analyze_fn: Callable[[str, str], str] | None = None,
) -> CriticReport:
    """
    Run an adversarial fuzzing session against candidate code.

    Args:
        candidate_code: The code to test (as a string).
        context: What the code is supposed to do (used in adversary prompt).
        llm_query_fn: A callable wrapping llm_query for sub-LM calls.
        execute_fn: A callable wrapping REPL execute_code, returns REPLResult.
        max_rounds: Maximum number of Engineer/Adversary rounds (default 3).
        memory_analyze_fn: Optional callable to store discovered rules.

    Returns:
        CriticReport with full round history and final code.
    """
    rounds: list[FuzzRound] = []
    discovered_rules: list[str] = []
    current_code = candidate_code

    for round_num in range(1, max_rounds + 1):
        # Step 1: Adversary generates tests
        adversary_prompt = _ADVERSARY_PROMPT_TEMPLATE.format(
            code=current_code,
            context=context,
        )
        adversary_response = llm_query_fn(adversary_prompt)

        # Extract code from the adversary's response
        adversary_tests = _extract_code(adversary_response)
        if not adversary_tests:
            adversary_tests = adversary_response  # fallback

        # Step 2: Build test harness: mount the candidate code then run adversary tests
        full_test_code = f"""
# --- Candidate Code ---
{current_code}

# --- Adversary Tests ---
try:
{_indent(adversary_tests)}
    print("ADVERSARY_TESTS_PASSED")
except (AssertionError, Exception) as _critic_err:
    print(f"ADVERSARY_TESTS_FAILED: {{type(_critic_err).__name__}}: {{_critic_err}}")
"""
        result = execute_fn(full_test_code)
        output = (result.stdout or "") + (result.stderr or "")

        engineer_won = "ADVERSARY_TESTS_PASSED" in output
        winner = "engineer" if engineer_won else "adversary"

        bugs = []
        if not engineer_won:
            # Extract bug description from output
            for line in output.splitlines():
                if "ADVERSARY_TESTS_FAILED" in line:
                    bugs.append(line.replace("ADVERSARY_TESTS_FAILED: ", "").strip())

        fuzz_round = FuzzRound(
            round_number=round_num,
            candidate_code=current_code,
            adversary_tests=adversary_tests,
            test_output=output,
            winner=winner,
            bugs_found=bugs,
        )
        rounds.append(fuzz_round)

        # If engineer lost, ask them to fix
        if not engineer_won and round_num < max_rounds:
            fix_prompt = _ENGINEER_FIX_PROMPT_TEMPLATE.format(
                code=current_code,
                tests=adversary_tests,
                output=output,
            )
            fixed_response = llm_query_fn(fix_prompt)
            fixed_code = _extract_code(fixed_response)
            if fixed_code:
                current_code = fixed_code

            # Record the bug as a discovered rule
            if bugs:
                rule = f"BUG FOUND in round {round_num}: {bugs[0]}"
                discovered_rules.append(rule)

        elif engineer_won:
            # Engineer survived — store the resilience fact
            rule = f"Code survived adversarial round {round_num} with {len(adversary_tests.splitlines())} test lines"
            discovered_rules.append(rule)
            break

    # Determine overall winner
    engineer_rounds = sum(1 for r in rounds if r.winner == "engineer")
    adversary_rounds = sum(1 for r in rounds if r.winner == "adversary")

    if engineer_rounds > adversary_rounds:
        overall_winner = "engineer"
    elif adversary_rounds > engineer_rounds:
        overall_winner = "adversary"
    else:
        overall_winner = "draw"

    # Store discovered rules to memory if function is available
    if memory_analyze_fn and discovered_rules:
        for i, rule in enumerate(discovered_rules):
            memory_analyze_fn(
                f"critic/round_{i+1}",
                f"CRITIC DISCOVERED: {rule}"
            )

    return CriticReport(
        rounds=rounds,
        final_code=current_code,
        winner=overall_winner,
        discovered_rules=discovered_rules,
    )


def _extract_code(response: str) -> str:
    """Extract Python code from a fenced code block in the response."""
    import re
    pattern = r"```(?:python)?\s*\n(.*?)\n```"
    match = re.search(pattern, response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def _indent(code: str, spaces: int = 4) -> str:
    """Indent all non-empty lines of code."""
    prefix = " " * spaces
    return "\n".join(prefix + line if line.strip() else line for line in code.splitlines())


def get_critic_tools(
    llm_query_fn: Callable[[str], str],
    execute_fn: Callable[[str], Any],
    max_rounds: int = 3,
    memory_analyze_fn: Callable[[str, str], str] | None = None,
) -> dict[str, Any]:
    """
    Return a REPL-injectable dict containing the critic fuzzer as a callable tool.

    The returned tool accepts (candidate_code, context) and returns a plain-text
    summary of the fuzzing session rather than the full CriticReport object, so
    it stays readable inside an LLM REPL namespace.

    Args:
        llm_query_fn: Callable wrapping the sub-LM for adversary/engineer turns.
        execute_fn: Callable wrapping REPL code execution; must return an object
                    with .stdout and .stderr string attributes.
        max_rounds: Maximum Engineer vs. Adversary rounds per invocation.
        memory_analyze_fn: Optional callable to persist discovered rules to memory.

    Returns:
        Dict with key ``"critic_fuzz"`` → callable.
    """

    def critic_fuzz(candidate_code: str, context: str) -> str:
        """Adversarially fuzz candidate_code.

        Runs up to ``max_rounds`` rounds of Engineer vs. Adversary.
        Returns a structured plain-text report with the final (fixed) code and
        any bugs discovered.

        Args:
            candidate_code: Python code string to test.
            context: Natural-language description of what the code should do.

        Returns:
            Plain-text report (winner, bugs found, final code).
        """
        report = run_critic_fuzzer(
            candidate_code=candidate_code,
            context=context,
            llm_query_fn=llm_query_fn,
            execute_fn=execute_fn,
            max_rounds=max_rounds,
            memory_analyze_fn=memory_analyze_fn,
        )

        lines = [
            f"=== Critic Fuzz Report ({len(report.rounds)} rounds) ===",
            f"Overall winner : {report.winner}",
            f"Rules discovered: {len(report.discovered_rules)}",
        ]
        for i, rule in enumerate(report.discovered_rules, 1):
            lines.append(f"  [{i}] {rule}")

        lines.append("\n--- Final code ---")
        lines.append(report.final_code)
        return "\n".join(lines)

    return {"critic_fuzz": critic_fuzz}

