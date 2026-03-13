"""
RLM Cron Scheduler — Fase 8.4

Inspirado em: OpenClaw cron/schedule.ts + cron/service.ts

O RLM ganha "iniciativa": pode executar tarefas agendadas automaticamente,
sem necessidade de interação humana.

Suporta 3 tipos de agendamento:
- Cron expression: "*/5 * * * *" (a cada 5 minutos)
- Intervalo fixo: "every:30m", "every:1h", "every:5s"
- Execução única: "at:2026-03-10T15:00:00"
"""
import re
import time
import threading
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Callable, Any


# ---------------------------------------------------------------------------
# Cron Job
# ---------------------------------------------------------------------------

@dataclass
class CronJob:
    """Define um job agendado."""
    name: str                          # Identificador único
    schedule: str                      # Cron expr ou "every:30m" ou "at:ISO"
    prompt: str                        # Prompt a executar no RLM
    client_id: str = "cron:scheduler"  # Sessão onde executa
    enabled: bool = True
    last_run: float = 0.0              # Unix timestamp da última execução
    run_count: int = 0
    last_error: str = ""
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Schedule Parser
# ---------------------------------------------------------------------------

def parse_interval_seconds(schedule: str) -> float | None:
    """
    Parse an interval string into seconds.
    
    Supports: "every:30s", "every:5m", "every:1h", "every:2d"
    Returns None if not an interval format.
    """
    match = re.match(r'^every:(\d+)(s|m|h|d)$', schedule, re.IGNORECASE)
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2).lower()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers.get(unit, 1)


def parse_at_timestamp(schedule: str) -> float | None:
    """
    Parse an "at:" timestamp into unix seconds.
    
    Supports: "at:2026-03-10T15:00:00"
    Returns None if not an at: format.
    """
    if not schedule.startswith("at:"):
        return None
    try:
        dt = datetime.fromisoformat(schedule[3:])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def compute_next_run(job: CronJob, now: float) -> float | None:
    """
    Compute the next run time for a job.
    
    Returns unix timestamp of next run, or None if job should not run again.
    """
    schedule = job.schedule

    # Interval: "every:Ns/m/h/d"
    interval = parse_interval_seconds(schedule)
    if interval is not None:
        if job.last_run <= 0:
            return now  # Run immediately on first schedule
        next_run = job.last_run + interval
        return next_run if next_run > now else now

    # One-shot: "at:ISO-timestamp"
    at_time = parse_at_timestamp(schedule)
    if at_time is not None:
        return at_time if at_time > now else None

    # Cron expression: use simple minute/hour matching
    # (Full cron parsing would require a library; this handles common cases)
    return _simple_cron_next(schedule, now)


def _simple_cron_next(expr: str, now: float) -> float | None:
    """
    Simple cron expression matcher for common patterns.
    
    Supports:
    - "* * * * *"      → every minute
    - "*/5 * * * *"    → every 5 minutes
    - "0 * * * *"      → every hour
    - "0 0 * * *"      → every day at midnight
    - "30 8 * * *"     → every day at 08:30
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        return None

    minute_spec, hour_spec = parts[0], parts[1]
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)

    # Parse minute
    if minute_spec == "*":
        target_minute = now_dt.minute + 1
        if target_minute >= 60:
            target_minute = 0
            target_hour = now_dt.hour + 1
        else:
            target_hour = now_dt.hour
    elif minute_spec.startswith("*/"):
        interval = int(minute_spec[2:])
        current = now_dt.minute
        target_minute = ((current // interval) + 1) * interval
        if target_minute >= 60:
            target_minute = 0
            target_hour = now_dt.hour + 1
        else:
            target_hour = now_dt.hour
    else:
        try:
            target_minute = int(minute_spec)
        except ValueError:
            return None
        target_hour = now_dt.hour

    # Parse hour
    if hour_spec != "*":
        if hour_spec.startswith("*/"):
            interval = int(hour_spec[2:])
            target_hour = ((now_dt.hour // interval) + 1) * interval
        else:
            try:
                target_hour = int(hour_spec)
            except ValueError:
                return None

    # Build next run datetime
    if target_hour >= 24:
        target_hour = target_hour % 24
        next_dt = now_dt.replace(
            hour=target_hour, minute=target_minute, second=0, microsecond=0
        ) + timedelta(days=1)
    else:
        next_dt = now_dt.replace(
            hour=target_hour, minute=target_minute, second=0, microsecond=0
        )

    if next_dt.timestamp() <= now:
        next_dt += timedelta(hours=1) if minute_spec == "*" else timedelta(days=1)

    return next_dt.timestamp()


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class RLMScheduler:
    """
    Scheduler that executes RLM tasks on a schedule.
    
    Usage:
        scheduler = RLMScheduler(execute_fn=my_execute)
        scheduler.add_job(CronJob(
            name="healthcheck",
            schedule="every:5m",
            prompt="Run a health check on all systems.",
        ))
        scheduler.start()
        # ...
        scheduler.stop()
    """

    def __init__(
        self,
        execute_fn: Callable[[str, str], Any] | None = None,
        poll_interval: float = 10.0,
    ):
        """
        Args:
            execute_fn: Function(client_id, prompt) -> result. 
                        Called when a job fires. Typically wired to
                        supervisor.execute() or session_manager.get_or_create() chain.
            poll_interval: How often to check for due jobs (seconds).
        """
        self._execute_fn = execute_fn
        self._poll_interval = poll_interval
        self._jobs: dict[str, CronJob] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def add_job(self, job: CronJob) -> None:
        """Add or update a scheduled job."""
        with self._lock:
            self._jobs[job.name] = job

    def remove_job(self, name: str) -> bool:
        """Remove a scheduled job. Returns True if found."""
        with self._lock:
            return self._jobs.pop(name, None) is not None

    def get_job(self, name: str) -> CronJob | None:
        """Get a job by name."""
        return self._jobs.get(name)

    def list_jobs(self) -> list[CronJob]:
        """List all registered jobs."""
        with self._lock:
            return list(self._jobs.values())

    def start(self) -> None:
        """Start the scheduler background thread."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="rlm-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._poll_interval + 2)
            self._thread = None

    def is_running(self) -> bool:
        return self._running

    # --- Internal ---

    def _run_loop(self) -> None:
        """Main scheduler loop."""
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                pass  # Scheduler must never crash
            self._stop_event.wait(self._poll_interval)

    def _tick(self) -> None:
        """Check all jobs and execute any that are due."""
        now = time.time()
        with self._lock:
            jobs_snapshot = list(self._jobs.values())

        for job in jobs_snapshot:
            if not job.enabled:
                continue

            next_run = compute_next_run(job, now)
            if next_run is None:
                continue

            if next_run <= now:
                self._fire_job(job, now)

    def _fire_job(self, job: CronJob, now: float) -> None:
        """Execute a due job."""
        job.last_run = now
        job.run_count += 1

        if self._execute_fn is None:
            return

        try:
            self._execute_fn(job.client_id, job.prompt)
            job.last_error = ""
        except Exception as e:
            job.last_error = str(e)[:200]

    def job_to_dict(self, job: CronJob) -> dict:
        """Convert job to JSON-safe dict for API responses."""
        now = time.time()
        return {
            "name": job.name,
            "schedule": job.schedule,
            "prompt": job.prompt[:100] + ("..." if len(job.prompt) > 100 else ""),
            "client_id": job.client_id,
            "enabled": job.enabled,
            "run_count": job.run_count,
            "last_error": job.last_error,
            "next_run": compute_next_run(job, now),
        }
