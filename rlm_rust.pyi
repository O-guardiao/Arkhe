"""
Type stubs for rlm_rust — Rust extension module (PyO3).

Gerado manualmente como contrato de tipos para o Pylance.
O binário real é compilado via `maturin build --release` na pasta rlm_rust/.
"""
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Socket utilities
# ---------------------------------------------------------------------------

def socket_send(fd_or_sock: Any, data: Any) -> None: ...
def socket_recv(fd_or_sock: Any) -> Any: ...
def socket_request(address: Any, data: Any, timeout: int = 300) -> Any: ...

# ---------------------------------------------------------------------------
# Parsing utilities
# ---------------------------------------------------------------------------

def find_code_blocks(text: str) -> list[str]: ...
def find_final_answer(text: str) -> Optional[str]: ...

# ---------------------------------------------------------------------------
# Optional extensions (available after rebuild with new exports)
# ---------------------------------------------------------------------------

def format_iteration_rs(iteration: int, content: str) -> str: ...
def compute_hash(data: str) -> int: ...

# ---------------------------------------------------------------------------
# LM Handler
# ---------------------------------------------------------------------------

class RustLMHandler:
    def __init__(self, host: str, port: int, model: str) -> None: ...
    def completion(self, prompt: str) -> str: ...
    def close(self) -> None: ...
