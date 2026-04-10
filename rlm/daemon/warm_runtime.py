from __future__ import annotations

import threading
import time
from typing import Any


class WarmRuntimePool:
    """Invoca ensure_warm_runtime e delega métricas ao rlm_core existente.

    Métricas detalhadas (warm_since_ts, turn_count, cold/warm hits) já são
    rastreadas pelo próprio ensure_warm_runtime em RLMContextMixin.  Esta
    classe mantém apenas contadores de requisições para o snapshot do daemon.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stats: dict[str, int] = {
            "requests": 0,
            "warmed": 0,
            "already_warm": 0,
            "failed": 0,
        }
        self._last_rlm_core: Any | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result: dict[str, Any] = dict(self._stats)
            # Delegate detailed metrics to rlm_core when available
            core = self._last_rlm_core
            if core is not None:
                result["warm_since_ts"] = getattr(core, "_warm_since_ts", None)
                result["last_warm_ts"] = getattr(core, "_last_warm_access_ts", None)
                result["turn_count"] = int(getattr(core, "_warm_turn_count", 0) or 0)
                since = result.get("warm_since_ts")
                result["warm_uptime_s"] = round(time.time() - since, 2) if since else 0.0
            else:
                result["warm_since_ts"] = None
                result["last_warm_ts"] = None
                result["turn_count"] = 0
                result["warm_uptime_s"] = 0.0
            return result

    def warm_session(self, session: Any) -> bool:
        with self._lock:
            self._stats["requests"] += 1

        rlm_session = getattr(session, "rlm_instance", None)
        rlm_core = getattr(rlm_session, "_rlm", None)
        if rlm_core is None:
            with self._lock:
                self._stats["failed"] += 1
            return False

        ensure_warm_runtime = getattr(rlm_core, "ensure_warm_runtime", None)
        if not callable(ensure_warm_runtime):
            with self._lock:
                self._stats["failed"] += 1
            return False

        already_warm = (
            getattr(rlm_core, "_persistent_env", None) is not None
            and getattr(rlm_core, "_persistent_lm_handler", None) is not None
        )

        ensure_warm_runtime()

        with self._lock:
            self._last_rlm_core = rlm_core
            if already_warm:
                self._stats["already_warm"] += 1
            else:
                self._stats["warmed"] += 1
        return True


__all__ = ["WarmRuntimePool"]