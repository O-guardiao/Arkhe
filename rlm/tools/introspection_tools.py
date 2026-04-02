"""
RLM Introspection Tools — Self-awareness for the agent.

Exposes runtime identity, resource limits, SIF usage telemetry,
and prompt structure to the REPL so the LLM can reason about its
own capabilities, adapt strategies, and self-diagnose issues.
"""
from __future__ import annotations

import time
from typing import Any


def get_introspection_tools(rlm_session: Any) -> dict[str, Any]:
    """
    Build dict of introspection callables for REPL injection.

    Args:
        rlm_session: RLMSession instance (from session manager).

    Returns:
        Dict of tool name -> callable.
    """

    def rlm_introspect() -> dict[str, Any]:
        """Return a snapshot of the agent's runtime identity, resources, and tool status."""
        result: dict[str, Any] = {}

        # --- Identity & Resources ---
        rlm_core = getattr(rlm_session, "rlm_instance", None)
        rlm_obj = getattr(rlm_core, "_rlm", None) if rlm_core else None

        model = "unknown"
        backend = "unknown"
        depth = 0
        max_depth = 1
        max_iterations = 30
        context_window = 128_000
        compaction_threshold = 0

        if rlm_obj is not None:
            bk = getattr(rlm_obj, "backend_kwargs", {}) or {}
            model = bk.get("model_name", "unknown")
            backend = str(getattr(rlm_obj, "backend", "unknown"))
            depth = getattr(rlm_obj, "depth", 0)
            max_depth = getattr(rlm_obj, "max_depth", 1)
            max_iterations = getattr(rlm_obj, "max_iterations", 30)
            compactor = getattr(rlm_obj, "compactor", None)
            if compactor and hasattr(compactor, "config"):
                compaction_threshold = getattr(compactor.config, "max_history_tokens", 0)
            try:
                from rlm.utils.token_utils import get_context_limit
                context_window = get_context_limit(model)
            except Exception:
                pass
        elif rlm_core is not None:
            model = getattr(rlm_core, "model", "unknown")

        result["identity"] = {
            "model": model,
            "backend": backend,
            "depth": depth,
            "max_depth": max_depth,
            "max_iterations": max_iterations,
            "context_window_tokens": context_window,
            "compaction_threshold_tokens": compaction_threshold,
        }

        # --- Session info ---
        session_id = getattr(rlm_session, "session_id", "") or ""
        client_id = getattr(rlm_session, "client_id", "") or ""
        created_at = getattr(rlm_session, "created_at", "") or ""
        total_completions = getattr(rlm_session, "total_completions", 0)

        result["session"] = {
            "session_id": session_id[:12] + "..." if len(session_id) > 12 else session_id,
            "client_id": client_id,
            "created_at": created_at,
            "total_completions": total_completions,
        }

        # --- Memory systems status ---
        memory_status: dict[str, Any] = {}

        mem = getattr(rlm_core, "_memory", None) if rlm_core else None
        if mem is not None:
            try:
                import sqlite3
                from contextlib import closing
                with closing(sqlite3.connect(mem.db_path)) as conn:
                    total = conn.execute(
                        "SELECT COUNT(*) FROM memory_chunks WHERE is_deprecated = 0"
                    ).fetchone()[0]
                memory_status["session_memory"] = {"available": True, "active_chunks": total}
            except Exception:
                memory_status["session_memory"] = {"available": True, "stats_error": True}
        else:
            memory_status["session_memory"] = {"available": False}

        kb = getattr(rlm_core, "_kb", None) if rlm_core else None
        if kb is not None:
            try:
                memory_status["knowledge_base"] = {"available": True, "entry_count": kb.count()}
            except Exception:
                memory_status["knowledge_base"] = {"available": True}
        else:
            memory_status["knowledge_base"] = {"available": False}

        bridge = getattr(rlm_core, "_obsidian_bridge", None) if rlm_core else None
        if bridge is not None:
            vault_root = getattr(bridge, "vault_root", "")
            memory_status["vault"] = {
                "available": True,
                "vault_path": str(vault_root) if vault_root else "unknown",
            }
        else:
            memory_status["vault"] = {"available": False}

        result["memory"] = memory_status

        # --- SIF tools loaded ---
        try:
            from rlm.core.skillkit.sif import SIFFactory
            sif_names = list(SIFFactory._usage_stats.keys())
            result["sif_tools_loaded"] = sif_names
            result["sif_tool_count"] = len(sif_names)
        except Exception:
            result["sif_tools_loaded"] = []
            result["sif_tool_count"] = 0

        # --- Skill telemetry summary ---
        try:
            from rlm.core.skillkit.skill_telemetry import get_skill_telemetry
            store = get_skill_telemetry()
            summary = store.get_summary()
            result["telemetry"] = {
                "tracked_skills": summary.get("tracked_skills", 0),
                "total_calls": summary.get("call_events", 0),
                "total_routes": summary.get("route_events", 0),
                "handoffs": summary.get("handoff_events", 0),
            }
        except Exception:
            result["telemetry"] = {"available": False}

        return result

    def sif_usage() -> dict[str, Any]:
        """Return detailed SIF tool usage: call counts, success rates, latency, and recommendations."""
        result: dict[str, Any] = {"tools": {}, "recommendations": []}

        try:
            from rlm.core.skillkit.sif import SIFFactory
            from rlm.core.skillkit.skill_telemetry import get_skill_telemetry
            store = get_skill_telemetry()

            for name, stats in SIFFactory._usage_stats.items():
                call_count = stats.get("call_count", 0)
                compile_count = stats.get("compile_count", 0)
                source = stats.get("source", "unknown")
                runtime_name = stats.get("runtime_name", name)

                # Get telemetry stats
                telem = store.get_skill_stats(name)

                result["tools"][name] = {
                    "runtime_name": runtime_name,
                    "source": source,
                    "compile_count": compile_count,
                    "call_count": telem.get("call_count", call_count),
                    "success_count": telem.get("success_count", 0),
                    "failure_count": telem.get("failure_count", 0),
                    "success_rate": telem.get("success_rate", 0.5),
                    "avg_latency_ms": telem.get("avg_latency_ms", 0.0),
                    "utility_hits": telem.get("utility_hits", 0),
                }

            # Generate recommendations
            tools = result["tools"]
            never_used = [n for n, t in tools.items() if t["call_count"] == 0]
            if never_used:
                result["recommendations"].append(
                    f"Nunca usados: {', '.join(never_used)}. "
                    f"Considere se são relevantes para suas tarefas atuais."
                )

            high_fail = [
                n for n, t in tools.items()
                if t["call_count"] >= 3 and t["success_rate"] < 0.5
            ]
            if high_fail:
                result["recommendations"].append(
                    f"Alta taxa de falha (>50%): {', '.join(high_fail)}. "
                    f"Verifique parâmetros ou precondições antes de chamar."
                )

            slow = [
                n for n, t in tools.items()
                if t["avg_latency_ms"] > 5000 and t["call_count"] >= 2
            ]
            if slow:
                result["recommendations"].append(
                    f"Latência alta (>5s): {', '.join(slow)}. "
                    f"Considere caching ou chamadas assíncronas."
                )

            most_used = sorted(tools.items(), key=lambda x: -x[1]["call_count"])[:3]
            if most_used and most_used[0][1]["call_count"] > 0:
                result["top_3"] = [
                    {"name": n, "calls": t["call_count"], "success_rate": t["success_rate"]}
                    for n, t in most_used
                ]

        except Exception as e:
            result["error"] = str(e)

        return result

    def prompt_overview() -> dict[str, Any]:
        """Return a structural summary of the active system prompt and operating mode."""
        result: dict[str, Any] = {}

        rlm_core = getattr(rlm_session, "rlm_instance", None)
        rlm_obj = getattr(rlm_core, "_rlm", None) if rlm_core else None

        if rlm_obj is None:
            return {"error": "RLM core not accessible"}

        prompt = getattr(rlm_obj, "system_prompt", "") or ""
        prompt_lower = prompt.lower()

        # Detect operating mode
        if "foraging mode" in prompt_lower:
            mode = "foraging"
        elif "analyzing a software codebase" in prompt_lower:
            mode = "codebase"
        else:
            mode = "standard"
        result["mode"] = mode

        # Detect sections present
        sections = []
        section_markers = {
            "prime_directive": "prime directive",
            "core_tools": "core tools:",
            "speed_rules": "speed rules:",
            "sandbox_rules": "sandbox rules:",
            "termination": "termination discipline:",
            "server_mode": "server-mode extras:",
            "sif_tools": "sif tools",
            "vault_tools": "vault tools",
            "memory_domains": "memory domains",
            "sibling_coordination": "sibling coordination",
        }
        for key, marker in section_markers.items():
            if marker in prompt_lower:
                sections.append(key)
        result["sections"] = sections

        # Skills context
        skills_ctx = getattr(rlm_obj, "skills_context", None)
        if skills_ctx:
            skill_lines = [l.strip() for l in skills_ctx.strip().split("\n") if l.strip() and not l.startswith("#")]
            result["skills_injected"] = len(skill_lines)
            result["skills_preview"] = skill_lines[:5]
        else:
            result["skills_injected"] = 0

        # Prompt size
        result["prompt_chars"] = len(prompt)
        result["prompt_est_tokens"] = len(prompt) // 4

        # Interactive mode
        result["interaction_mode"] = getattr(rlm_obj, "interaction_mode", "repl")

        return result

    return {
        "rlm_introspect": rlm_introspect,
        "sif_usage": sif_usage,
        "prompt_overview": prompt_overview,
    }
