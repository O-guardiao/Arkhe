"""
session_label.py — Geração, sanitização e formatação de labels de sessão.

Porta fiel de packages/sessions/src/session-label.ts para Python.

Fornece:
- ``generate_label(channel_type, created_at, turn_count)``
      → ``"telegram-session-2025-04-04-t3"``
- ``sanitize_label(input)``
      → lowercase, não-alfanumérico→hífen, hifens consecutivos colapsados,
        leading/trailing hifens removidos, máx 64 chars
- ``format_session_summary(meta)``
      → linha única para logging, mesma estrutura do TS
"""
from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Label generation
# ---------------------------------------------------------------------------

def generate_label(
    channel_type: str,
    created_at: str,
    turn_count: int,
) -> str:
    """
    Gera um label legível para a sessão.

    Formato: ``"{channel_type}-session-{YYYY-MM-DD}-t{turn_count}"``

    Exemplo: ``"telegram-session-2025-04-04-t3"``

    Porta de ``generateLabel()`` em session-label.ts.
    """
    date_part = created_at[:10]  # "YYYY-MM-DD"
    return f"{channel_type}-session-{date_part}-t{turn_count}"


# ---------------------------------------------------------------------------
# Label sanitization
# ---------------------------------------------------------------------------

def sanitize_label(input: str) -> str:  # noqa: A002
    """
    Sanitiza um label fornecido pelo usuário.

    Regras (idênticas ao TS):
    - Lowercase
    - Caracteres não-alfanuméricos substituídos por hífen
    - Hifens consecutivos colapsados em um
    - Hifens no início e no fim removidos
    - Resultado limitado a 64 caracteres

    Porta de ``sanitizeLabel()`` em session-label.ts.
    """
    result = input.lower()
    result = re.sub(r"[^a-z0-9\-]", "-", result)
    result = re.sub(r"-+", "-", result)
    result = result.strip("-")
    return result[:64]


# ---------------------------------------------------------------------------
# Summary formatting
# ---------------------------------------------------------------------------

def format_session_summary(meta: Any) -> str:
    """
    Produz uma linha única de resumo da sessão para log.

    Formato::

        [active] abc123def456... | ch=telegram/123456 user=main | turns=5 tokens=1200 | label="minha-sessao"

    Aceita qualquer objeto com os atributos de ``SessionMetadata`` (ou
    ``SessionRecord`` ampliado) ou um dict.

    Porta de ``formatSessionSummary()`` em session-label.ts.
    """
    # Suporte a dict e a objetos (dataclass / TypedDict)
    def _get(obj: Any, *keys: str, default: Any = None) -> Any:
        for key in keys:
            try:
                if isinstance(obj, dict):
                    val = obj.get(key)
                else:
                    val = getattr(obj, key, None)
                if val is not None:
                    return val
            except Exception:
                pass
        return default

    # Extrai campos — tolera nomes ligeiramente diferentes entre TS e Python
    session_id = _get(meta, "id", "session_id", default="?")
    state = _get(meta, "state", "status", default="?")
    label = _get(meta, "label", default=None)
    turn_count = _get(meta, "turn_count", "total_completions", default=0)
    token_count = _get(meta, "token_count", "total_tokens_used", default=0)

    # Extrai campos do SessionKey (pode ser DataClass aninhado ou flat)
    key = _get(meta, "key", default=None)
    if key is not None:
        channel_type = _get(key, "channel_type", default="?")
        channel_id = _get(key, "channel_id", default="?")
        user_id = _get(key, "user_id", default="?")
    else:
        # Fallback para SessionRecord do Python (campos flat)
        client_id: str = _get(meta, "client_id", default="?")
        # client_id pode ser "telegram:123" → channel_type="telegram", channel_id="123"
        if ":" in client_id:
            channel_type, channel_id = client_id.split(":", 1)
        else:
            channel_type, channel_id = "?", client_id
        user_id = _get(meta, "user_id", default="main")

    label_part = f' | label="{label}"' if label is not None else ""
    return (
        f"[{state}] {session_id} | "
        f"ch={channel_type}/{channel_id} user={user_id} | "
        f"turns={turn_count} tokens={token_count}"
        f"{label_part}"
    )
