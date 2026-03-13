"""
RLM Proactive Scheduler — Phase 10.1

Transforma o RLM de chatbot reativo em agente autônomo com tarefas agendadas.

Funcionamento
-------------
- Roda como daemon: `python -m rlm.server.scheduler`
- Persiste tarefas em SQLite (~/.rlm/scheduler.db)
- Suporta disparos cron + one-shot + intervalo + condição Python
- Cada tarefa chama rlm.completion(task_prompt) num thread separado
- Notifica via Telegram quando a tarefa termina (sucesso ou falha)
- Máximo de N tarefas concorrentes (default 4)

CLI de uso rápido
-----------------
  # agendar
  python -m rlm.server.scheduler add \\
      --task "Verifica meu email e resume os últimos 3 assuntos" \\
      --cron "0 9 * * *"   # todo dia às 9h

  # listar
  python -m rlm.server.scheduler list

  # cancelar
  python -m rlm.server.scheduler cancel <task_id>

  # iniciar daemon
  python -m rlm.server.scheduler run

Variáveis de ambiente relevantes
---------------------------------
  RLM_BACKEND            backend padrão (default: openai)
  RLM_MODEL              model name
  RLM_SCHEDULER_WORKERS  max tarefas paralelas (default: 4)
  RLM_SCHEDULER_DB       caminho do banco (default: ~/.rlm/scheduler.db)
  TELEGRAM_BOT_TOKEN     notificações Telegram
  TELEGRAM_CHAT_ID       chat destino das notificações
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, cast

from rlm.logging import get_runtime_logger
from rlm.core.types import ClientBackend

log = get_runtime_logger("scheduler")

# ──────────────────────────────────────────────────────────────────────────────
# Cron parser — stdlib only (sem croniter)
# ──────────────────────────────────────────────────────────────────────────────

def _cron_matches(cron_expr: str, dt: datetime) -> bool:
    """
    Verifica se `dt` corresponde a `cron_expr` (5 campos: min hour dom mon dow).
    Suporta: * números fixos listas (1,2,3) e passos (*/5).
    """
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    values = [dt.minute, dt.hour, dt.day, dt.month, dt.weekday()]

    def _match(field_str: str, val: int, lo: int, hi: int) -> bool:
        if field_str == "*":
            return True
        if field_str.startswith("*/"):
            step = int(field_str[2:])
            return val % step == 0
        if "," in field_str:
            return val in [int(x) for x in field_str.split(",")]
        if "-" in field_str:
            a, b = field_str.split("-")
            return int(a) <= val <= int(b)
        return val == int(field_str)

    return all(_match(f, v, 0, 59) for f, v in zip(
        [minute, hour, dom, month, dow], values
    ))

# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ScheduledTask:
    task_id: str
    prompt: str
    trigger_type: str          # "cron" | "once" | "interval" | "condition"
    trigger_value: str         # cron expr | ISO datetime | seconds | Python expr
    enabled: bool = True
    last_run_ts: float = 0.0
    next_run_ts: float = 0.0
    run_count: int = 0
    last_status: str = "pending"   # pending | running | success | error
    last_result: str = ""
    last_error: str = ""
    backend: str = ""              # "" = usa default do daemon
    model: str = ""
    max_iterations: int = 20
    notify_telegram: bool = True
    created_ts: float = field(default_factory=lambda: time.time())
    tags: str = ""                 # JSON list of tags


@dataclass
class TaskResult:
    task_id: str
    success: bool
    result: str
    error: str
    duration_s: float
    ts: float = field(default_factory=lambda: time.time())


# ──────────────────────────────────────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    task_id        TEXT PRIMARY KEY,
    prompt         TEXT NOT NULL,
    trigger_type   TEXT NOT NULL,
    trigger_value  TEXT NOT NULL,
    enabled        INTEGER DEFAULT 1,
    last_run_ts    REAL DEFAULT 0,
    next_run_ts    REAL DEFAULT 0,
    run_count      INTEGER DEFAULT 0,
    last_status    TEXT DEFAULT 'pending',
    last_result    TEXT DEFAULT '',
    last_error     TEXT DEFAULT '',
    backend        TEXT DEFAULT '',
    model          TEXT DEFAULT '',
    max_iterations INTEGER DEFAULT 20,
    notify_telegram INTEGER DEFAULT 1,
    created_ts     REAL DEFAULT 0,
    tags           TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS task_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    ts         REAL NOT NULL,
    success    INTEGER NOT NULL,
    result     TEXT DEFAULT '',
    error      TEXT DEFAULT '',
    duration_s REAL DEFAULT 0
);
"""


class TaskStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._conn()
            conn.executescript(_SCHEMA)
            conn.commit()
            conn.close()

    def add_task(self, task: ScheduledTask):
        task.next_run_ts = self._compute_next(task, time.time())
        with self._lock:
            conn = self._conn()
            d = asdict(task)
            conn.execute("""
                INSERT OR REPLACE INTO scheduled_tasks
                (task_id, prompt, trigger_type, trigger_value, enabled,
                 last_run_ts, next_run_ts, run_count, last_status, last_result,
                 last_error, backend, model, max_iterations, notify_telegram,
                 created_ts, tags)
                VALUES
                (:task_id, :prompt, :trigger_type, :trigger_value, :enabled,
                 :last_run_ts, :next_run_ts, :run_count, :last_status, :last_result,
                 :last_error, :backend, :model, :max_iterations, :notify_telegram,
                 :created_ts, :tags)
            """, d)
            conn.commit()
            conn.close()
        log.info(
            "Task added",
            task_id=task.task_id[:8],
            trigger_type=task.trigger_type,
            trigger_value=task.trigger_value,
        )

    def get_all(self, enabled_only: bool = False) -> list[ScheduledTask]:
        with self._lock:
            conn = self._conn()
            q = "SELECT * FROM scheduled_tasks"
            if enabled_only:
                q += " WHERE enabled=1"
            rows = conn.execute(q).fetchall()
            conn.close()
        return [self._row_to_task(r) for r in rows]

    def get_due(self, now_ts: float) -> list[ScheduledTask]:
        with self._lock:
            conn = self._conn()
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE enabled=1 AND next_run_ts <= ? AND last_status != 'running'",
                (now_ts,)
            ).fetchall()
            conn.close()
        return [self._row_to_task(r) for r in rows]

    def mark_running(self, task_id: str):
        with self._lock:
            conn = self._conn()
            conn.execute(
                "UPDATE scheduled_tasks SET last_status='running', last_run_ts=? WHERE task_id=?",
                (time.time(), task_id)
            )
            conn.commit()
            conn.close()

    def record_result(self, result: TaskResult, next_run_ts: float):
        with self._lock:
            conn = self._conn()
            status = "success" if result.success else "error"
            conn.execute("""
                UPDATE scheduled_tasks SET
                    last_status=?, last_result=?, last_error=?,
                    run_count=run_count+1, next_run_ts=?
                WHERE task_id=?
            """, (status, result.result[:2000], result.error[:1000], next_run_ts, result.task_id))
            conn.execute("""
                INSERT INTO task_history (task_id, ts, success, result, error, duration_s)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (result.task_id, result.ts, int(result.success), result.result[:2000],
                  result.error[:1000], result.duration_s))
            conn.commit()
            conn.close()

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            conn = self._conn()
            c = conn.execute(
                "UPDATE scheduled_tasks SET enabled=0 WHERE task_id=?", (task_id,)
            )
            conn.commit()
            conn.close()
        return c.rowcount > 0

    def delete(self, task_id: str) -> bool:
        with self._lock:
            conn = self._conn()
            c = conn.execute("DELETE FROM scheduled_tasks WHERE task_id=?", (task_id,))
            conn.commit()
            conn.close()
        return c.rowcount > 0

    def get_history(self, task_id: str, limit: int = 10) -> list[dict]:
        with self._lock:
            conn = self._conn()
            rows = conn.execute(
                "SELECT * FROM task_history WHERE task_id=? ORDER BY ts DESC LIMIT ?",
                (task_id, limit)
            ).fetchall()
            conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> ScheduledTask:
        return ScheduledTask(
            task_id=row["task_id"],
            prompt=row["prompt"],
            trigger_type=row["trigger_type"],
            trigger_value=row["trigger_value"],
            enabled=bool(row["enabled"]),
            last_run_ts=row["last_run_ts"],
            next_run_ts=row["next_run_ts"],
            run_count=row["run_count"],
            last_status=row["last_status"],
            last_result=row["last_result"],
            last_error=row["last_error"],
            backend=row["backend"],
            model=row["model"],
            max_iterations=row["max_iterations"],
            notify_telegram=bool(row["notify_telegram"]),
            created_ts=row["created_ts"],
            tags=row["tags"],
        )

    @staticmethod
    def _compute_next(task: ScheduledTask, after_ts: float) -> float:
        """Calculates next run timestamp from a trigger definition."""
        t = task.trigger_type
        v = task.trigger_value

        if t == "once":
            try:
                dt = datetime.fromisoformat(v)
                return dt.timestamp()
            except ValueError:
                return 0.0

        if t == "interval":
            try:
                interval_s = float(v)
                return after_ts + interval_s
            except ValueError:
                return 0.0

        if t == "cron":
            # Find next minute that matches cron
            dt = datetime.fromtimestamp(after_ts, tz=timezone.utc).replace(second=0, microsecond=0)
            dt += timedelta(minutes=1)  # start from next minute
            for _ in range(60 * 24 * 7):  # scan up to 1 week ahead
                if _cron_matches(v, dt):
                    return dt.timestamp()
                dt += timedelta(minutes=1)
            return 0.0

        if t == "condition":
            # Condition tasks get re-checked every 60s
            return after_ts + 60.0

        return 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Telegram notifier (lightweight, no library)
# ──────────────────────────────────────────────────────────────────────────────

def _telegram_notify(text: str) -> bool:
    """Send a message to Telegram. Returns True on success."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    try:
        import urllib.request, urllib.parse
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:4096]}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        log.warn("Telegram notify failed", error=str(e))
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Task runner
# ──────────────────────────────────────────────────────────────────────────────

def _run_task(task: ScheduledTask, default_backend: str, default_model: str) -> TaskResult:
    """Executa uma tarefa agendada via rlm.completion()."""
    from rlm.core.rlm import RLM

    backend = cast(ClientBackend, task.backend or default_backend)
    model = task.model or default_model
    backend_kwargs = {"model_name": model} if model else None

    t0 = time.perf_counter()
    try:
        rlm_instance = RLM(
            backend=backend,
            backend_kwargs=backend_kwargs,
            max_iterations=task.max_iterations,
        )
        result = rlm_instance.completion(task.prompt)
        response = result.response if hasattr(result, "response") else str(result)
        return TaskResult(
            task_id=task.task_id,
            success=True,
            result=response,
            error="",
            duration_s=time.perf_counter() - t0,
        )
    except Exception:
        tb = traceback.format_exc()
        return TaskResult(
            task_id=task.task_id,
            success=False,
            result="",
            error=tb,
            duration_s=time.perf_counter() - t0,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Scheduler daemon
# ──────────────────────────────────────────────────────────────────────────────

class RLMScheduler:
    """
    Daemon que verifica o banco de tarefas a cada 30s e dispara as devidas.

    Uso:
        scheduler = RLMScheduler()
        scheduler.run()          # bloqueia até SIGINT/SIGTERM

    Ou em thread separada:
        t = threading.Thread(target=scheduler.run, daemon=True)
        t.start()
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        backend: str | None = None,
        model: str | None = None,
        max_workers: int | None = None,
        poll_interval_s: float = 30.0,
    ):
        _home = Path.home() / ".rlm"
        self.db_path = Path(db_path or os.environ.get("RLM_SCHEDULER_DB", str(_home / "scheduler.db")))
        self.backend = backend or os.environ.get("RLM_BACKEND", "openai")
        self.model = model or os.environ.get("RLM_MODEL", "")
        self.max_workers = max_workers or int(os.environ.get("RLM_SCHEDULER_WORKERS", "4"))
        self.poll_interval_s = poll_interval_s
        self.store = TaskStore(self.db_path)
        self._running = False
        self._active: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

        log.info(
            "Scheduler initialized",
            db_path=str(self.db_path),
            backend=self.backend,
            workers=self.max_workers,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def tasks(self) -> TaskStore:
        return self.store

    def add(
        self,
        prompt: str,
        *,
        cron: str | None = None,
        once: str | None = None,
        interval_s: float | None = None,
        condition: str | None = None,
        backend: str = "",
        model: str = "",
        max_iterations: int = 20,
        notify_telegram: bool = True,
        tags: list[str] | None = None,
    ) -> str:
        """
        Agenda uma nova tarefa. Retorna o task_id.

        Exemplos:
            scheduler.add("Resume meus emails", cron="0 9 * * 1-5")
            scheduler.add("Backup diário", interval_s=86400)
            scheduler.add("Notifica se Bitcoin > 100k",
                          condition="web_get('https://api.binance.com/...').json()['price'] > 100000")
        """
        if sum(x is not None for x in [cron, once, interval_s, condition]) != 1:
            raise ValueError("Especifique exatamente um: cron, once, interval_s ou condition")

        if cron is not None:
            ttype, tval = "cron", cron
        elif once is not None:
            ttype, tval = "once", once
        elif interval_s is not None:
            ttype, tval = "interval", str(interval_s)
        else:
            assert condition is not None
            ttype, tval = "condition", condition

        task_id = str(uuid.uuid4())
        task = ScheduledTask(
            task_id=task_id,
            prompt=prompt,
            trigger_type=ttype,
            trigger_value=tval,
            backend=backend,
            model=model,
            max_iterations=max_iterations,
            notify_telegram=notify_telegram,
            tags=json.dumps(tags or []),
        )
        self.store.add_task(task)
        return task_id

    def cancel(self, task_id: str) -> bool:
        return self.store.cancel(task_id)

    def run_once(self, task_id: str):
        """Força execução imediata de uma tarefa (ignora trigger)."""
        tasks = self.store.get_all()
        for t in tasks:
            if t.task_id == task_id:
                self._dispatch(t)
                return
        raise ValueError(f"Task {task_id} não encontrada")

    def run(self):
        """
        Inicia o loop principal do daemon.
        Bloqueia até SIGINT/SIGTERM ou stop().
        """
        self._running = True
        log.info("Scheduler daemon started", poll_interval_s=self.poll_interval_s)

        def _sighandler(sig, _frame):
            log.info("Signal received, shutting down scheduler", signal=sig)
            self._running = False

        signal.signal(signal.SIGINT, _sighandler)
        signal.signal(signal.SIGTERM, _sighandler)

        _telegram_notify("🤖 RLM Scheduler started")

        while self._running:
            try:
                self._tick()
            except Exception:
                log.error("Scheduler tick failed", traceback=traceback.format_exc())
            time.sleep(self.poll_interval_s)

        # Wait for active tasks
        self._wait_active()
        log.info("Scheduler stopped")
        _telegram_notify("🛑 RLM Scheduler stopped")

    def stop(self):
        self._running = False

    # ── Internal ───────────────────────────────────────────────────────────────

    def _tick(self):
        now = time.time()
        due = self.store.get_due(now)
        if not due:
            return

        log.info("Tasks due", due_count=len(due))
        for task in due:
            with self._lock:
                active_count = sum(1 for t in self._active.values() if t.is_alive())
                if active_count >= self.max_workers:
                    log.warn(
                        "Worker pool full, deferring task",
                        workers=self.max_workers,
                        task_id=task.task_id[:8],
                    )
                    continue

                # Condition check
                if task.trigger_type == "condition":
                    try:
                        import ast
                        result = ast.literal_eval(task.trigger_value)
                        if not result:
                            # reschedule in 60s
                            next_ts = TaskStore._compute_next(task, now)
                            self.store.record_result(
                                TaskResult(task.task_id, True, "condition_not_met", "", 0.0),
                                next_ts
                            )
                            continue
                    except Exception as e:
                        log.warn(
                            "Condition evaluation failed",
                            task_id=task.task_id[:8],
                            error=str(e),
                        )
                        continue

                self._dispatch(task)

    def _dispatch(self, task: ScheduledTask):
        """Marca como running e lança em thread daemon."""
        self.store.mark_running(task.task_id)
        log.info(
            "Dispatching task",
            task_id=task.task_id[:8],
            prompt_preview=task.prompt[:60],
            trigger_type=task.trigger_type,
        )

        def _worker():
            result = _run_task(task, self.backend, self.model)
            next_ts = TaskStore._compute_next(task, time.time())

            # One-shot: disable after running
            if task.trigger_type == "once":
                next_ts = 0.0
                self.store.cancel(task.task_id)

            self.store.record_result(result, next_ts)

            status_emoji = "✅" if result.success else "❌"
            msg = (
                f"{status_emoji} RLM Task {task.task_id[:8]}\n"
                f"Prompt: {task.prompt[:100]}\n"
                f"Duration: {result.duration_s:.1f}s\n"
            )
            if result.success:
                msg += f"Result: {result.result[:300]}"
            else:
                msg += f"Error: {result.error[:300]}"

            log.info(
                "Task finished",
                task_id=task.task_id[:8],
                status="OK" if result.success else "ERROR",
                duration_s=round(result.duration_s, 3),
            )

            if task.notify_telegram:
                _telegram_notify(msg)

        t = threading.Thread(target=_worker, daemon=True, name=f"task-{task.task_id[:8]}")
        self._active[task.task_id] = t
        t.start()

    def _wait_active(self, timeout_s: float = 30.0):
        deadline = time.time() + timeout_s
        for tid, thread in list(self._active.items()):
            remaining = deadline - time.time()
            if remaining > 0:
                thread.join(timeout=remaining)


# ──────────────────────────────────────────────────────────────────────────────
# CLI entrypoint
# ──────────────────────────────────────────────────────────────────────────────

def _cli_run(args):
    """Start scheduler daemon."""
    s = RLMScheduler(
        db_path=args.db,
        backend=args.backend or None,
        model=args.model or None,
        max_workers=args.workers,
        poll_interval_s=args.poll,
    )
    s.run()


def _cli_add(args):
    s = RLMScheduler(db_path=args.db)
    cron = args.cron or None
    once = args.once or None
    interval_s = float(args.interval) if args.interval else None
    condition = args.condition or None
    task_id = s.add(
        prompt=args.task,
        cron=cron, once=once, interval_s=interval_s, condition=condition,
        backend=args.backend or "",
        model=args.model or "",
        max_iterations=args.max_iterations,
        notify_telegram=not args.no_notify,
        tags=args.tags.split(",") if args.tags else [],
    )
    next_run = s.store.get_all()
    for t in next_run:
        if t.task_id == task_id:
            nxt = datetime.fromtimestamp(t.next_run_ts).isoformat() if t.next_run_ts else "never"
            print(f"✅ Task {task_id[:8]} added — next run: {nxt}")
            return
    print(f"✅ Task {task_id} added")


def _cli_list(args):
    s = RLMScheduler(db_path=args.db)
    tasks = s.store.get_all(enabled_only=not args.all)
    if not tasks:
        print("No tasks.")
        return
    fmt = "{:<10} {:<8} {:<22} {:<8} {:<22} {}"
    print(fmt.format("ID", "TYPE", "TRIGGER", "STATUS", "NEXT_RUN", "PROMPT"))
    print("-" * 100)
    for t in tasks:
        nxt = datetime.fromtimestamp(t.next_run_ts).strftime("%Y-%m-%d %H:%M") if t.next_run_ts else "disabled"
        print(fmt.format(
            t.task_id[:8],
            t.trigger_type,
            t.trigger_value[:20],
            t.last_status,
            nxt,
            t.prompt[:50],
        ))


def _cli_cancel(args):
    s = RLMScheduler(db_path=args.db)
    ok = s.cancel(args.task_id)
    print("Cancelled." if ok else "Task not found.")


def _cli_delete(args):
    s = RLMScheduler(db_path=args.db)
    ok = s.store.delete(args.task_id)
    print("Deleted." if ok else "Task not found.")


def _cli_history(args):
    s = RLMScheduler(db_path=args.db)
    rows = s.store.get_history(args.task_id, limit=args.limit)
    for r in rows:
        ts = datetime.fromtimestamp(r["ts"]).isoformat()
        status = "✅" if r["success"] else "❌"
        print(f"{status} {ts} ({r['duration_s']:.1f}s): {(r['result'] or r['error'])[:120]}")


def main():
    parser = argparse.ArgumentParser(description="RLM Proactive Scheduler")
    parser.add_argument("--db", default=str(Path.home() / ".rlm" / "scheduler.db"),
                        help="Path to scheduler SQLite database")
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Start daemon")
    p_run.add_argument("--backend", default="")
    p_run.add_argument("--model", default="")
    p_run.add_argument("--workers", type=int, default=4)
    p_run.add_argument("--poll", type=float, default=30.0, help="Poll interval in seconds")
    p_run.set_defaults(func=_cli_run)

    # add
    p_add = sub.add_parser("add", help="Schedule a new task")
    p_add.add_argument("--task", required=True, help="Prompt to run")
    p_add.add_argument("--cron", help="Cron expression (5 fields)")
    p_add.add_argument("--once", help="ISO datetime for one-shot run")
    p_add.add_argument("--interval", help="Interval in seconds")
    p_add.add_argument("--condition", help="Python expression (truthy = run)")
    p_add.add_argument("--backend", default="")
    p_add.add_argument("--model", default="")
    p_add.add_argument("--max-iterations", type=int, default=20)
    p_add.add_argument("--tags", default="")
    p_add.add_argument("--no-notify", action="store_true")
    p_add.set_defaults(func=_cli_add)

    # list
    p_list = sub.add_parser("list", help="List scheduled tasks")
    p_list.add_argument("--all", action="store_true", help="Include disabled tasks")
    p_list.set_defaults(func=_cli_list)

    # cancel
    p_cancel = sub.add_parser("cancel", help="Disable a task")
    p_cancel.add_argument("task_id")
    p_cancel.set_defaults(func=_cli_cancel)

    # delete
    p_delete = sub.add_parser("delete", help="Delete a task permanently")
    p_delete.add_argument("task_id")
    p_delete.set_defaults(func=_cli_delete)

    # history
    p_hist = sub.add_parser("history", help="Show task run history")
    p_hist.add_argument("task_id")
    p_hist.add_argument("--limit", type=int, default=10)
    p_hist.set_defaults(func=_cli_history)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
