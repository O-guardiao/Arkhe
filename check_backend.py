
from rlm.core import fast

def verify():
    print(f"\n[BACKEND CHECK] Active Backend: {fast.BACKEND}")
    if fast.BACKEND == "optimized":
        print("✅ SUCCESS: Optimized Python backend is ACTIVE.")
    else:
        print(f"⚠️  WARNING: Backend is '{fast.BACKEND}'.")

if __name__ == "__main__":
    verify()
