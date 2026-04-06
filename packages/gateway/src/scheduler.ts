/**
 * Proactive Scheduler — mensagens e tarefas agendadas (cron + one-shot + interval).
 *
 * Analogia Python: rlm/server/scheduler.py
 *
 * Transforma o Brain de chatbot reativo em agente autônomo com tarefas agendadas.
 *
 * Funcionamento:
 *   - Persiste tarefas em SQLite (~/.rlm/scheduler.db)
 *   - Suporta: cron (5 campos), once (ISO datetime), interval (seconds)
 *   - Cada tarefa gera um Envelope → forwarda ao Brain via registry
 *   - Máximo de N tarefas concorrentes (default: 4)
 *
 * Variáveis de ambiente:
 *   RLM_SCHEDULER_DB       — caminho do banco (default: ~/.rlm/scheduler.db)
 *   RLM_SCHEDULER_WORKERS  — max tarefas paralelas (default: 4)
 *
 * Uso via API (HTTP não exposto diretamente — usar via OperatorBridge ou CLI):
 *   const scheduler = new GatewayScheduler(registry);
 *   await scheduler.start();
 *   await scheduler.addTask({ prompt: "verifica email", triggerType: "cron", triggerValue: "0 9 * * *" });
 */

import { createRequire } from "node:module";
import { homedir } from "node:os";
import { join } from "node:path";
import { childLogger } from "./logger.js";
import { newEnvelope } from "./envelope.js";
import type { ChannelRegistry } from "./registry.js";

const log = childLogger({ component: "scheduler" });

const _require = createRequire(import.meta.url);

// ---------------------------------------------------------------------------
// Cron parser (stdlib-only, sem dependências externas)
// ---------------------------------------------------------------------------

/**
 * Verifica se `date` corresponde a `cronExpr` (5 campos: min hour dom month dow).
 * Suporta: números fixos, listas (1,2,3), passos (*&#47;5), ranges (1-5).
 */
function cronMatches(cronExpr: string, date: Date): boolean {
  const fields = cronExpr.trim().split(/\s+/);
  if (fields.length !== 5) return false;

  const [minute, hour, dom, month, dow] = fields as [string, string, string, string, string];
  const values = [
    date.getMinutes(),
    date.getHours(),
    date.getDate(),
    date.getMonth() + 1, // 1-based
    date.getDay(),       // 0=Sunday
  ];
  const pairsToCheck = [
    [minute, values[0]!] as const,
    [hour, values[1]!] as const,
    [dom, values[2]!] as const,
    [month, values[3]!] as const,
    [dow, values[4]!] as const,
  ];

  return pairsToCheck.every(([field, val]) => matchField(field, val));
}

function matchField(field: string, val: number): boolean {
  if (field === "*") return true;
  if (field.startsWith("*/")) {
    const step = parseInt(field.slice(2), 10);
    return !isNaN(step) && val % step === 0;
  }
  if (field.includes(",")) {
    return field.split(",").map(Number).includes(val);
  }
  if (field.includes("-")) {
    const [a, b] = field.split("-").map(Number) as [number, number];
    return val >= a && val <= b;
  }
  return parseInt(field, 10) === val;
}

// ---------------------------------------------------------------------------
// Tipos
// ---------------------------------------------------------------------------

export type TriggerType = "cron" | "once" | "interval";

export interface ScheduledTask {
  taskId: string;
  prompt: string;
  triggerType: TriggerType;
  /** cron expr | ISO datetime | interval em segundos */
  triggerValue: string;
  enabled: boolean;
  clientId: string;
  lastRunTs: number;
  nextRunTs: number;
  runCount: number;
  lastStatus: "pending" | "running" | "success" | "error";
  lastError: string;
  createdTs: number;
  tags: string[];
}

export interface AddTaskOptions {
  prompt: string;
  triggerType: TriggerType;
  triggerValue: string;
  clientId?: string;
  tags?: string[];
}

// ---------------------------------------------------------------------------
// Database (better-sqlite3 opcional — fallback para in-memory)
// ---------------------------------------------------------------------------

interface DbRow {
  task_id: string;
  prompt: string;
  trigger_type: string;
  trigger_value: string;
  enabled: number;
  client_id: string;
  last_run_ts: number;
  next_run_ts: number;
  run_count: number;
  last_status: string;
  last_error: string;
  created_ts: number;
  tags: string;
}

interface Database {
  exec(sql: string): void;
  prepare(sql: string): { run: (...args: unknown[]) => void; all: () => DbRow[]; get: (...args: unknown[]) => DbRow | undefined };
  close(): void;
}

function openDatabase(dbPath: string): Database | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
    const BetterSqlite = _require("better-sqlite3") as (path: string) => Database;
    return BetterSqlite(dbPath);
  } catch {
    log.warn({ dbPath }, "better-sqlite3 não encontrado — scheduler usa memória apenas");
    return null;
  }
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS scheduled_tasks (
  task_id       TEXT PRIMARY KEY,
  prompt        TEXT NOT NULL,
  trigger_type  TEXT NOT NULL,
  trigger_value TEXT NOT NULL,
  enabled       INTEGER DEFAULT 1,
  client_id     TEXT DEFAULT 'scheduler',
  last_run_ts   REAL DEFAULT 0,
  next_run_ts   REAL DEFAULT 0,
  run_count     INTEGER DEFAULT 0,
  last_status   TEXT DEFAULT 'pending',
  last_error    TEXT DEFAULT '',
  created_ts    REAL NOT NULL,
  tags          TEXT DEFAULT '[]'
)`;

// ---------------------------------------------------------------------------
// GatewayScheduler
// ---------------------------------------------------------------------------

export class GatewayScheduler {
  private readonly registry: ChannelRegistry;
  private readonly maxWorkers: number;
  private readonly dbPath: string;

  private db: Database | null = null;
  private tasks = new Map<string, ScheduledTask>();
  private running = new Set<string>();
  private tickTimer: ReturnType<typeof setInterval> | null = null;
  private started = false;

  constructor(
    registry: ChannelRegistry,
    options: {
      dbPath?: string;
      maxWorkers?: number;
    } = {},
  ) {
    this.registry = registry;
    this.maxWorkers = options.maxWorkers ?? parseInt(process.env["RLM_SCHEDULER_WORKERS"] ?? "4", 10);
    this.dbPath =
      options.dbPath ??
      process.env["RLM_SCHEDULER_DB"] ??
      join(homedir(), ".rlm", "scheduler.db");
  }

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;

    this.db = openDatabase(this.dbPath);
    if (this.db) {
      this.db.exec(SCHEMA);
      this.loadFromDb();
    }

    log.info({ dbPath: this.dbPath, maxWorkers: this.maxWorkers }, "Scheduler started");

    // Tick a cada 30 segundos (suficiente para granularidade de minuto em cron)
    this.tickTimer = setInterval(() => void this.tick(), 30_000);
    this.tickTimer.unref?.();
  }

  async stop(): Promise<void> {
    if (!this.started) return;
    this.started = false;

    if (this.tickTimer) {
      clearInterval(this.tickTimer);
      this.tickTimer = null;
    }
    this.db?.close();
    this.db = null;
    log.info("Scheduler stopped");
  }

  // --------------------------------------------------------------------------
  // Task management
  // --------------------------------------------------------------------------

  addTask(options: AddTaskOptions): ScheduledTask {
    const taskId = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
    const now = Date.now() / 1000;
    const task: ScheduledTask = {
      taskId,
      prompt: options.prompt,
      triggerType: options.triggerType,
      triggerValue: options.triggerValue,
      enabled: true,
      clientId: options.clientId ?? "scheduler",
      lastRunTs: 0,
      nextRunTs: this.computeNextRun(options.triggerType, options.triggerValue, 0),
      runCount: 0,
      lastStatus: "pending",
      lastError: "",
      createdTs: now,
      tags: options.tags ?? [],
    };

    this.tasks.set(taskId, task);
    this.persistTask(task);
    log.info({ taskId, trigger: `${options.triggerType}:${options.triggerValue}` }, "Task added");
    return task;
  }

  removeTask(taskId: string): boolean {
    if (!this.tasks.has(taskId)) return false;
    this.tasks.delete(taskId);
    this.db?.prepare("DELETE FROM scheduled_tasks WHERE task_id = ?").run(taskId);
    return true;
  }

  listTasks(): ScheduledTask[] {
    return [...this.tasks.values()];
  }

  getTask(taskId: string): ScheduledTask | undefined {
    return this.tasks.get(taskId);
  }

  // --------------------------------------------------------------------------
  // Tick — verifica tarefas elegíveis
  // --------------------------------------------------------------------------

  private async tick(): Promise<void> {
    const now = Date.now() / 1000;
    const eligible: ScheduledTask[] = [];

    for (const task of this.tasks.values()) {
      if (!task.enabled) continue;
      if (this.running.has(task.taskId)) continue;
      if (task.nextRunTs > now) continue;
      eligible.push(task);
    }

    const slots = this.maxWorkers - this.running.size;
    const toRun = eligible.slice(0, slots);

    await Promise.allSettled(toRun.map((t) => this.runTask(t)));
  }

  private async runTask(task: ScheduledTask): Promise<void> {
    this.running.add(task.taskId);
    task.lastStatus = "running";
    task.lastRunTs = Date.now() / 1000;
    log.info({ taskId: task.taskId, prompt: task.prompt.slice(0, 80) }, "Running scheduled task");

    try {
      const envelope = newEnvelope({
        source_channel: "internal",
        source_id: `scheduler:${task.clientId}`,
        source_client_id: `scheduler:${task.clientId}`,
        direction: "inbound",
        message_type: "text",
        text: task.prompt,
        metadata: { routing_key: task.clientId, scheduled_task_id: task.taskId },
      });

      const forwarded = this.registry.forwardToBrain(envelope);
      if (!forwarded) throw new Error("Brain bridge unavailable");

      task.lastStatus = "success";
      task.runCount++;
      log.info({ taskId: task.taskId }, "Task dispatched to brain");
    } catch (err) {
      task.lastStatus = "error";
      task.lastError = String(err);
      log.error({ err, taskId: task.taskId }, "Task execution failed");
    } finally {
      // Atualiza next_run ou desabilita se one-shot
      if (task.triggerType === "once") {
        task.enabled = false;
        task.nextRunTs = 0;
      } else {
        task.nextRunTs = this.computeNextRun(
          task.triggerType,
          task.triggerValue,
          task.lastRunTs,
        );
      }

      this.persistTask(task);
      this.running.delete(task.taskId);
    }
  }

  // --------------------------------------------------------------------------
  // Next-run computation
  // --------------------------------------------------------------------------

  private computeNextRun(
    type: TriggerType,
    value: string,
    lastRunTs: number,
  ): number {
    const now = Date.now() / 1000;

    if (type === "once") {
      const ts = Date.parse(value) / 1000;
      return isNaN(ts) ? 0 : ts;
    }

    if (type === "interval") {
      const sec = parseFloat(value);
      if (isNaN(sec) || sec <= 0) return 0;
      return (lastRunTs > 0 ? lastRunTs : now) + sec;
    }

    if (type === "cron") {
      // Avança minuto a minuto até encontrar match (máx 1 hora)
      const start = new Date(Math.ceil(now * 1000 / 60_000) * 60_000);
      for (let i = 0; i < 60; i++) {
        const candidate = new Date(start.getTime() + i * 60_000);
        if (cronMatches(value, candidate)) {
          return candidate.getTime() / 1000;
        }
      }
    }

    return 0;
  }

  // --------------------------------------------------------------------------
  // Persistence
  // --------------------------------------------------------------------------

  private persistTask(task: ScheduledTask): void {
    if (!this.db) return;
    const stmt = this.db.prepare(`
      INSERT OR REPLACE INTO scheduled_tasks
        (task_id, prompt, trigger_type, trigger_value, enabled, client_id,
         last_run_ts, next_run_ts, run_count, last_status, last_error, created_ts, tags)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    stmt.run(
      task.taskId, task.prompt, task.triggerType, task.triggerValue,
      task.enabled ? 1 : 0, task.clientId,
      task.lastRunTs, task.nextRunTs, task.runCount,
      task.lastStatus, task.lastError, task.createdTs,
      JSON.stringify(task.tags),
    );
  }

  private loadFromDb(): void {
    if (!this.db) return;
    const rows = this.db.prepare("SELECT * FROM scheduled_tasks").all();
    for (const row of rows) {
      const task: ScheduledTask = {
        taskId: row.task_id,
        prompt: row.prompt,
        triggerType: row.trigger_type as TriggerType,
        triggerValue: row.trigger_value,
        enabled: row.enabled === 1,
        clientId: row.client_id,
        lastRunTs: row.last_run_ts,
        nextRunTs: row.next_run_ts,
        runCount: row.run_count,
        lastStatus: row.last_status as ScheduledTask["lastStatus"],
        lastError: row.last_error,
        createdTs: row.created_ts,
        tags: ((): string[] => { try { return JSON.parse(row.tags) as string[]; } catch { return []; } })(),
      };
      this.tasks.set(task.taskId, task);
    }
    log.info({ count: this.tasks.size }, "Tasks loaded from database");
  }
}
