"""
Transformation script: rebuilds rlm.py from the worktree backup.
Run from the rlm/core/ directory.
"""
import pathlib

SOURCE = r"C:\Users\demet\Desktop\agente proativo\RLM_OpenClaw_Engine\rlm-main.worktrees\copilot-worktree-2026-03-24T15-59-50\rlm\core\rlm.py"
TARGET = r"C:\Users\demet\Desktop\agente proativo\RLM_OpenClaw_Engine\rlm-main\rlm\core\rlm.py"

with open(SOURCE, "r", encoding="utf-8") as f:
    lines = f.readlines()

print(f"Source lines: {len(lines)}")

# ── Mixin imports block (inserted after the last import, before class RLM) ──
MIXIN_IMPORTS = """\
from rlm.core.rlm_context_mixin import RLMContextMixin
from rlm.core.rlm_loop_mixin import RLMLoopMixin
from rlm.core.rlm_mcts_mixin import RLMMCTSMixin
from rlm.core.rlm_persistence_mixin import RLMPersistenceMixin

"""

# ── Slim completion() (replaces L566-L1034) ──────────────────────────────────
SLIM_COMPLETION = '''\
    def completion(
        self,
        prompt: str | dict[str, Any],
        root_prompt: str | None = None,
        mcts_branches: int = 0,
        capture_artifacts: bool = False,
    ) -> RLMChatCompletion:
        """
        Recursive Language Model completion call.

        Args:
            prompt: A single string or dictionary of messages to pass as context to the model.
            root_prompt: Optional root prompt visible to the main LM.
            mcts_branches: Evolution 6.3. If > 0, runs this many parallel branches via MCTS
                before starting the main loop. The best branch namespace is seeded into the
                main REPL. Default 0 = standard behaviour (no MCTS, no extra cost).
            capture_artifacts: Se True, extrai os locals do REPL do filho antes do cleanup
                e os armazena em ``RLMChatCompletion.artifacts``. Usado internamente por
                ``sub_rlm(..., return_artifacts=True)`` para Recursive Primitive Accumulation.
                Default False = comportamento original (sem overhead).
        Returns:
            RLMChatCompletion with the final answer.
        """
        time_start = time.perf_counter()

        if self.depth >= self.max_depth:
            return self._fallback_answer_as_completion(prompt)

        with self._spawn_completion_context(prompt) as (lm_handler, environment):
            self._clear_active_mcts_strategy(environment)
            self._record_environment_event(
                environment,
                "completion.started",
                {
                    "depth": self.depth,
                    "max_iterations": self.max_iterations,
                    "persistent": self.persistent,
                },
            )
            message_history = self._setup_prompt(prompt)
            self._inject_repl_globals(lm_handler, environment)

            if mcts_branches > 0 and self.depth == 0:
                self._run_mcts_preamble(
                    prompt, mcts_branches, lm_handler, environment, message_history,
                )

            self.hooks.trigger("completion.started", context={"prompt": str(prompt)[:100]})

            return self._run_inner_loop(
                message_history=message_history,
                lm_handler=lm_handler,
                environment=environment,
                root_prompt=root_prompt,
                turn_start=time_start,
                prompt_for_result=prompt,
                capture_artifacts=capture_artifacts,
            )

'''

# ── Build new file ────────────────────────────────────────────────────────────
# Boundaries (1-indexed → 0-indexed for list slicing):
#   L1-44    imports          → lines[0:44]
#   L45      "class RLM:\n"  → replaced with mixin inheritance
#   L46-205  __init__ body   → lines[45:205]
#   L206-565 methods (skip)  → in mixin files
#   L566-1034 completion()   → replaced with slim version
#   L1035-1190 stream+sentinel → lines[1034:1190]
#   L1191-1843 helpers (skip) → in mixin files

new_lines = []

# Imports (L1-44)
new_lines.extend(lines[0:44])

# Mixin imports
new_lines.append(MIXIN_IMPORTS)

# Class declaration with mixin inheritance (replaced L45)
new_lines.append("class RLM(RLMContextMixin, RLMLoopMixin, RLMMCTSMixin, RLMPersistenceMixin):\n")

# __init__ body (L46-205, i.e. lines[45:205])
new_lines.extend(lines[45:205])

# Slim completion() replacing L566-1034
new_lines.append(SLIM_COMPLETION)

# completion_stream() + sentinel_completion() (L1035-1190, i.e. lines[1034:1190])
new_lines.extend(lines[1034:1190])

result = "".join(new_lines)

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(result)

target_lines = result.count("\n")
print(f"Target written: {target_lines} lines")
print("Done.")
