"""Run a real RLM session for mathematical research assistance.

This example is intentionally framed as research support, not as a claim of
solving an open problem. It is suitable for real LLM-backed exploration using
the local RLM runtime plus a remote model backend.
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from rlm import RLM

load_dotenv()

DEFAULT_PROBLEM = (
    "A Hipotese de Riemann. Nao reivindique uma prova se voce nao tiver uma. "
    "Use o runtime para pesquisa matematica seria: mapear formulacoes equivalentes, "
    "identificar obstaculos tecnicos, propor lemas intermediarios verificaveis e "
    "executar checagens numericas pequenas quando fizer sentido."
)


def build_prompt(problem: str) -> str:
    return (
        "Voce esta operando em um RLM com LocalREPL e IA real. \n"
        "Objetivo: trabalhar como assistente de pesquisa rigoroso, nao como gerador de hype. \n\n"
        "Regras:\n"
        "1. Nao afirme que resolveu um problema em aberto sem uma demonstracao completa e verificavel.\n"
        "2. Se a tarefa for inatingivel em uma sessao, reduza para subproblemas validos.\n"
        "3. Use sub_rlm e sub_rlm_parallel apenas quando a decomposicao realmente ajudar.\n"
        "4. Use Python no REPL para verificacoes numericas, series truncadas e exploracao simbolica leve.\n"
        "5. Ao final, entregue incertezas e pontos de falha.\n\n"
        "Tarefa:\n"
        f"{problem}\n\n"
        "Formato de saida exigido:\n"
        "- Estado atual do conhecimento relevante\n"
        "- 3 a 5 abordagens plausiveis\n"
        "- Melhor abordagem para esta sessao\n"
        "- Experimentos ou calculos executados\n"
        "- Resultados negativos importantes\n"
        "- Proximo passo concreto\n"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RLM research runner for open math problems")
    parser.add_argument(
        "--model",
        default=os.environ.get("RLM_MODEL", "gpt-5.4-mini"),
        help="Model ID exposed by your provider",
    )
    parser.add_argument(
        "--reasoning-effort",
        default=os.environ.get("RLM_REASONING_EFFORT", "high"),
        help="Passed through to the OpenAI-compatible backend when supported",
    )
    parser.add_argument(
        "--problem",
        default=DEFAULT_PROBLEM,
        help="Research problem statement",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=3,
        help="Maximum recursive depth for sub-agents",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY nao configurada.", file=sys.stderr)
        return 2

    rlm = RLM(
        backend="openai",
        backend_kwargs={
            "model_name": args.model,
            "reasoning": {"effort": args.reasoning_effort},
        },
        environment="local",
        max_depth=args.max_depth,
        verbose=True,
    )

    result = rlm.completion(build_prompt(args.problem), capture_artifacts=True)
    print(result.response)

    artifacts = getattr(result, "artifacts", None)
    if artifacts:
        print("\n[artifacts]")
        print(artifacts)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())