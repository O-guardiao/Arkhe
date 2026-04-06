"""
session_key.py — Utilitários de SessionKey e SessionId.

Porta fiel de packages/sessions/src/session-key.ts para Python.

Fornece:
- ``SessionId``        — alias de str com validação de formato (32 hex lowercase)
- ``SessionKey``       — dataclass composta (session_id, channel_type, channel_id, user_id)
- ``create_session_id()``  — gera novo SessionId com 16 bytes aleatórios
- ``is_session_id(v)``     — valida formato
- ``make_session_id(raw)`` — cast com validação, levanta ValueError em formato inválido
- ``encode_session_key(k)``— "channel_type:channel_id:user_id" (sem session_id)
- ``decode_session_key(s)``— inverte encode; session_id gerado fresco; None se inválido
- ``session_key_hash(k)``  — SHA-256 hex determinístico sobre os 3 campos de canal
"""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import NewType

# ---------------------------------------------------------------------------
# SessionId — string com formato garantido (32 hex lowercase)
# ---------------------------------------------------------------------------

SessionId = NewType("SessionId", str)

_SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def create_session_id() -> SessionId:
    """Gera um novo SessionId de 32 caracteres hex (16 bytes aleatórios)."""
    return SessionId(secrets.token_hex(16))


def is_session_id(value: str) -> bool:
    """Retorna True se *value* é um SessionId válido (32 hex lowercase)."""
    return bool(_SESSION_ID_RE.match(value))


def make_session_id(raw: str) -> SessionId:
    """
    Converte *raw* para SessionId com validação em runtime.
    Levanta ``ValueError`` se o formato for inválido.
    """
    if not is_session_id(raw):
        raise ValueError(f"Invalid SessionId format: {raw!r}")
    return SessionId(raw)


# ---------------------------------------------------------------------------
# SessionKey — chave composta de contexto de canal
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionKey:
    """
    Chave composta que identifica uma sessão pelo seu contexto de canal.

    ``session_id`` é o identificador canônico único.
    ``channel_type``, ``channel_id`` e ``user_id`` formam a *natural key*
    usada para lookups de deduplicação (análogo ao TypeScript SessionKey).
    """
    session_id: SessionId
    channel_type: str
    channel_id: str
    user_id: str


# ---------------------------------------------------------------------------
# Encode / Decode
# ---------------------------------------------------------------------------

def encode_session_key(key: SessionKey) -> str:
    """
    Produz representação compacta dos três campos de canal:
    ``"{channel_type}:{channel_id}:{user_id}"``

    O ``session_id`` é intencionalmente excluído para que o encoding sirva
    como lookup key estável para uma combinação canal+usuário entre sessões.
    """
    return f"{key.channel_type}:{key.channel_id}:{key.user_id}"


def decode_session_key(encoded: str) -> SessionKey | None:
    """
    Decodifica um string codificado por ``encode_session_key`` de volta
    para um ``SessionKey``.

    Retorna ``None`` se o string não contiver exatamente três segmentos
    não-vazios separados por ``:``.

    NOTA: o ``session_id`` retornado é um identificador gerado fresco —
    use esta função para lookup, não para reconstrução de identidade.
    """
    if not encoded or not encoded.strip():
        return None

    parts = encoded.split(":", 2)
    if len(parts) != 3:
        return None

    channel_type, channel_id, user_id = parts
    if not channel_type or not channel_id or not user_id:
        return None

    return SessionKey(
        session_id=create_session_id(),
        channel_type=channel_type,
        channel_id=channel_id,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# Hash determinístico
# ---------------------------------------------------------------------------

def session_key_hash(key: SessionKey) -> str:
    """
    Calcula SHA-256 hex digest determinístico sobre os três campos de canal
    de um ``SessionKey``.  Adequado para uso como chave de armazenamento
    ou roteamento.

    Porta exata de ``sessionKeyHash()`` em session-key.ts.
    """
    payload = f"{key.channel_type}:{key.channel_id}:{key.user_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
