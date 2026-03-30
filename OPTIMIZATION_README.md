# RLM Optimization: Technical Documentation

This document describes the pure-Python performance path currently maintained in RLM. The repository no longer ships or activates a Rust backend.

## 📂 File Manifest

### Core Integration

* `rlm/core/fast.py`: **Facade Module**. The main entry point. It prefers the optimized Python backend and falls back to the original implementation only if the optimized path cannot load. It exports a unified API (`socket_send`, `find_final_answer`, etc.) so the rest of the codebase does not need backend-specific branching.

### Strategy: Optimized Python

* `rlm/core/optimized.py`: High-performance pure Python facade.
* `rlm/core/optimized_parsing.py`: compiled regex parsing, short hashing, and fast iteration formatting helpers.
* `rlm/core/optimized_wire.py`: framing, JSON serialization, exact socket reads, and typed LM request helpers.
* `rlm/core/optimized_types.py`: typed LM request/response dataclasses.
* `rlm/core/optimized_benchmark.py`: benchmark entry point kept separate from runtime imports.

The optimized Python path focuses on:

* **JSON**: Uses `orjson` when available, which is much faster than standard `json`.
* **Parsing**: Uses `re.compile()` to cache regex state machines, avoiding recompilation on every call.
* **I/O**: Uses `memoryview`, exact reads, and capped frame sizes to reduce copying and harden socket handling.
* **Compatibility helpers**: Keeps `compute_hash` and `format_iteration_rs` available from Python so higher layers do not depend on native code.

---

## 🛠️ Technical Deep Dive

### 1. Python Optimization Strategy

We squeezed the maximum performance practical out of Python without another runtime layer.

* **Problem**: `json.loads` and `json.dumps` are slow for large LLM payloads.
* **Solution**: Switched to `orjson` when available. It returns bytes directly and avoids intermediate string allocations.
* **Problem**: TCP framing is stream-based; naive code often assumes one `recv()` returns a full header or payload.
* **Solution**: Added exact-read framing with a configurable frame-size cap and JSON-object validation.
* **Problem**: Formatting and hashing helpers previously depended on optional native hooks.
* **Solution**: Added optimized Python implementations for short hashes and iteration formatting so the fast path stays fully in Python.

---

## 📊 Performance Benchmarks

| Component | Operation | Python Original | Python Optimized |
| :--- | :--- | :--- | :--- |
| **Parsing** | Find Final Answer | 45k ops/s | 233k ops/s |
| **Parsing** | Extract Code | 60k ops/s | 388k ops/s |
| **Comms** | JSON Serialization | Baseline | 28x Faster |
| **Comms** | Socket Throughput | Limited | High |

## Usage

No manual changes are required. The system is designed to prefer the optimized Python path automatically.

* If the optimized backend loads, it is used.
* If not, the system silently falls back to the original implementation.

To verify your current backend:

```python
from rlm.core.fast import print_backend_info
print_backend_info()
```

## ⚠️ Troubleshooting & Safety

### Partial header reads / oversized frames

If socket parsing fails, verify the peer is respecting the 4-byte length-prefixed JSON protocol.

* **Cause:** TCP framing is stream-based; a single `recv()` is not guaranteed to return the full header or payload.
* **Fix:** The optimized backend now uses exact reads and rejects frames above `RLM_MAX_SOCKET_FRAME_BYTES`.

### Invalid Unicode in payloads

If a payload contains broken surrogate pairs, the optimized backend sanitizes them before serialization.

* **Cause:** Some upstream payloads may contain invalid UTF-16 surrogate code points.
* **Fix:** The serializer normalizes invalid surrogates to replacement characters before encoding.
