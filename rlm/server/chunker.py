"""
RLM Chunker — Divisão inteligente de texto para respostas longas.

Problema real:
    Telegram limita mensagens a 4096 chars. WhatsApp a ~4096.
    Atualmente o Telegram gateway simplesmente trunca em 4000 chars
    com "[... resposta truncada ...]". Informação é perdida.

Solução:
    smart_chunk() divide texto respeitando limites de markdown:
    - Prioridade: headers > parágrafos > linhas > palavras
    - Nunca corta no meio de bloco de código
    - Cada chunk cabe no limite do canal
    - Retorna lista de chunks prontos para envio sequencial

Design RLM-nativo:
    - Função pura (sem estado) — fácil de testar
    - Sem dependências externas
    - Limites pré-definidos por canal

Uso:
    from rlm.server.chunker import smart_chunk, TELEGRAM_LIMIT

    chunks = smart_chunk(long_text, max_chars=TELEGRAM_LIMIT)
    for chunk in chunks:
        send_message(chat_id, chunk)
"""
from __future__ import annotations

import re

# Limites por canal (chars, com margem de segurança)
TELEGRAM_LIMIT = 4000
WHATSAPP_LIMIT = 4000
DISCORD_LIMIT = 1900   # Discord embed ~2000, texto ~4000
SLACK_LIMIT = 3900     # Slack blocks ~3000, text ~4000


def smart_chunk(text: str, max_chars: int = TELEGRAM_LIMIT) -> list[str]:
    """
    Divide texto longo em chunks que respeitam limites de markdown.

    Estratégia (em ordem de preferência):
    1. Divide em headers markdown (# / ## / ###)
    2. Divide em parágrafos (\\n\\n)
    3. Divide em linhas (\\n)
    4. Último recurso: corte por palavras

    Nunca corta no meio de um bloco de código (``` ... ```).

    Retorna lista com pelo menos 1 elemento.
    """
    if not text or max_chars <= 0:
        return [text] if text else [""]

    if len(text) <= max_chars:
        return [text]

    # Proteger blocos de código — substituir por placeholders
    code_blocks: list[str] = []
    PLACEHOLDER = "\x00CODE_BLOCK_{}\x00"

    def _protect_code(match: re.Match) -> str:
        idx = len(code_blocks)
        code_blocks.append(match.group(0))
        return PLACEHOLDER.format(idx)

    protected = re.sub(r"```[\s\S]*?```", _protect_code, text)

    # Tentar dividir por headers
    chunks = _split_by_pattern(protected, r"\n(?=#{1,3}\s)", max_chars)
    if chunks is None:
        # Tentar por parágrafos
        chunks = _split_by_pattern(protected, r"\n\n+", max_chars)
    if chunks is None:
        # Tentar por linhas
        chunks = _split_by_pattern(protected, r"\n", max_chars)
    if chunks is None:
        # Último recurso: corte por palavras
        chunks = _split_by_words(protected, max_chars)

    # Restaurar blocos de código
    result: list[str] = []
    for chunk in chunks:
        for idx, block in enumerate(code_blocks):
            chunk = chunk.replace(PLACEHOLDER.format(idx), block)
        chunk = chunk.strip()
        if chunk:
            result.append(chunk)

    # Verificação final: se algum chunk ainda estourou (bloco de código gigante),
    # fazer hard split
    final: list[str] = []
    for chunk in result:
        if len(chunk) <= max_chars:
            final.append(chunk)
        else:
            # Hard split sem cortar no meio de palavra quando possível
            final.extend(_split_by_words(chunk, max_chars))

    return final if final else [text[:max_chars]]


def _split_by_pattern(
    text: str,
    pattern: str,
    max_chars: int,
) -> list[str] | None:
    """
    Tenta dividir texto por pattern regex, agrupando segments em chunks ≤ max_chars.
    Retorna None se algum segmento individual > max_chars (precisa fallback).
    """
    parts = re.split(pattern, text)
    if len(parts) <= 1:
        return None

    chunks: list[str] = []
    current = ""

    for part in parts:
        # Se o segmento sozinho já estourou, precisa de fallback
        if len(part) > max_chars:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            return None  # sinaliza que este nível de split não basta

        if len(current) + len(part) > max_chars:
            if current.strip():
                chunks.append(current.strip())
            current = part
        else:
            current += part

    if current.strip():
        chunks.append(current.strip())

    return chunks if chunks else None


def _split_by_words(text: str, max_chars: int) -> list[str]:
    """
    Divide texto por palavras, respeitando max_chars.
    Fallback final — sempre funciona.
    """
    words = text.split()
    chunks: list[str] = []
    current = ""

    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            # Se a palavra sozinha é maior que max_chars, hard split
            if len(word) > max_chars:
                while word:
                    chunks.append(word[:max_chars])
                    word = word[max_chars:]
                current = ""
            else:
                current = word

    if current:
        chunks.append(current)

    return chunks if chunks else [text[:max_chars]]
