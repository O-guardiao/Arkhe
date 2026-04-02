from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rlm.core.security.execution_policy import CostSlice, estimate_architecture_cost, parse_price_table


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Estimate RLM architecture cost from a price table.")
    parser.add_argument("prices_file", type=Path, help="Path to the markdown price table.")
    parser.add_argument("--planner-input", type=int, default=12000)
    parser.add_argument("--planner-output", type=int, default=900)
    parser.add_argument("--worker-calls", type=int, default=3)
    parser.add_argument("--worker-input", type=int, default=6000)
    parser.add_argument("--worker-output", type=int, default=500)
    parser.add_argument("--response-calls", type=int, default=1)
    parser.add_argument("--response-input", type=int, default=2500)
    parser.add_argument("--response-output", type=int, default=350)
    parser.add_argument("--minirepl-calls", type=int, default=4)
    parser.add_argument("--minirepl-input", type=int, default=1200)
    parser.add_argument("--minirepl-output", type=int, default=120)
    parser.add_argument("--planner-model", default="gpt-5.4")
    parser.add_argument("--worker-model", default="gpt-5.4-mini")
    parser.add_argument("--response-model", default="gpt-5.4-nano")
    parser.add_argument("--minirepl-model", default="gpt-5-nano")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    prices = parse_price_table(args.prices_file.read_text(encoding="utf-8"))

    monolith = [
        CostSlice(
            label="all-in-one",
            model_name=args.planner_model,
            input_tokens=args.planner_input + args.worker_calls * args.worker_input + args.response_calls * args.response_input + args.minirepl_calls * args.minirepl_input,
            output_tokens=args.planner_output + args.worker_calls * args.worker_output + args.response_calls * args.response_output + args.minirepl_calls * args.minirepl_output,
        ),
    ]
    depth_split = [
        CostSlice("planner", args.planner_model, args.planner_input, args.planner_output),
        CostSlice("workers", args.worker_model, args.worker_input, args.worker_output, calls=args.worker_calls),
        CostSlice("responses", args.worker_model, args.response_input, args.response_output, calls=args.response_calls),
        CostSlice("minirepl", args.worker_model, args.minirepl_input, args.minirepl_output, calls=args.minirepl_calls),
    ]
    policy_split = [
        CostSlice("planner", args.planner_model, args.planner_input, args.planner_output),
        CostSlice("workers", args.worker_model, args.worker_input, args.worker_output, calls=args.worker_calls),
        CostSlice("responses", args.response_model, args.response_input, args.response_output, calls=args.response_calls),
        CostSlice("minirepl", args.minirepl_model, args.minirepl_input, args.minirepl_output, calls=args.minirepl_calls),
    ]

    totals = {
        "monolith": estimate_architecture_cost(prices, monolith),
        "depth_split": estimate_architecture_cost(prices, depth_split),
        "policy_split": estimate_architecture_cost(prices, policy_split),
    }

    baseline = totals["monolith"]
    print("Architecture cost comparison\n")
    for name, total in totals.items():
        savings = baseline - total
        ratio = 0.0 if baseline == 0 else (savings / baseline) * 100.0
        print(f"- {name}: ${total:.6f}  savings_vs_monolith=${savings:.6f} ({ratio:.2f}%)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())