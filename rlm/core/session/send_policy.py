"""
send_policy.py — Política de envio de mensagens por sessão.

Porta fiel de packages/sessions/src/send-policy.ts para Python.

Fornece:
- ``SendPolicy``         — dataclass com as regras de envio
- ``DEFAULT_SEND_POLICY``— política permissiva padrão
- ``check_send_policy(policy, message)`` — avalia se a mensagem pode ser enviada
- ``RateLimiter``        — janela deslizante (sliding window) por minuto
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# SendPolicy
# ---------------------------------------------------------------------------

@dataclass
class SendPolicy:
    """
    Política de envio outbound por sessão.

    Campos:
    - ``allow_outbound``     — quando False, todas as mensagens de saída são bloqueadas
    - ``rate_limit_rpm``     — máximo de chamadas por minuto (enforcement via RateLimiter)
    - ``max_message_length`` — comprimento máximo em caracteres de uma mensagem
    - ``require_ack``        — quando True, remetente deve aguardar ACK explícito
    - ``blocked_patterns``   — padrões regex (case-insensitive); mensagem bloqueada se
                               qualquer padrão corresponder ao texto completo

    Porta de ``SendPolicy`` + ``SendPolicySchema`` em send-policy.ts.
    """
    allow_outbound: bool = True
    rate_limit_rpm: Optional[int] = None
    max_message_length: Optional[int] = None
    require_ack: Optional[bool] = None
    blocked_patterns: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Default policy
# ---------------------------------------------------------------------------

# Permissiva — outbound permitido sem restrições adicionais.
DEFAULT_SEND_POLICY = SendPolicy(allow_outbound=True)


# ---------------------------------------------------------------------------
# Policy checker
# ---------------------------------------------------------------------------

@dataclass
class PolicyCheckResult:
    allowed: bool
    reason: Optional[str] = None


def check_send_policy(policy: SendPolicy, message: str) -> PolicyCheckResult:
    """
    Avalia se *message* pode ser enviada de acordo com a política.

    Verificações (em ordem):
    1. ``allow_outbound``
    2. ``max_message_length``
    3. Cada entrada em ``blocked_patterns`` (regexes inválidos são silenciosamente ignorados)

    Enforcement de rate-limit é intencionalmente excluído aqui; use ``RateLimiter``
    em combinação com ``rate_limit_rpm`` para isso.

    Porta de ``checkSendPolicy()`` em send-policy.ts.
    """
    if not policy.allow_outbound:
        return PolicyCheckResult(allowed=False, reason="outbound not allowed by policy")

    if policy.max_message_length is not None and len(message) > policy.max_message_length:
        return PolicyCheckResult(
            allowed=False,
            reason=(
                f"message length {len(message)} exceeds "
                f"max_message_length {policy.max_message_length}"
            ),
        )

    for pattern in policy.blocked_patterns:
        try:
            if re.search(pattern, message, re.IGNORECASE):
                return PolicyCheckResult(
                    allowed=False,
                    reason=f"message matches blocked pattern: {pattern}",
                )
        except re.error:
            # Regex inválido — ignora em vez de crashar (mesmo comportamento do TS)
            pass

    return PolicyCheckResult(allowed=True)


# ---------------------------------------------------------------------------
# RateLimiter — janela deslizante de 60 segundos
# ---------------------------------------------------------------------------

class RateLimiter:
    """
    Rate limiter de janela deslizante.

    Rastreia os timestamps das últimas N chamadas dentro de uma janela
    de 60 segundos onde N = ``rpm``. Quando a janela está cheia,
    ``check()`` retorna False.

    Porta de ``RateLimiter`` em send-policy.ts.
    """

    def __init__(self, rpm: int) -> None:
        if rpm <= 0:
            raise ValueError(f"rpm must be positive, got {rpm}")
        self._rpm = rpm
        self._timestamps: list[float] = []

    def _evict(self) -> None:
        """Remove timestamps fora da janela de 60 segundos."""
        now = time.time()
        self._timestamps = [t for t in self._timestamps if now - t < 60.0]

    def check(self) -> bool:
        """
        Tenta consumir um token do orçamento de rate-limit.
        Registra a chamada em caso de sucesso.

        Retorna ``True`` se a chamada está dentro da taxa permitida;
        ``False`` caso contrário.

        Porta de ``check()`` em send-policy.ts.
        """
        self._evict()
        if len(self._timestamps) >= self._rpm:
            return False
        self._timestamps.append(time.time())
        return True

    def remaining(self) -> int:
        """Tokens restantes na janela atual (para fins de diagnóstico)."""
        self._evict()
        return max(0, self._rpm - len(self._timestamps))
