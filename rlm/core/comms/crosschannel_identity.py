"""
CrossChannelIdentity — Mapeamento de identidade de um usuário em múltiplos canais.

Problema central:
  O mesmo indivíduo usa Telegram, Discord e Slack simultaneamente.
  Sem unificação, cada canal trata a pessoa como usuário distinto:
  sessões separadas, contexto perdido, preferências invisíveis entre canais.

Solução:
  Este módulo mantém um grafo de identidades vinculadas em SQLite.

  Exemplo de uso:

      store = get_crosschannel_identity()

      # O usuário informa: "meu Discord é discord:789012"
      store.link("telegram", "123456", "discord", "789012")

      # A partir daí, RLM sabe que são a mesma pessoa:
      identidades = store.resolve("telegram", "123456")
      # → [LinkedIdentity(channel="telegram", ...), LinkedIdentity(channel="discord", ...)]

      # Usuário prefere receber respostas no Discord:
      store.set_preferred("telegram", "123456", "discord:789012")
      store.get_preferred("telegram", "123456")  # → "discord:789012"

Integração no pipeline:
  ``RoutingPolicy.UserPreferenceRule`` chama ``get_preferred()`` antes de
  consultar ``session.metadata["preferred_channel"]``.  Se o store retornar
  um canal preferido, a resposta é entregue lá — sem que o agente precise
  saber em qual canal o usuário está ativo naquele momento.

Padrão SQLite:
  - DDL inline (CREATE TABLE IF NOT EXISTS) — mesmo padrão de OutboxStore
  - Mesmo arquivo de banco (rlm_sessions.db)
  - check_same_thread=False + Lock de thread (thread-safe)
  - WAL mode para leituras concorrentes sem bloqueio de escrita
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import NamedTuple

from rlm.core.structured_log import get_logger

_log = get_logger("crosschannel_identity")

_DDL = """
CREATE TABLE IF NOT EXISTS crosschannel_links (
    canonical_id  TEXT NOT NULL,
    channel       TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    client_id     TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (channel, user_id)
);

CREATE TABLE IF NOT EXISTS crosschannel_preferences (
    canonical_id        TEXT NOT NULL PRIMARY KEY,
    preferred_client_id TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
"""


class LinkedIdentity(NamedTuple):
    """Identidade vinculada retornada por resolve()."""

    canonical_id: str
    channel: str
    user_id: str
    client_id: str


class CrossChannelIdentityStore:
    """
    Armazena e consulta vínculos de identidade cross-channel em SQLite.

    Thread-safe via Lock interno.  Reutiliza a conexão SQLite de longa
    vida (check_same_thread=False) — mesmo padrão de OutboxStore.

    Não importar diretamente em testes de unidade: use o padrão
    ``init_crosschannel_identity(db_path=":memory:")`` com banco in-memory.
    """

    def __init__(self, db_path: str = "rlm_sessions.db") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)
        self._conn.commit()
        _log.info("CrossChannelIdentityStore ready", db=db_path)

    # ── Vinculação ────────────────────────────────────────────────────────

    def link(
        self,
        channel_a: str,
        user_id_a: str,
        channel_b: str,
        user_id_b: str,
    ) -> str:
        """
        Vincula dois pares (channel, user_id) como sendo o mesmo indivíduo.

        Regras de merge (Fase 1 — sem grafo completo):
          - Se nenhum tem canonical_id → cria UUID novo.
          - Se apenas um tem → o outro herda o mesmo canonical_id.
          - Se ambos têm canonical_ids distintos → B é remapeado para A
            e as entradas de B são migradas (merge simples, sem histórico).

        Returns
        -------
        str
            canonical_id do grupo resultante.
        """
        now = _now()
        with self._lock:
            cur = self._conn.cursor()

            canon_a = self._find_canonical(cur, channel_a, user_id_a)
            canon_b = self._find_canonical(cur, channel_b, user_id_b)

            if canon_a and canon_b:
                # Merge: reassign B's entries to A's canonical_id
                if canon_a != canon_b:
                    cur.execute(
                        "UPDATE crosschannel_links SET canonical_id=? "
                        "WHERE canonical_id=?",
                        (canon_a, canon_b),
                    )
                    cur.execute(
                        "DELETE FROM crosschannel_preferences WHERE canonical_id=?",
                        (canon_b,),
                    )
                canon = canon_a
            elif canon_a:
                canon = canon_a
            elif canon_b:
                canon = canon_b
            else:
                canon = str(uuid.uuid4())

            client_id_a = f"{channel_a}:{user_id_a}"
            client_id_b = f"{channel_b}:{user_id_b}"

            cur.execute(
                "INSERT OR IGNORE INTO crosschannel_links "
                "(canonical_id, channel, user_id, client_id, created_at) "
                "VALUES (?,?,?,?,?)",
                (canon, channel_a, user_id_a, client_id_a, now),
            )
            cur.execute(
                "INSERT OR IGNORE INTO crosschannel_links "
                "(canonical_id, channel, user_id, client_id, created_at) "
                "VALUES (?,?,?,?,?)",
                (canon, channel_b, user_id_b, client_id_b, now),
            )
            self._conn.commit()

        _log.info(
            "Identity linked",
            a=client_id_a,
            b=client_id_b,
            canonical_id=canon,
        )
        return canon

    # ── Resolução ─────────────────────────────────────────────────────────

    def resolve(self, channel: str, user_id: str) -> list[LinkedIdentity]:
        """
        Retorna todos os pares vinculados ao mesmo canonical_id.

        Inclui o próprio par informado.
        Retorna lista vazia se o par não tiver nenhum vínculo registrado.
        """
        with self._lock:
            cur = self._conn.cursor()
            canon = self._find_canonical(cur, channel, user_id)
            if not canon:
                return []
            rows = cur.execute(
                "SELECT canonical_id, channel, user_id, client_id "
                "FROM crosschannel_links WHERE canonical_id=?",
                (canon,),
            ).fetchall()
        return [LinkedIdentity(*r) for r in rows]

    def find_canonical(self, channel: str, user_id: str) -> str | None:
        """Retorna o canonical_id para (channel, user_id) ou None."""
        with self._lock:
            return self._find_canonical(self._conn.cursor(), channel, user_id)

    # ── Preferência de canal ──────────────────────────────────────────────

    def get_preferred(self, channel: str, user_id: str) -> str | None:
        """
        Retorna o client_id preferido (ex: ``"discord:789012"``) para este
        indivíduo, ou None se nenhuma preferência foi configurada.
        """
        with self._lock:
            cur = self._conn.cursor()
            canon = self._find_canonical(cur, channel, user_id)
            if not canon:
                return None
            row = cur.execute(
                "SELECT preferred_client_id FROM crosschannel_preferences "
                "WHERE canonical_id=?",
                (canon,),
            ).fetchone()
        return row[0] if row else None

    def set_preferred(
        self,
        channel: str,
        user_id: str,
        preferred_client_id: str,
    ) -> None:
        """
        Define o canal preferido para este indivíduo.

        Se (channel, user_id) ainda não existe no store, cria um vínculo
        implícito de identidade única (um indivíduo, um canal por enquanto).
        Isso permite configurar preferência sem precisar chamar link() antes.
        """
        now = _now()
        with self._lock:
            cur = self._conn.cursor()
            canon = self._find_canonical(cur, channel, user_id)
            if not canon:
                # Auto-registra como identidade única
                canon = str(uuid.uuid4())
                client_id = f"{channel}:{user_id}"
                cur.execute(
                    "INSERT OR IGNORE INTO crosschannel_links "
                    "(canonical_id, channel, user_id, client_id, created_at) "
                    "VALUES (?,?,?,?,?)",
                    (canon, channel, user_id, client_id, now),
                )
            cur.execute(
                "INSERT OR REPLACE INTO crosschannel_preferences "
                "(canonical_id, preferred_client_id, updated_at) VALUES (?,?,?)",
                (canon, preferred_client_id, now),
            )
            self._conn.commit()

        _log.info(
            "Preferred channel set",
            canonical_id=canon,
            preferred=preferred_client_id,
        )

    # ── Helpers privados ──────────────────────────────────────────────────

    def _find_canonical(
        self, cur: sqlite3.Cursor, channel: str, user_id: str
    ) -> str | None:
        row = cur.execute(
            "SELECT canonical_id FROM crosschannel_links "
            "WHERE channel=? AND user_id=?",
            (channel, user_id),
        ).fetchone()
        return row[0] if row else None


# ── Singleton ─────────────────────────────────────────────────────────────

_store: CrossChannelIdentityStore | None = None
_store_lock = threading.Lock()


def init_crosschannel_identity(
    db_path: str = "rlm_sessions.db",
) -> CrossChannelIdentityStore:
    """
    Inicializa o store singleton de identidade cross-channel.

    Chamado por ``bootstrap_channel_infrastructure()``.
    Pode ser chamado diretamente em scripts ou testes de integração.

    Parameters
    ----------
    db_path : str
        Caminho do SQLite.  Use ``":memory:"`` em testes de unidade.
    """
    global _store
    with _store_lock:
        if _store is None:
            _store = CrossChannelIdentityStore(db_path=db_path)
    return _store


def get_crosschannel_identity() -> CrossChannelIdentityStore | None:
    """
    Retorna o singleton ou None se ainda não inicializado.

    Retornar None é o estado válido em testes de unidade e em binários
    que não passaram pelo bootstrap de canal.  Consumidores devem tratar
    ``None`` graciosamente (ex: ``UserPreferenceRule`` cai no fallback).
    """
    return _store


# ── Helper interno ────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
