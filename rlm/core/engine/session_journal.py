"""
SessionJournal — persistência JSONL de conversas.

Features:
  - Append-only em arquivo .jsonl
  - Rotação automática quando arquivo ≥ ROTATE_AFTER_BYTES (256 KB)
  - Mantém no máximo MAX_ROTATED_FILES arquivos rotacionados
  - Rollback automático se a escrita falhar (write + rename atômico)
  - Thread-safe via threading.RLock
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

ROTATE_AFTER_BYTES: int = 256 * 1024   # 256 KB
MAX_ROTATED_FILES: int = 3


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

Role = Literal["system", "user", "assistant", "tool", "tool_result"]


@dataclass(frozen=True)
class JournalEntry:
    """Uma entrada no diário de sessão."""

    role: Role
    content: Any
    timestamp: float
    session_id: str
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
        }
        if self.extra:
            d["extra"] = self.extra
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "JournalEntry":
        return cls(
            role=d["role"],
            content=d["content"],
            timestamp=float(d.get("timestamp", 0.0)),
            session_id=d.get("session_id", ""),
            extra=d.get("extra"),
        )


# ---------------------------------------------------------------------------
# SessionJournal
# ---------------------------------------------------------------------------

class SessionJournal:
    """Diário de sessão em JSONL com rotação automática.

    Args:
        data_dir: Diretório onde os arquivos de sessão serão armazenados.
        session_id: Identificador único da sessão atual.
        rotate_after_bytes: Tamanho em bytes ao atingir o qual o arquivo é rotacionado.
        max_rotated_files: Número máximo de arquivos rotacionados a manter.
    """

    def __init__(
        self,
        data_dir: Path,
        session_id: str,
        rotate_after_bytes: int = ROTATE_AFTER_BYTES,
        max_rotated_files: int = MAX_ROTATED_FILES,
    ) -> None:
        self._dir = data_dir
        self._session_id = session_id
        self._rotate_after = rotate_after_bytes
        self._max_rotated = max_rotated_files
        self._lock = threading.RLock()

        self._dir.mkdir(parents=True, exist_ok=True)
        self._active = self._dir / f"{session_id}.jsonl"

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------

    def push_message(
        self,
        role: Role,
        content: Any,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Adiciona mensagem ao diário.

        A escrita é atômica: primeiro grava em arquivo temporário, depois
        executa rename. Se falhar, o arquivo original não é corrompido.
        """
        entry = JournalEntry(
            role=role,
            content=content,
            timestamp=time.time(),
            session_id=self._session_id,
            extra=extra,
        )
        line = json.dumps(entry.to_dict(), ensure_ascii=False) + "\n"
        encoded = line.encode("utf-8")

        with self._lock:
            self._rotate_if_needed()
            self._append_atomic(self._active, encoded)

    def push_tool_result(self, tool_name: str, result: Any, success: bool = True) -> None:
        """Atalho para adicionar resultado de ferramenta."""
        self.push_message(
            role="tool_result",
            content=result,
            extra={"tool": tool_name, "success": success},
        )

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    def load_messages(self, include_rotated: bool = False) -> list[dict[str, Any]]:
        """Lê todas as mensagens do diário.

        Args:
            include_rotated: Se True, inclui também os arquivos rotacionados,
                             em ordem cronológico (mais antigo primeiro).
        """
        with self._lock:
            files: list[Path] = []

            if include_rotated:
                rotated = sorted(self._dir.glob(f"{self._session_id}.*.jsonl"))
                files.extend(rotated)

            if self._active.exists():
                files.append(self._active)

            messages: list[dict[str, Any]] = []
            for path in files:
                messages.extend(self._read_file(path))
            return messages

    def count_messages(self) -> int:
        """Conta total de mensagens (sem carregar conteúdo)."""
        return len(self.load_messages(include_rotated=False))

    # ------------------------------------------------------------------
    # Rotação
    # ------------------------------------------------------------------

    def _rotate_if_needed(self) -> None:
        """Rotaciona arquivo ativo se ultrapassou o limite de tamanho.

        Chamado enquanto _lock está adquirido.
        """
        if not self._active.exists():
            return

        if self._active.stat().st_size < self._rotate_after:
            return

        # Rotacionar: renomear ativo para timestamped
        ts = int(time.time())
        rotated_name = self._dir / f"{self._session_id}.{ts}.jsonl"
        self._active.rename(rotated_name)
        logger.debug("Rotacionado %s → %s", self._active.name, rotated_name.name)

        # Limpar arquivos antigos se exceder o máximo
        self._prune_rotated()

    def _prune_rotated(self) -> None:
        """Remove os arquivos rotacionados mais antigos, mantendo apenas os N mais recentes."""
        rotated = sorted(self._dir.glob(f"{self._session_id}.*.jsonl"))
        excess = len(rotated) - self._max_rotated
        for old in rotated[:excess]:
            try:
                old.unlink()
                logger.debug("Removido arquivo rotacionado antigo: %s", old.name)
            except OSError:
                logger.warning("Falha ao remover %s", old.name, exc_info=True)

    # ------------------------------------------------------------------
    # I/O auxiliar
    # ------------------------------------------------------------------

    @staticmethod
    def _append_atomic(target: Path, data: bytes) -> None:
        """Garante escrita atômica usando arquivo temporário + rename."""
        tmp = target.with_suffix(".tmp")
        try:
            # Copiar conteúdo existente + nova linha
            existing = target.read_bytes() if target.exists() else b""
            tmp.write_bytes(existing + data)
            tmp.replace(target)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _read_file(path: Path) -> list[dict[str, Any]]:
        """Lê arquivo JSONL, ignorando linhas inválidas."""
        messages: list[dict[str, Any]] = []
        try:
            for lineno, raw in enumerate(path.read_text("utf-8").splitlines(), 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    messages.append(json.loads(raw))
                except json.JSONDecodeError:
                    logger.warning("Linha %d inválida em %s — ignorada", lineno, path.name)
        except OSError:
            logger.warning("Não foi possível ler %s", path.name, exc_info=True)
        return messages

    # ------------------------------------------------------------------
    # Utilitários
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Remove todos os arquivos de sessão (incluindo rotacionados)."""
        with self._lock:
            for f in self._dir.glob(f"{self._session_id}*.jsonl"):
                f.unlink(missing_ok=True)

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def active_path(self) -> Path:
        return self._active
