
import pytest
from rlm.core import fast

def test_backend_is_optimized_python():
    """Verify that the repository runs on the optimized Python backend."""
    print(f"\n\n[BACKEND CHECK] Active Backend: {fast.BACKEND}")
    if fast.BACKEND == "optimized":
        print("✅ SUCCESS: Running on Optimized Python backend.")
    else:
        print("❌ FAILURE: Running on Slow/Original Python.")

    assert fast.BACKEND == "optimized", f"Expected optimized backend, got '{fast.BACKEND}'"
