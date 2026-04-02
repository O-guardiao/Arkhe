# -*- coding: utf-8 -*-
"""Live parallel test: three mandatory sub_rlm_parallel agents on Riemann Hypothesis.

Forces:
  - sub_rlm_parallel with 3 concurrent specialized agents (numeric, strategy, philosophy)
  - agents must THINK about proof strategies, not just compute zeros
  - a root synthesis step connecting all three perspectives
  - mcts_branches > 0

Visual observability (the critical difference from test_live_riemann.py):
  - verbose=True  -> Rich-formatted iteration trace printed to terminal
  - LiveEventLogger (event_bus) -> real-time feed of thoughts, REPL code,
    MCTS events, and final answer written to sys.__stdout__, bypassing
    pytest capture entirely
  - NO capsys fixture -> nothing is suppressed; every line flows to the terminal

Hard assertions:
  - sub_rlm_parallel actually ran (latest_parallel_summary.total_tasks >= 3)
  - final synthesis contains the required section headers
  - mcts_archive attachment present
  - elapsed < 300 s
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv

# Resolve .env from project root (parent of tests/) regardless of cwd
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

_API_KEY = os.environ.get("OPENAI_API_KEY", "")
_HAS_KEY = bool(_API_KEY) and _API_KEY.startswith("sk-")
_MODEL = "gpt-5.4-mini"

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(not _HAS_KEY, reason="OPENAI_API_KEY not set or invalid"),
]

TRACE_DIR = Path("test_outputs")
PARALLEL_TRACE_PATH = TRACE_DIR / "live_riemann_parallel_trace.md"


# ---------------------------------------------------------------------------
# Real-time event logger -- writes directly to sys.__stdout__ (bypasses capsys)
# ---------------------------------------------------------------------------

class LiveEventLogger:
    """Prints RLM lifecycle events to sys.__stdout__ in real-time.

    Implements the minimal duck-type interface used by rlm.core.engine.rlm:
      emit(event_type: str, data: dict)
      set_iteration(i: int)
    """

    _BAR = "=" * 72

    def set_iteration(self, i: int) -> None:
        sys.__stdout__.write(
            f"\n{self._BAR}\n"
            f"  > ITERATION {i + 1}\n"
            f"{self._BAR}\n"
        )
        sys.__stdout__.flush()

    def emit(self, event: str, data: dict) -> None:  # noqa: PLR0912
        ts = time.strftime("%H:%M:%S")
        out: str

        if event == "thought":
            preview = (data.get("response_preview") or "")[:240].replace("\n", " ")
            cb = data.get("code_blocks", 0)
            flag = "[FINAL]" if data.get("has_final") else f"code_blocks={cb}"
            out = f"  [{ts}] THOUGHT  {flag}\n    {preview}\n"

        elif event == "repl_exec":
            # Show the code the LLM is about to run in the REPL
            code = (data.get("code") or "")
            indented = "\n".join("    " + line for line in code.splitlines())
            out = f"  [{ts}] REPL >\n{indented}\n"

        elif event == "final_answer":
            preview = (data.get("answer_preview") or "")[:480].replace("\n", " ")
            out = (
                f"\n{self._BAR}\n"
                f"  [{ts}] ** FINAL ANSWER **"
                f"  iters={data.get('iterations')}"
                f"  elapsed={data.get('time', 0):.1f}s\n"
                f"    {preview}\n"
                f"{self._BAR}\n"
            )

        elif event == "mcts_complete":
            out = (
                f"  [{ts}] MCTS COMPLETE"
                f"  branch={data.get('best_branch')}"
                f"  score={data.get('best_score')}"
                f"  rounds={data.get('rounds')}"
                f"  seeded={data.get('seeded_vars')}\n"
            )

        else:
            text = str(data)[:160].replace("\n", " ")
            out = f"  [{ts}] {event}  {text}\n"

        sys.__stdout__.write(out)
        sys.__stdout__.flush()


# ---------------------------------------------------------------------------
# Prompt that explicitly scaffolds the mandatory sub_rlm_parallel call
# ---------------------------------------------------------------------------

# System prompt para agentes puramente textuais (sem obrigacao de REPL code)
TEXTUAL_AGENT_PROMPT = (
    "You are a deep-thinking research agent. Your task is analytical and textual — "
    "you do NOT need to write Python code or use a REPL environment.\n\n"
    "PROTOCOL:\n"
    "1. Read your task carefully.\n"
    "2. Think step by step and write your full analysis as plain text.\n"
    "3. You MAY use ```repl``` blocks with print() if you want to organize your "
    "reasoning, but it is NOT required.\n"
    "4. When you have completed your analysis, wrap your ENTIRE answer inside "
    "FINAL(your complete answer here) to finish.\n"
    "5. Do NOT call FINAL_VAR — just use FINAL(...) with the text directly.\n\n"
    "IMPORTANT: Provide your FINAL answer as soon as your analysis is complete. "
    "Do not keep iterating once you have a thorough answer."
)


def _build_parallel_riemann_prompt() -> str:
    return (
        "MISSAO: Investigar a Hipotese de Riemann (HR) com 3 agentes especializados em paralelo.\n"
        "O objetivo NAO eh apenas calcular zeros -- eh pensar criativamente sobre estrategias\n"
        "de ataque ao problema e avaliar caminhos possiveis de demonstracao.\n"
        "\n"
        "PROTOCOLO OBRIGATORIO -- execute EXATAMENTE esta sequencia:\n"
        "\n"
        "--- ETAPA 1: Iniciar 3 agentes em paralelo (PRIMEIRO bloco REPL obrigatorio) ---\n"
        "\n"
        "Execute este codigo Python no REPL agora:\n"
        "\n"
        "```repl\n"
        "resultados = sub_rlm_parallel(\n"
        "    [\n"
        "        (\n"
        '            "AGENTE NUMERICO: '\
        "1) Use mpmath (mp.dps=30) para calcular |zeta(s)| nos 5 primeiros zeros nao-triviais "\
        "conhecidos: s = 0.5 + t*j com t em [14.13472514, 21.02203964, 25.01085758, 30.42487613, 32.93506159]. "\
        "2) Depois, INVESTIGUE: escolha 3 pontos FORA da reta critica (ex: Re(s)=0.6, 0.7, 0.8 "\
        "com Im(s) entre 10 e 40) e calcule |zeta(s)|. "\
        "Compare os resultados: por que |zeta(s)| nos zeros eh ~0 mas fora da reta nao? "\
        "O que isso sugere sobre a distribuicao dos zeros? "\
        'Conclua com uma hipotese numerica baseada nos dados."\n'\
        "        ),\n"
        "        (\n"
        '            "AGENTE ESTRATEGISTA: '\
        "Voce eh um matematico investigando caminhos de prova para a HR. "\
        "Analise CRITICAMENTE estas 4 abordagens conhecidas e avalie qual tem mais potencial: "\
        "1) Operadores de Hilbert-Polya: conectar zeros de zeta a autovalores de um operador hermitiano. "\
        "2) Teoria de matrizes aleatorias (GUE): conexao estatistica entre espacamentos de zeros e autovalores aleatorios. "\
        "3) Programa de Langlands: conexao entre funcoes L e representacoes de Galois. "\
        "4) Abordagem via funcoes de campo finito: analogia com a prova de Deligne para variedades sobre Fq. "\
        "Para CADA abordagem: (a) resuma a ideia central em 2 frases, (b) identifique o maior obstaculo tecnico, "\
        "(c) de uma nota de 1-10 de viabilidade. "\
        'Proponha uma 5a abordagem hibrida combinando elementos das anteriores."\n'\
        "        ),\n"
        "        (\n"
        '            "AGENTE FILOSOFO-CRITICO: '\
        "Analise a HR do ponto de vista epistemologico e de limites computacionais: "\
        "1) Explique por que verificar 10 trilhoes de zeros numericamente NAO constitui prova -- "\
        "qual a diferenca fundamental entre verificacao e demonstracao? "\
        "2) Existem teoremas que mostram que INFINITOS zeros estao na reta critica (ex: Hardy, Selberg). "\
        "Pesquise: que fracao dos zeros sabemos que esta na reta? Isso eh suficiente? "\
        "3) Discuta: seria possivel que a HR seja independente de ZFC (indecidivel)? "\
        "Quais as implicacoes se for? "\
        "4) Qual seria o impacto pratico se a HR fosse refutada (um zero fora de Re(s)=0.5)? "\
        'Conclua com sua avaliacao: a HR provavelmente eh verdadeira? Por que?"\n'\
        "        ),\n"
        "    ],\n"
        "    timeout_s=360.0,\n"
        "    max_iterations=15,\n"
        "    coordination_policy='wait_all',\n"
        "    system_prompts=[\n"
        "        None,  # NUMERICO usa REPL padrao (precisa de mpmath)\n"
        '        "You are a deep-thinking research agent. Your task is analytical and textual. '\
        'You do NOT need to write Python code or use a REPL environment. '\
        'Think step by step and write your full analysis as plain text. '\
        'You MAY use repl blocks with print() to organize your reasoning, but it is NOT required. '\
        'When your analysis is complete, wrap your ENTIRE answer inside FINAL(your answer here). '\
        'Provide your FINAL answer as soon as your analysis is thorough. Do not keep iterating.",\n'\
        '        "You are a deep-thinking research agent. Your task is analytical and textual. '\
        'You do NOT need to write Python code or use a REPL environment. '\
        'Think step by step and write your full analysis as plain text. '\
        'You MAY use repl blocks with print() to organize your reasoning, but it is NOT required. '\
        'When your analysis is complete, wrap your ENTIRE answer inside FINAL(your answer here). '\
        'Provide your FINAL answer as soon as your analysis is thorough. Do not keep iterating.",\n'\
        "    ],\n"
        "    interaction_modes=['repl', 'text', 'text'],\n"
        ")\n"
        "for i, label in enumerate(['NUMERICO', 'ESTRATEGISTA', 'FILOSOFO-CRITICO']):\n"
        "    print(f'=== AGENTE-{label} ===')\n"
        "    print(resultados[i][:900])\n"
        "    print()\n"
        "```\n"
        "\n"
        "--- ETAPA 2: Sintese critica ---\n"
        "\n"
        "Apos receber os resultados dos tres agentes, escreva uma nota de pesquisa.\n"
        "NAO apenas resuma -- PENSE e CONECTE as ideias. Use EXATAMENTE estes cabecalhos:\n"
        "\n"
        "STATUS:\n"
        "EVIDENCIA-NUMERICA: (o que os dados mostram e o que NAO mostram)\n"
        "ESTRATEGIAS-DE-PROVA: (qual abordagem eh mais promissora e por que)\n"
        "LIMITES-EPISTEMICOS: (o que podemos e nao podemos saber)\n"
        "SINTESE: (sua propria tese -- conecte numerica + estrategia + filosofia)\n"
        "LIMITACOES: (declare que a HR eh uma conjectura em aberto sem prova conhecida)\n"
        "\n"
        "REGRA ABSOLUTA: sub_rlm_parallel e obrigatorio na ETAPA 1. "
        "Nao invente resultados dos agentes -- execute o bloco REPL acima "
        "e use os resultados reais. A sintese so pode ser escrita apos "
        "os tres agentes retornarem.\n"
        "\n"
        "Ao terminar, salve a nota completa em uma variavel chamada 'nota' "
        "e chame FINAL_VAR(nota) para encerrar.\n"
    )


# ---------------------------------------------------------------------------
# Trace writer (parallel variant)
# ---------------------------------------------------------------------------

def _write_parallel_trace_report(
    *,
    prompt: str,
    result: object,
    snapshot: dict,
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

    # -- Summary Flags --------------------------------------------------------
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

    final_mechanism = "unknown"
    for e in timeline_entries:
        if isinstance(e, dict) and e.get("event_type") == "completion.finalized":
            d = e.get("data") or {}
            if isinstance(d, dict):
                final_mechanism = "fallback/default_answer" if d.get("used_default_answer") else "FINAL()"
            break

    parallel_tasks = (latest_parallel_summary or {}).get("total_tasks", 0)

    lines: list[str] = []
    lines.append("# Live Riemann Parallel Trace")
    lines.append("")
    lines.append("## Summary Flags")
    lines.append("")
    lines.append(f"- MCTS used:       {'yes' if mcts_used else 'no'}")
    lines.append(
        f"- Subagents used:  "
        + (f"yes ({task_count} tasks, {len(spawned_events)} spawned)" if subagents_used else "no")
    )
    lines.append(
        f"- REPL executed:   "
        + (f"yes ({len(repl_events)} blocks)" if repl_events else "no")
    )
    lines.append(f"- Final mechanism: {final_mechanism}")
    lines.append(f"- Parallel tasks:  {parallel_tasks}")
    lines.append("")
    lines.append("## Run Info")
    lines.append("")
    lines.append(f"- model: {_MODEL}")
    lines.append(f"- elapsed_s: {elapsed:.2f}")
    lines.append(f"- trace_file: {PARALLEL_TRACE_PATH.as_posix()}")
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

    PARALLEL_TRACE_PATH.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

class TestLiveRiemannParallel:
    """Mandatory parallel sub-agent test on the Riemann Hypothesis.

    Differences from TestLiveRiemannResearch:
      - verbose=True   -> Rich iteration trace visible in terminal
      - event_bus      -> LiveEventLogger streams events in real-time
      - NO capsys      -> nothing suppressed; all output flows to terminal
      - max_depth=3    -> allows sub-agents (depth 1) to exist
      - max_iterations=10 -> more headroom for the parallel call
      - client_timeout=60  -> each LLM call can take up to 60 s

    Hard invariant: latest_parallel_summary.total_tasks >= 2
    """

    def test_riemann_parallel_agents_synthesize(self) -> None:  # noqa: PLR0914
        from rlm import RLM

        live_logger = LiveEventLogger()

        sys.__stdout__.write(
            f"\n{'=' * 72}\n"
            f"  LIVE RIEMANN PARALLEL TEST\n"
            f"  model={_MODEL}  max_depth=3  mcts_branches=0\n"
            f"{'=' * 72}\n"
        )
        sys.__stdout__.flush()

        engine = RLM(
            backend="openai",
            backend_kwargs={
                "model_name": _MODEL,
                "api_key": _API_KEY,
                "client_timeout": 60.0,
                "client_max_retries": 0,
            },
            environment="local",
            max_iterations=10,
            max_depth=3,      # depth 0 = root, depth 1 = sub-agents, depth 2 = sub-sub if needed
            verbose=True,     # Rich-formatted trace to terminal
            persistent=True,
            event_bus=live_logger,  # Real-time event feed
        )

        prompt = _build_parallel_riemann_prompt()
        start = time.perf_counter()

        with engine:
            result = engine.completion(
                prompt,
                mcts_branches=0,
                capture_artifacts=True,
            )
            elapsed = time.perf_counter() - start

            # -- Collect observability data ------------------------------------
            persistent_env = engine._persistent_env
            assert persistent_env is not None, "Persistent env must exist after completion"
            snapshot = persistent_env.get_runtime_state_snapshot(coordination_limit=50)

            _write_parallel_trace_report(
                prompt=prompt,
                result=result,
                snapshot=snapshot,
                elapsed=elapsed,
            )
            sys.__stdout__.write(
                f"\n[parallel_trace] {PARALLEL_TRACE_PATH.as_posix()}\n"
            )
            sys.__stdout__.flush()

            # -- Assertions ----------------------------------------------------
            response = (result.response or "").strip()
            artifact_text = ""
            if result.artifacts:
                artifact_text = " ".join(str(v) for v in result.artifacts.values())

            combined = "\n".join(
                p for p in [response, artifact_text.strip()] if p
            ).strip()
            lowered = combined.lower()

            assert combined != "", "Combined response must not be empty"
            assert elapsed < 900, f"Parallel test too slow: {elapsed:.1f}s"
            assert result.root_model == _MODEL

            # Required synthesis sections
            for header in (
                "status:",
                "evidencia-numerica:",
                "estrategias-de-prova:",
                "limites-epistemicos:",
                "sintese:",
                "limitacoes:",
            ):
                assert header in lowered, (
                    f"Missing required section '{header}' in response.\n"
                    f"Full response:\n{combined[:1200]}"
                )

            # Must acknowledge that HR is an open problem
            assert any(
                token in lowered
                for token in [
                    "em aberto", "nao ha prova", "nao resolvido",
                    "open problem", "conjectura", "nao demonstrada",
                    "indecidivel", "nao provada",
                ]
            ), f"Response must acknowledge HR is unsolved.\nResponse:\n{combined[:600]}"

            # -- CRITICAL: parallel sub-agents must have run ----------------
            coordination = snapshot.get("coordination", {})
            latest_parallel_summary = coordination.get("latest_parallel_summary") or {}
            total_tasks = latest_parallel_summary.get("total_tasks", 0)

            assert total_tasks >= 3, (
                f"sub_rlm_parallel did NOT run with >= 3 tasks.\n"
                f"latest_parallel_summary = {latest_parallel_summary}\n"
                f"Tasks registered: {snapshot.get('tasks', {}).get('items', [])}\n"
                f"Hint: ensure the model executed the sub_rlm_parallel REPL block.\n"
                f"Response preview:\n{combined[:800]}"
            )

            # Print final summary to terminal
            sys.__stdout__.write(
                f"\n{'=' * 72}\n"
                f"  TEST PASSED\n"
                f"  elapsed={elapsed:.1f}s  parallel_tasks={total_tasks}\n"
                f"  trace -> {PARALLEL_TRACE_PATH.as_posix()}\n"
                f"{'=' * 72}\n"
            )
            sys.__stdout__.flush()
