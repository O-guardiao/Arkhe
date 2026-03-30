from __future__ import annotations

from rlm.core.optimized_parsing import find_code_blocks, find_final_answer
from rlm.core.optimized_wire import JSON_BACKEND, json_dumps, json_loads


def benchmark() -> None:
    import time

    test_text = """
Let me solve this problem step by step:

```repl
x = 2 + 2
y = x * 10
print(f\"Result: {y}\")
```

Based on the calculation:

```repl
final_answer = y + 100
```

FINAL(The answer is 140)
"""

    iterations = 10000

    start = time.perf_counter()
    for _ in range(iterations):
        find_code_blocks(test_text)
    elapsed = time.perf_counter() - start
    print(f"find_code_blocks: {iterations} iterations in {elapsed:.3f}s ({iterations / elapsed:.0f} ops/s)")

    start = time.perf_counter()
    for _ in range(iterations):
        find_final_answer(test_text)
    elapsed = time.perf_counter() - start
    print(f"find_final_answer: {iterations} iterations in {elapsed:.3f}s ({iterations / elapsed:.0f} ops/s)")

    test_dict = {"prompt": "Hello world", "model": "gpt-4", "depth": 0, "data": list(range(100))}
    start = time.perf_counter()
    for _ in range(iterations):
        payload = json_dumps(test_dict)
        json_loads(payload)
    elapsed = time.perf_counter() - start
    print(f"json roundtrip ({JSON_BACKEND}): {iterations} iterations in {elapsed:.3f}s ({iterations / elapsed:.0f} ops/s)")


def main() -> None:
    print(f"RLM Optimized - JSON backend: {JSON_BACKEND}")
    print("-" * 50)
    benchmark()
    print("-" * 50)
    print("✅ All benchmarks complete!")


if __name__ == "__main__":
    main()