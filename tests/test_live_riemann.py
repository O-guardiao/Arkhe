"""Live end-to-end test for real RLM research on the Riemann Hypothesis.

This test is intentionally framed as research assistance for a real hard problem,
not as a proof oracle. It validates that the full RLM stack can:

- call a real LLM backend,
- run with LocalREPL,
- activate MCTS pre-exploration,
- produce a structured research-oriented response,
- persist runtime evidence of MCTS artifacts.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import pytest
from dotenv import load_dotenv

load_dotenv()

_API_KEY = os.environ.get("OPENAI_API_KEY", "")
_HAS_KEY = bool(_API_KEY) and _API_KEY.startswith("sk-")
_MODEL = "gpt-5.4-mini"

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(not _HAS_KEY, reason="OPENAI_API_KEY not set or invalid"),
]

TRACE_DIR = Path("test_outputs")
TRACE_PATH = TRACE_DIR / "live_riemann_trace.md"


def _build_riemann_prompt() -> str:
    return (
        "A Hipotese de Riemann e um problema em aberto.\n"
        "Quero que voce use o RLM completo para TENTAR atacar e resolver a Hipotese de Riemann, "
        "mas sem inventar sucesso: se voce nao resolver, diga isso explicitamente.\n\n"
        "Tarefa: produza uma nota curta de pesquisa usando o runtime completo do RLM.\n"
        "Voce deve:\n"
        "0. tratar a tarefa como tentativa seria de resolucao, mas sem falsificar prova;\n"
        "1. declarar explicitamente que se trata de problema em aberto;\n"
        "2. resumir o objeto central: funcao zeta, zeros nao triviais e relacao com primos;\n"
        "3. listar exatamente 3 abordagens plausiveis de pesquisa;\n"
        "4. escolher uma abordagem para esta sessao;\n"
        "5. executar no maximo uma verificacao numerica pequena em Python quando isso ajudar;\n"
        "6. nao escreva derivacoes longas; seja conciso;\n"
        "7. terminar com proximos passos concretos e limitacoes.\n\n"
        "Restricoes de saida: no maximo 220 palavras, sem apendices, sem divagacoes.\n\n"
        "Formato obrigatorio da resposta:\n"
        "STATUS:\n"
        "CONCEITOS-CENTRAIS:\n"
        "ABORDAGENS:\n"
        "ABORDAGEM-ESCOLHIDA:\n"
        "EXPERIMENTOS:\n"
        "LIMITACOES:\n"
        "PROXIMOS-PASSOS:\n"
    )


def _write_trace_report(
    *,
    prompt: str,
    result: object,
    snapshot: dict,
    captured_stdout: str,
    elapsed: float,
) -> None:
    TRACE_DIR.mkdir(parents=True, exist_ok=True)

    response = getattr(result, "response", "") or ""
    artifacts = getattr(result, "artifacts", None)
    usage_summary = getattr(result, "usage_summary", None)
    usage_payload = usage_summary.to_dict() if usage_summary is not None else {}

    task_items = snapshot.get("tasks", {}).get("items", [])
    attachment_items = snapshot.get("attachments", {}).get("items", [])
    timeline_entries = snapshot.get("timeline", {}).get("entries", [])
    coordination = snapshot.get("coordination", {})
    coordination_events = coordination.get("events", [])
    latest_parallel_summary = coordination.get("latest_parallel_summary", {})
    active_strategy = snapshot.get("strategy", {}).get("active_recursive_strategy")

    lines: list[str] = []
    lines.append("# Live Riemann Trace")
    lines.append("")
    lines.append(f"- model: {_MODEL}")
    lines.append(f"- elapsed_s: {elapsed:.2f}")
    lines.append(f"- trace_file: {TRACE_PATH.as_posix()}")
    lines.append("")
    lines.append("## Prompt")
    lines.append("")
    lines.append("```text")
    lines.append(prompt)
    lines.append("```")
    lines.append("")
    lines.append("## Final Response")
    lines.append("")
    lines.append("```text")
    lines.append(response if response else "<empty>")
    lines.append("```")
    lines.append("")
    lines.append("## Captured Stdout")
    lines.append("")
    lines.append("```text")
    lines.append(captured_stdout.strip() if captured_stdout.strip() else "<empty>")
    lines.append("```")
    lines.append("")
    lines.append("## Usage Summary")
    lines.append("")
    lines.append("```text")
    lines.append(str(usage_payload))
    lines.append("```")
    lines.append("")
    lines.append("## Active Strategy")
    lines.append("")
    lines.append("```text")
    lines.append(str(active_strategy))
    lines.append("```")
    lines.append("")
    lines.append("## Latest Parallel Summary")
    lines.append("")
    lines.append("```text")
    lines.append(str(latest_parallel_summary))
    lines.append("```")
    lines.append("")
    lines.append("## Tasks")
    lines.append("")
    for item in task_items:
        lines.append(f"- {item}")
    if not task_items:
        lines.append("- <none>")
    lines.append("")
    lines.append("## Attachments")
    lines.append("")
    for item in attachment_items:
        lines.append(f"- {item}")
    if not attachment_items:
        lines.append("- <none>")
    lines.append("")
    lines.append("## Coordination Events")
    lines.append("")
    for event in coordination_events:
        lines.append(f"- {event}")
    if not coordination_events:
        lines.append("- <none>")
    lines.append("")
    lines.append("## Timeline Entries")
    lines.append("")
    for entry in timeline_entries:
        lines.append(f"- {entry}")
    if not timeline_entries:
        lines.append("- <none>")
    lines.append("")
    lines.append("## Artifacts")
    lines.append("")
    lines.append("```text")
    lines.append(str(artifacts) if artifacts is not None else "<none>")
    lines.append("```")
    lines.append("")

    # ── Summary Flags ────────────────────────────────────────────────────────
    mcts_used = any(
        (item.get("kind") if isinstance(item, dict) else getattr(item, "kind", None)) == "mcts_archive"
        for item in attachment_items
    )

    task_count = len(task_items)
    spawned_events = [
        e for e in timeline_entries
        if isinstance(e, dict) and e.get("event_type") == "subagent.spawned"
    ]
    subagents_used = task_count > 0 or len(spawned_events) > 0

    repl_events = [
        e for e in timeline_entries
        if isinstance(e, dict) and e.get("event_type") == "repl.executed"
    ]
    repl_used = len(repl_events) > 0

    final_mechanism = "unknown"
    for e in timeline_entries:
        if isinstance(e, dict) and e.get("event_type") == "completion.finalized":
            data = e.get("data") or {}
            if isinstance(data, dict):
                if data.get("used_default_answer"):
                    final_mechanism = "fallback/default_answer"
                else:
                    final_mechanism = "FINAL()"
            break

    parallel_tasks = (latest_parallel_summary or {}).get("total_tasks", 0)

    lines.append("## Summary Flags")
    lines.append("")
    lines.append(f"- MCTS used:       {'yes' if mcts_used else 'no'}")
    lines.append(
        f"- Subagents used:  {'yes (' + str(task_count) + ' tasks, ' + str(len(spawned_events)) + ' spawned)' if subagents_used else 'no'}"
    )
    lines.append(
        f"- REPL executed:   {'yes (' + str(len(repl_events)) + ' blocks)' if repl_used else 'no'}"
    )
    lines.append(f"- Final mechanism: {final_mechanism}")
    lines.append(f"- Parallel tasks:  {parallel_tasks}")
    lines.append("")

    TRACE_PATH.write_text("\n".join(lines), encoding="utf-8")


class TestLiveRiemannResearch:
    def test_riemann_hypothesis_research_flow_uses_full_rlm_stack(self, capsys):
        from rlm import RLM

        engine = RLM(
            backend="openai",
            backend_kwargs={
                "model_name": _MODEL,
                "api_key": _API_KEY,
                "client_timeout": 20.0,
                "client_max_retries": 0,
            },
            environment="local",
            max_iterations=6,
            max_depth=3,
            verbose=False,
            persistent=True,
        )

        with engine:
            start = time.perf_counter()
            result = engine.completion(
                _build_riemann_prompt(),
                mcts_branches=2,
                capture_artifacts=True,
            )
            elapsed = time.perf_counter() - start
            captured = capsys.readouterr()

            assert result.response is not None
            response = result.response.strip()
            artifact_text = ""
            if result.artifacts:
                artifact_text = " ".join(str(value) for value in result.artifacts.values())
            combined = "\n".join(
                part for part in [response, captured.out.strip(), artifact_text.strip()] if part
            ).strip()
            lowered = combined.lower()

            assert combined != ""
            assert elapsed < 90, f"Live Riemann test too slow: {elapsed:.1f}s"
            assert result.root_model == _MODEL
            assert "status:" in lowered
            assert "conceitos-centrais:" in lowered
            assert "abordagens:" in lowered
            assert "abordagem-escolhida:" in lowered
            assert "experimentos:" in lowered
            assert "limitacoes:" in lowered
            assert "proximos-passos:" in lowered

            # The response should treat RH as unsolved research, not as a solved task.
            assert any(
                token in lowered
                for token in [
                    "problema em aberto",
                    "em aberto",
                    "nao ha prova",
                    "sem prova",
                    "nao afirmo uma prova",
                ]
            ), combined

            # Core topic coverage.
            assert "zeta" in lowered
            assert any(token in lowered for token in ["zero", "zeros nao triviais", "linha critica"])
            assert any(token in lowered for token in ["primos", "numeros primos", "prime numbers"])

            # Runtime evidence that MCTS actually ran and attached archive information.
            persistent_env = engine._persistent_env
            assert persistent_env is not None
            snapshot = persistent_env.get_runtime_state_snapshot(coordination_limit=20)
            _write_trace_report(
                prompt=_build_riemann_prompt(),
                result=result,
                snapshot=snapshot,
                captured_stdout=captured.out,
                elapsed=elapsed,
            )
            sys.__stdout__.write(f"\n[live_riemann_trace] {TRACE_PATH.as_posix()}\n")
            sys.__stdout__.flush()
            attachments = snapshot["attachments"]["items"]
            assert any(item.get("kind") == "mcts_archive" for item in attachments), snapshot

            coordination = snapshot.get("coordination", {})
            latest_parallel_summary = coordination.get("latest_parallel_summary") or {}

            # sub_rlm_parallel is encouraged but not guaranteed; if it runs, summary must be coherent.
            if latest_parallel_summary:
                assert latest_parallel_summary.get("total_tasks", 0) >= 1

            # Artifacts are optional, but when present they should be structured.
            assert result.artifacts is None or isinstance(result.artifacts, dict)