"""One-shot import rewriter for core/ restructuring.

Replaces every ``from rlm.core.<old_module>`` with the new subpackage path.
Handles both ``from rlm.core.X import Y`` and ``import rlm.core.X`` forms.

Run: python _migrate_imports.py          (dry-run, prints changes)
     python _migrate_imports.py --apply  (writes files)
"""

import os
import re
import sys

# ── mapping: old module name → new dotted path ──────────────────────
MOVES: dict[str, str] = {
    # engine/
    "rlm.core.engine.rlm":                    "rlm.core.engine.rlm",
    "rlm.core.engine.rlm_context_mixin":      "rlm.core.engine.rlm_context_mixin",
    "rlm.core.engine.rlm_loop_mixin":         "rlm.core.engine.rlm_loop_mixin",
    "rlm.core.engine.rlm_mcts_mixin":         "rlm.core.engine.rlm_mcts_mixin",
    "rlm.core.engine.rlm_persistence_mixin":  "rlm.core.engine.rlm_persistence_mixin",
    "rlm.core.engine.sub_rlm":                "rlm.core.engine.sub_rlm",
    "rlm.core.engine.lm_handler":             "rlm.core.engine.lm_handler",
    "rlm.core.engine.control_flow":           "rlm.core.engine.control_flow",
    "rlm.core.engine.compaction":             "rlm.core.engine.compaction",
    "rlm.core.engine.loop_detector":          "rlm.core.engine.loop_detector",
    "rlm.core.engine.hooks":                  "rlm.core.engine.hooks",
    "rlm.core.engine.runtime_workbench":      "rlm.core.engine.runtime_workbench",
    # orchestration/
    "rlm.core.orchestration.role_orchestrator":      "rlm.core.orchestration.role_orchestrator",
    "rlm.core.orchestration.mcts":                   "rlm.core.orchestration.mcts",
    "rlm.core.orchestration.handoff":                "rlm.core.orchestration.handoff",
    "rlm.core.orchestration.supervisor":             "rlm.core.orchestration.supervisor",
    "rlm.core.orchestration.scheduler":              "rlm.core.orchestration.scheduler",
    # memory/
    "rlm.core.memory.memory_manager":         "rlm.core.memory.memory_manager",
    "rlm.core.memory.memory_budget":          "rlm.core.memory.memory_budget",
    "rlm.core.memory.memory_hot_cache":       "rlm.core.memory.memory_hot_cache",
    "rlm.core.memory.memory_mini_agent":      "rlm.core.memory.memory_mini_agent",
    "rlm.core.memory.semantic_retrieval":     "rlm.core.memory.semantic_retrieval",
    "rlm.core.memory.knowledge_base":         "rlm.core.memory.knowledge_base",
    "rlm.core.memory.knowledge_consolidator": "rlm.core.memory.knowledge_consolidator",
    # security/  (security.py → security/_impl.py; __init__ re-exports)
    # "rlm.core.security" stays valid via __init__.py — no change needed
    "rlm.core.security.exec_approval":          "rlm.core.security.exec_approval",
    "rlm.core.security.execution_policy":       "rlm.core.security.execution_policy",
    "rlm.core.security.auth":                   "rlm.core.security.auth",
    # skillkit/
    "rlm.core.skillkit.skill_loader":           "rlm.core.skillkit.skill_loader",
    "rlm.core.skillkit.sif":                    "rlm.core.skillkit.sif",
    "rlm.core.skillkit.skill_telemetry":        "rlm.core.skillkit.skill_telemetry",
    # session/  (session.py → session/_impl.py; __init__ re-exports)
    # "rlm.core.session" stays valid via __init__.py — no change needed
    "rlm.core.session.client_registry":        "rlm.core.session.client_registry",
    # comms/
    "rlm.core.comms.sibling_bus":            "rlm.core.comms.sibling_bus",
    "rlm.core.comms.comms_utils":            "rlm.core.comms.comms_utils",
    "rlm.core.comms.mcp_client":             "rlm.core.comms.mcp_client",
    # lifecycle/
    "rlm.core.lifecycle.cancellation":           "rlm.core.lifecycle.cancellation",
    "rlm.core.lifecycle.disposable":             "rlm.core.lifecycle.disposable",
    "rlm.core.lifecycle.shutdown":               "rlm.core.lifecycle.shutdown",
    # integrations/
    "rlm.core.integrations.obsidian_bridge":        "rlm.core.integrations.obsidian_bridge",
    "rlm.core.integrations.obsidian_mirror":        "rlm.core.integrations.obsidian_mirror",
    # optimized/  (optimized.py → optimized/_impl.py; __init__ re-exports)
    # "rlm.core.optimized" stays valid via __init__.py — no change needed
    "rlm.core.optimized.benchmark":    "rlm.core.optimized.benchmark",
    "rlm.core.optimized.parsing":      "rlm.core.optimized.parsing",
    "rlm.core.optimized.opt_types":        "rlm.core.optimized.opt_types",
    "rlm.core.optimized.wire":         "rlm.core.optimized.wire",
    "rlm.core.optimized.fast":                   "rlm.core.optimized.fast",
    # observability/
    "rlm.core.observability.turn_telemetry":         "rlm.core.observability.turn_telemetry",
    "rlm.core.observability.operator_surface":       "rlm.core.observability.operator_surface",
}

# Sort by longest key first so we don't accidentally match a prefix
SORTED_MOVES = sorted(MOVES.items(), key=lambda kv: -len(kv[0]))

# Build a single regex that matches any old module path as a whole word
_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(old) for old, _ in SORTED_MOVES) + r")\b"
)


def rewrite_line(line: str) -> str:
    """Replace old import paths with new ones in a single line."""
    return _PATTERN.sub(lambda m: MOVES[m.group(0)], line)


def process_file(filepath: str, apply: bool) -> list[tuple[int, str, str]]:
    """Process one .py file. Returns list of (lineno, old, new) changes."""
    with open(filepath, encoding="utf-8") as f:
        lines = f.readlines()

    changes: list[tuple[int, str, str]] = []
    new_lines: list[str] = []
    for i, line in enumerate(lines, 1):
        new_line = rewrite_line(line)
        if new_line != line:
            changes.append((i, line.rstrip(), new_line.rstrip()))
        new_lines.append(new_line)

    if apply and changes:
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    return changes


def main() -> None:
    apply = "--apply" in sys.argv
    root = os.path.dirname(os.path.abspath(__file__))

    total_files = 0
    total_changes = 0

    for dirpath, _dirs, filenames in os.walk(root):
        # skip hidden dirs, __pycache__, etc.
        if any(part.startswith(".") or part == "__pycache__" for part in dirpath.split(os.sep)):
            continue
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            filepath = os.path.join(dirpath, fn)
            relpath = os.path.relpath(filepath, root)
            changes = process_file(filepath, apply)
            if changes:
                total_files += 1
                total_changes += len(changes)
                print(f"\n{'WRITE' if apply else 'WOULD CHANGE'}: {relpath}")
                for lineno, old, new in changes:
                    print(f"  L{lineno}: {old}")
                    print(f"     -> {new}")

    print(f"\n{'Applied' if apply else 'Would apply'}: {total_changes} changes in {total_files} files")
    if not apply and total_changes:
        print("Run with --apply to write changes.")


if __name__ == "__main__":
    main()
