"""
turn_telemetry.py — Phase 1: Métricas por Turno

Responsabilidade única: registrar o custo (tokens, tempo, memória injetada,
compactação) de cada turno de conversa em formato JSONL append-only.

Princípios de design:
  - Nunca bloqueia o turno principal. Escrita em JSONL é feita em background.
  - Falha silenciosa total: qualquer exceção é suprimida para não derrubar o chat.
  - Uma instância de TurnTelemetryStore por RLMSession.
  - O JSONL é append-only — leitura/análise é feita por ferramentas externas.

Schema JSONL (um objeto JSON por linha):
  {
    "session_id": str,
    "turn_id": int,                    # sequencial dentro da sessão (1-based)
    "model_name": str,                 # modelo usado pelo RLM principal
    "timestamp_start": float,          # epoch seconds
    "timestamp_end": float,            # epoch seconds
    "elapsed_s": float,                # tempo total do turno
    "tokens_in": int,                  # total input tokens (todas as iterações)
    "tokens_out": int,                 # total output tokens (todas as iterações)
    "iterations": int,                 # iterações internas do RLM neste turno
    "memory_chunks_retrieved": int,    # chunks buscados na memória
    "memory_chunks_injected": int,     # chunks que couberam no budget
    "memory_tokens_injected": int,     # tokens gastos com memória no prompt
    "memory_budget_used_pct": float,   # % do budget de memória consumido (0-1)
    "compaction_triggered": bool,      # se compactação foi disparada neste turno
    "is_complete": bool                # RLM entregou FINAL_ANSWER antes do limite
  }
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from rlm.core.structured_log import get_logger

_log = get_logger("turn_telemetry")

# Diretório base para os arquivos JSONL. Criado automaticamente.
_TELEMETRY_DIR = os.path.join(".rlm_workspace", "telemetry")


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class TurnTelemetry:
    """
    Representa o registro de métricas de um único turno de conversa.

    Criado por TurnTelemetryStore.start_turn() e finalizado por finish_turn().
    Campos marcados com valor padrão são preenchidos em finish_turn().
    """

    # Identidade
    session_id: str
    turn_id: int
    model_name: str

    # Temporal
    timestamp_start: float = field(default_factory=time.time)
    timestamp_end: float = 0.0
    elapsed_s: float = 0.0

    # Uso de tokens (obtidos de RLMChatCompletion.usage_summary após completion)
    tokens_in: int = 0
    tokens_out: int = 0
    iterations: int = 0
    is_complete: bool = True

    # Memória injetada
    memory_chunks_retrieved: int = 0   # chunks encontrados na busca
    memory_chunks_injected: int = 0    # chunks que entraram no prompt (budget gate)
    memory_tokens_injected: int = 0    # tokens estimados usados por memória
    memory_budget_used_pct: float = 0.0  # fração do budget de memória consumida

    # Compactação
    compaction_triggered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class TurnTelemetryStore:
    """
    Gerencia a telemetria de turno para uma sessão.

    Uso:
        store = TurnTelemetryStore(session_id="abc123", model_name="gpt-4o-mini")

        tel = store.start_turn()
        # ... executa completion ...
        store.finish_turn(tel, completion=completion_obj, memory_chunks_retrieved=5,
                         memory_chunks_injected=3, memory_tokens_injected=420)

    A escrita no disco é feita em daemon thread (fire-and-forget).
    """

    def __init__(self, session_id: str, model_name: str = "unknown") -> None:
        self._session_id = session_id
        self._model_name = model_name
        self._turn_counter = 0
        self._lock = threading.Lock()
        self._jsonl_path = os.path.join(_TELEMETRY_DIR, f"{session_id}.jsonl")
        self._session_stats: dict[str, Any] = {
            "total_turns": 0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "total_memory_injected": 0,
            "total_elapsed_s": 0.0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_turn(self) -> TurnTelemetry:
        """
        Inicia o registro de um novo turno. Deve ser chamado ANTES do completion().

        Returns:
            TurnTelemetry — objeto mutable a ser enriquecido e passado para finish_turn().
        """
        with self._lock:
            self._turn_counter += 1
            turn_id = self._turn_counter

        return TurnTelemetry(
            session_id=self._session_id,
            turn_id=turn_id,
            model_name=self._model_name,
            timestamp_start=time.time(),
        )

    def finish_turn(
        self,
        tel: TurnTelemetry,
        *,
        completion: Any | None = None,     # RLMChatCompletion — opcional, lido via duck typing
        memory_chunks_retrieved: int = 0,
        memory_chunks_injected: int = 0,
        memory_tokens_injected: int = 0,
        memory_budget_used_pct: float = 0.0,
        compaction_triggered: bool = False,
    ) -> None:
        """
        Finaliza e persiste a telemetria do turno.

        Args:
            tel: Objeto criado por start_turn().
            completion: RLMChatCompletion da chamada (duck-typed para evitar import circular).
            memory_chunks_retrieved: Chunks encontrados no search_hybrid.
            memory_chunks_injected: Chunks que passaram pelo budget gate.
            memory_tokens_injected: Tokens estimados consumidos por memória.
            memory_budget_used_pct: Fração do budget de memória consumida (0.0–1.0).
            compaction_triggered: Se a compactação de contexto foi disparada.
        """
        try:
            tel.timestamp_end = time.time()
            tel.elapsed_s = round(tel.timestamp_end - tel.timestamp_start, 4)
            tel.memory_chunks_retrieved = memory_chunks_retrieved
            tel.memory_chunks_injected = memory_chunks_injected
            tel.memory_tokens_injected = memory_tokens_injected
            tel.memory_budget_used_pct = round(memory_budget_used_pct, 4)
            tel.compaction_triggered = compaction_triggered

            # Extrai usage de RLMChatCompletion via duck typing (sem import circular)
            if completion is not None:
                try:
                    usage = getattr(completion, "usage_summary", None)
                    is_complete = getattr(completion, "is_complete", True)
                    tel.is_complete = is_complete
                    if usage is not None:
                        summaries = getattr(usage, "model_usage_summaries", {})
                        for _, model_usage in summaries.items():
                            tel.tokens_in += getattr(model_usage, "total_input_tokens", 0) or 0
                            tel.tokens_out += getattr(model_usage, "total_output_tokens", 0) or 0
                            total_calls = getattr(model_usage, "total_calls", 0) or 0
                            tel.iterations = max(tel.iterations, total_calls)
                except Exception:
                    pass  # token counting nunca bloqueia

            # Atualiza stats da sessão em memória
            with self._lock:
                self._session_stats["total_turns"] += 1
                self._session_stats["total_tokens_in"] += tel.tokens_in
                self._session_stats["total_tokens_out"] += tel.tokens_out
                self._session_stats["total_memory_injected"] += tel.memory_chunks_injected
                self._session_stats["total_elapsed_s"] = round(
                    self._session_stats["total_elapsed_s"] + tel.elapsed_s, 4
                )

            # Persiste em JSONL em background (não bloqueia o turno)
            threading.Thread(
                target=self._write_jsonl,
                args=(tel.to_jsonl(),),
                daemon=True,
                name="rlm-telemetry-writer",
            ).start()

        except Exception as exc:
            # Telemetria NUNCA pode derrubar um turno de chat
            _log.warn(f"TurnTelemetryStore.finish_turn failed (suppressed): {exc}")

    def get_session_stats(self) -> dict[str, Any]:
        """
        Retorna estatísticas acumuladas da sessão atual (in-memory, sem I/O).

        Returns:
            Dict com total_turns, total_tokens_in, total_tokens_out,
            total_memory_injected, total_elapsed_s.
        """
        with self._lock:
            return dict(self._session_stats)

    def set_model_name(self, model_name: str) -> None:
        """Atualiza o nome do modelo (útil se o backend mudar entre turnos)."""
        self._model_name = model_name

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write_jsonl(self, line: str) -> None:
        """Append de uma linha JSONL. Cria o diretório se necessário. Falha silenciosa."""
        try:
            os.makedirs(_TELEMETRY_DIR, exist_ok=True)
            with open(self._jsonl_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception as exc:
            _log.warn(f"TurnTelemetryStore: falha ao escrever JSONL (suprimida): {exc}")
