
import pytest
from rlm.core import fast

def test_backend_is_rust():
    """Verify that the active backend is one of the supported fast backends."""
    print(f"\n\n[BACKEND CHECK] Active Backend: {fast.BACKEND}")
    if fast.BACKEND == "rust":
        print("✅ SUCCESS: Rust backend requires no Python overhead.")
    elif fast.BACKEND == "optimized":
        print("✅ SUCCESS: Running on Optimized Python backend.")
    else:
        print("❌ FAILURE: Running on Slow/Original Python.")
    
    assert fast.BACKEND in {"rust", "optimized"}, (
        f"Expected a supported fast backend, got '{fast.BACKEND}'"
    )
