"""Test script for RLM optimized module."""
from rlm.core.fast import find_code_blocks, find_final_answer, BACKEND
import time

# Test data with explicit code blocks
text = '''Here is code:

```repl
x = 10
print(x)
```

And more:

```repl
y = x * 2
```

FINAL(The answer is 20)
'''

print(f"Backend: {BACKEND}")
print(f"Text length: {len(text)}")
print(f"Text repr: {repr(text[:100])}")

# Find code blocks
blocks = find_code_blocks(text)
print(f"\nFound {len(blocks)} code blocks:")
for i, b in enumerate(blocks):
    print(f"  Block {i+1}: {repr(b)}")

# Find final answer
answer = find_final_answer(text)
print(f"\nFinal answer: {answer}")

# Verify
if len(blocks) == 2:
    print("\n✅ Code block test PASSED!")
else:
    print(f"\n❌ Code block test FAILED: expected 2, got {len(blocks)}")

if answer == "The answer is 20":
    print("✅ Final answer test PASSED!")
else:
    print(f"❌ Final answer test FAILED: {answer!r} != 'The answer is 20'")

# Performance benchmark
print("\n--- Performance Benchmark ---")
iterations = 10000

start = time.perf_counter()
for _ in range(iterations):
    find_code_blocks(text)
elapsed = time.perf_counter() - start
print(f"find_code_blocks: {iterations/elapsed:,.0f} ops/sec")

start = time.perf_counter()
for _ in range(iterations):
    find_final_answer(text)
elapsed = time.perf_counter() - start
print(f"find_final_answer: {iterations/elapsed:,.0f} ops/sec")

# JSON Benchmark (simulated via socket_send imports if available)
try:
    from rlm.core.fast import socket_send
    import socket
    
    # Real socket pair for valid FD
    server, client = socket.socketpair()
    # Set non-blocking to avoid hanging if buffer fills (we just test serialization speed)
    server.setblocking(False)
    client.setblocking(False)
    
    msg = {"prompt": "Hello world " * 100, "data": list(range(100))}  # Smaller payload to avoid blocking
    
    print("\n--- JSON/Socket Benchmark ---")
    
    import threading

    def drain():
        while True:
            try:
                if not client.recv(4096): break
            except: pass
    t = threading.Thread(target=drain, daemon=True)
    t.start()
    
    start = time.perf_counter()
    
    target = server

    sent_count = 0
    start = time.perf_counter()
    
    try:
        for i in range(iterations):
            try:
                socket_send(target, msg)
                sent_count += 1
            except BlockingIOError:
                pass
            except Exception as e:
                if "10035" in str(e) or getattr(e, 'winerror', 0) == 10035:
                    pass
                else:
                    raise e
    finally:
        elapsed = time.perf_counter() - start

    print(f"socket_send: {sent_count/elapsed:,.0f} ops/sec (successful sends)")
    if sent_count < iterations:
        print(f"  Note: Saturated buffer at {sent_count} iterations (Consumer too slow)")
    
    server.close()
    client.close()
    
except Exception as e:
    print(f"\nSkipping JSON bench: {e}")

print("\n✅ All tests complete!")
