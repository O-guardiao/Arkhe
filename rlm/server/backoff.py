"""
RLM Backoff — Infraestrutura de retry com exponential backoff e jitter.

Adaptado para o RLM: funciona em contextos sync (Telegram polling thread)
e async (FastAPI gateways). Integra com CancellationToken para interrupção
cooperativa — evita que um gateway em backoff ignore um shutdown.

Padrão de referência:
    OpenClaw  — computeBackoff(initial × factor^attempt × jitter)
    VS Code   — sequência escalonada [0,5,5,10,10,10,10,10,30]s

O RLM usa a fórmula contínua (OpenClaw) por ser mais previsível,
mas com CancellationToken (VS Code) para interrupção limpa.

Uso:
    from rlm.server.backoff import BackoffPolicy, GATEWAY_RECONNECT, compute_backoff
    from rlm.server.backoff import sleep_sync, sleep_async

    policy = GATEWAY_RECONNECT
    for attempt in range(1, policy.max_attempts + 1):
        try:
            connect()
            break
        except ConnectionError:
            delay = compute_backoff(policy, attempt)
            sleep_sync(delay, cancel_token=token)
"""
from __future__ import annotations

import asyncio
import random
import threading
from dataclasses import dataclass

from rlm.core.cancellation import CancellationToken


@dataclass(frozen=True)
class BackoffPolicy:
    """
    Política de backoff imutável.

    Fórmula: min(max_s, initial_s × factor^(attempt-1) + jitter)
    Jitter = base × jitter_fraction × random.random()

    Frozen para evitar mutação acidental entre gateways.
    """
    initial_s: float = 5.0
    max_s: float = 300.0       # 5 minutos — teto
    factor: float = 2.0
    jitter_fraction: float = 0.1   # ±10% — evita thundering herd
    max_attempts: int = 10


# ---------------------------------------------------------------------------
# Policies pré-definidas — coerentes com as constantes do RLM e referências
# ---------------------------------------------------------------------------

# Reconexão de gateways (Telegram polling, WS server restart)
# Seq. approx: 5s, 10s, 20s, 40s, 80s, 160s, 300s, 300s, 300s, 300s
GATEWAY_RECONNECT = BackoffPolicy(
    initial_s=5.0, max_s=300.0, factor=2.0, jitter_fraction=0.1, max_attempts=10,
)

# Retry de chamadas HTTP internas (dispatch WhatsApp→RLM, Slack→RLM)
# Seq. approx: 1s, 2s, 4s, 8s, 16s
HTTP_RETRY = BackoffPolicy(
    initial_s=1.0, max_s=30.0, factor=2.0, jitter_fraction=0.2, max_attempts=5,
)

# Health check — cresce devagar, não precisa de muita variação
HEALTH_CHECK = BackoffPolicy(
    initial_s=10.0, max_s=60.0, factor=1.5, jitter_fraction=0.05, max_attempts=3,
)


# ---------------------------------------------------------------------------
# Cálculo
# ---------------------------------------------------------------------------

def compute_backoff(policy: BackoffPolicy, attempt: int) -> float:
    """
    Calcula o delay em segundos para a tentativa dada.

    attempt=1 → initial_s (sem fator)
    attempt=2 → initial_s × factor + jitter
    attempt=N → min(max_s, initial_s × factor^(N-1) + jitter)

    O jitter é sempre positivo (adiciona delay, nunca subtrai) para evitar
    que uma instância tente reconectar ANTES do previsto.
    """
    if attempt <= 0:
        return 0.0
    base = policy.initial_s * (policy.factor ** max(attempt - 1, 0))
    jitter = base * policy.jitter_fraction * random.random()
    return min(policy.max_s, base + jitter)


# ---------------------------------------------------------------------------
# Sleep — sync e async, com cancelamento via CancellationToken
# ---------------------------------------------------------------------------

def sleep_sync(
    seconds: float,
    cancel_token: CancellationToken | None = None,
    poll_interval: float = 0.25,
) -> bool:
    """
    Dorme por `seconds` com cancelamento cooperativo.

    Usa um Event interno que é sinalizado pelo CancellationToken.
    Poll interval de 0.25s mantém responsividade sem busy-wait.

    Retorna:
        True  — dormiu o tempo completo
        False — cancelado antes do tempo expirar
    """
    if seconds <= 0:
        return True
    if cancel_token is not None and cancel_token.is_cancelled:
        return False

    event = threading.Event()

    # Se houver token, registrar callback que acorda a thread
    unsub = None
    if cancel_token is not None:
        unsub = cancel_token.on_cancelled(event.set)

    try:
        # Event.wait com timeout já é eficiente (não faz busy-wait)
        was_set = event.wait(timeout=seconds)
        return not was_set  # True = timeout expirou (dormiu tudo), False = cancelado
    finally:
        if unsub is not None:
            unsub.dispose()


async def sleep_async(
    seconds: float,
    cancel_token: CancellationToken | None = None,
) -> bool:
    """
    Versão async do sleep com cancelamento via CancellationToken.

    Usa asyncio.Event para integração limpa com o event loop do FastAPI.

    Retorna:
        True  — dormiu o tempo completo
        False — cancelado antes do tempo expirar
    """
    if seconds <= 0:
        return True
    if cancel_token is not None and cancel_token.is_cancelled:
        return False

    loop = asyncio.get_running_loop()
    done = asyncio.Event()

    unsub = None
    if cancel_token is not None:
        # CancellationToken.on_cancelled pode ser chamado de outra thread,
        # então usamos call_soon_threadsafe para sinalizar o asyncio.Event
        def _on_cancel():
            loop.call_soon_threadsafe(done.set)

        unsub = cancel_token.on_cancelled(_on_cancel)

    try:
        await asyncio.wait_for(done.wait(), timeout=seconds)
        return False  # evento sinalizou = cancelado
    except asyncio.TimeoutError:
        return True  # timeout = dormiu completo
    finally:
        if unsub is not None:
            unsub.dispose()
