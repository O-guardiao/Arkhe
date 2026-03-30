"""
RLM Tool Loop Detection — Fase 8.2

Inspirado em: OpenClaw agents/tool-loop-detection.ts (624 LOC)

Detecta quando o RLM está preso em loops de código repetitivo no REPL:
- Generic Repeat: mesmo código sendo executado N vezes
- Ping-Pong: alternando entre 2 blocos de código sem progresso
- No Progress: mesmo output repetindo (ex: mesmo erro infinitamente)

Integração: chamado em _completion_turn() após cada execute_code().
"""
import hashlib
from dataclasses import dataclass, field
from typing import Any

try:
    from rlm.core.fast import compute_hash as _compute_hash_fast
    if _compute_hash_fast is None:
        raise ImportError
except (ImportError, AttributeError):
    _compute_hash_fast = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class LoopDetectorConfig:
    """Configuração do detector de loops."""
    history_size: int = 30             # Janela de chamadas recentes
    warning_threshold: int = 10        # Repetições para aviso
    critical_threshold: int = 20       # Repetições para abort
    global_circuit_breaker: int = 30   # Limite absoluto (para mesmo que unknowns)
    detectors: dict = field(default_factory=lambda: {
        "generic_repeat": True,        # Mesmo código repetindo
        "ping_pong": True,             # Alternando entre 2 blocos
        "no_progress": True,           # Mesmo output sem mudança
    })


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class LoopDetectionResult:
    """Resultado de uma verificação de loop."""
    stuck: bool = False
    level: str = "ok"                  # ok | warning | critical
    detector: str = ""                 # Qual detector disparou
    count: int = 0                     # Quantas repetições detectadas
    message: str = ""                  # Descrição humana
    paired_code: str = ""              # Em ping-pong, o segundo bloco


# ---------------------------------------------------------------------------
# History Entry
# ---------------------------------------------------------------------------

@dataclass
class CodeExecution:
    """Registro de uma execução de código no REPL."""
    code_hash: str                     # Hash do código executado
    output_hash: str                   # Hash do output
    code_preview: str                  # Primeiros 100 chars do código
    output_preview: str                # Primeiros 100 chars do output
    is_error: bool = False


# ---------------------------------------------------------------------------
# Loop Detector
# ---------------------------------------------------------------------------

class LoopDetector:
    """
    Detecta padrões de loop no REPL do RLM.
    
    Três detectores independentes:
    
    1. Generic Repeat: hash do código aparecendo N vezes consecutivas
    2. Ping-Pong: alternando entre 2 hashes ABAB...
    3. No Progress: output idêntico N vezes (mesmo sem código idêntico)
    
    Usage:
        detector = LoopDetector()
        
        # After each REPL execution:
        detector.record(code="print('hello')", output="hello")
        result = detector.check()
        
        if result.stuck:
            print(f"Loop detected: {result.message}")
    """

    def __init__(self, config: LoopDetectorConfig | None = None):
        self.config = config or LoopDetectorConfig()
        self._history: list[CodeExecution] = []
        self._warning_keys_seen: set[str] = set()

    def record(self, code: str, output: str, is_error: bool = False) -> None:
        """
        Record a code execution in the history.
        
        Args:
            code: The code that was executed.
            output: The output/result.
            is_error: Whether the execution resulted in an error.
        """
        entry = CodeExecution(
            code_hash=_hash(code),
            output_hash=_hash(output),
            code_preview=code[:100].strip(),
            output_preview=output[:100].strip(),
            is_error=is_error,
        )
        self._history.append(entry)

        # Maintain sliding window
        if len(self._history) > self.config.history_size:
            self._history = self._history[-self.config.history_size:]

    def check(self) -> LoopDetectionResult:
        """
        Check all active detectors for loop patterns.
        
        Returns the most severe detection result.
        """
        if len(self._history) < 3:
            return LoopDetectionResult()

        results = []

        if self.config.detectors.get("generic_repeat", True):
            results.append(self._check_generic_repeat())

        if self.config.detectors.get("ping_pong", True):
            results.append(self._check_ping_pong())

        if self.config.detectors.get("no_progress", True):
            results.append(self._check_no_progress())

        # Return most severe result
        for level in ("critical", "warning"):
            for r in results:
                if r.level == level:
                    return r

        return LoopDetectionResult()

    def reset(self) -> None:
        """Clear the history and start fresh."""
        self._history.clear()
        self._warning_keys_seen.clear()

    def get_stats(self) -> dict:
        """Get current statistics for monitoring."""
        if not self._history:
            return {"total_executions": 0, "unique_codes": 0, "unique_outputs": 0}

        code_hashes = set(e.code_hash for e in self._history)
        output_hashes = set(e.output_hash for e in self._history)
        error_count = sum(1 for e in self._history if e.is_error)

        return {
            "total_executions": len(self._history),
            "unique_codes": len(code_hashes),
            "unique_outputs": len(output_hashes),
            "error_count": error_count,
            "most_recent_code": self._history[-1].code_preview if self._history else "",
        }

    # --- Detectors ---

    def _check_generic_repeat(self) -> LoopDetectionResult:
        """
        Detect the same code executing consecutively.
        
        Counts how many times the most recent code_hash appears
        in a row from the end of history.
        """
        if not self._history:
            return LoopDetectionResult()

        current_hash = self._history[-1].code_hash
        streak = 0
        for entry in reversed(self._history):
            if entry.code_hash == current_hash:
                streak += 1
            else:
                break

        if streak >= self.config.critical_threshold:
            return LoopDetectionResult(
                stuck=True,
                level="critical",
                detector="generic_repeat",
                count=streak,
                message=f"Same code repeated {streak} times: '{self._history[-1].code_preview}...'",
            )
        elif streak >= self.config.warning_threshold:
            return LoopDetectionResult(
                stuck=True,
                level="warning",
                detector="generic_repeat",
                count=streak,
                message=f"Code repeating ({streak}x): '{self._history[-1].code_preview}...'",
            )

        return LoopDetectionResult()

    def _check_ping_pong(self) -> LoopDetectionResult:
        """
        Detect alternating between two code blocks (ABABAB...).
        
        Looks at the last entries and checks if they alternate between
        exactly 2 unique code hashes.
        """
        if len(self._history) < 4:
            return LoopDetectionResult()

        # Get last N entries
        recent = self._history[-min(len(self._history), self.config.history_size):]
        if len(recent) < 4:
            return LoopDetectionResult()

        # Check if last entries alternate between exactly 2 hashes
        last_hash = recent[-1].code_hash
        second_hash = recent[-2].code_hash

        if last_hash == second_hash:
            return LoopDetectionResult()  # Not alternating

        # Count the alternating pattern from the end
        streak = 0
        expected = [last_hash, second_hash]
        for i, entry in enumerate(reversed(recent)):
            if entry.code_hash == expected[i % 2]:
                streak += 1
            else:
                break

        if streak >= self.config.critical_threshold:
            return LoopDetectionResult(
                stuck=True,
                level="critical",
                detector="ping_pong",
                count=streak,
                message=f"Ping-pong loop ({streak} alternations) between 2 code blocks",
                paired_code=recent[-2].code_preview,
            )
        elif streak >= self.config.warning_threshold:
            return LoopDetectionResult(
                stuck=True,
                level="warning",
                detector="ping_pong",
                count=streak,
                message=f"Possible ping-pong ({streak}x) between 2 code blocks",
                paired_code=recent[-2].code_preview,
            )

        return LoopDetectionResult()

    def _check_no_progress(self) -> LoopDetectionResult:
        """
        Detect same output repeating (even with different code).
        
        If the REPL is producing identical output N times in a row,
        the agent isn't making progress regardless of what code it writes.
        """
        if len(self._history) < 3:
            return LoopDetectionResult()

        current_output = self._history[-1].output_hash
        streak = 0
        for entry in reversed(self._history):
            if entry.output_hash == current_output:
                streak += 1
            else:
                break

        # No progress threshold is lower than generic repeat
        # because identical output is a stronger signal
        no_progress_warning = max(3, self.config.warning_threshold // 2)
        no_progress_critical = max(5, self.config.critical_threshold // 2)

        if streak >= no_progress_critical:
            return LoopDetectionResult(
                stuck=True,
                level="critical",
                detector="no_progress",
                count=streak,
                message=f"No progress: identical output {streak} times: '{self._history[-1].output_preview}...'",
            )
        elif streak >= no_progress_warning:
            return LoopDetectionResult(
                stuck=True,
                level="warning",
                detector="no_progress",
                count=streak,
                message=f"Possible stall: same output {streak}x: '{self._history[-1].output_preview}...'",
            )

        return LoopDetectionResult()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash(text: str) -> str:
    """Generate a short hash for pattern matching."""
    if _compute_hash_fast is not None:
        return _compute_hash_fast(text)
    return hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()[:12]
