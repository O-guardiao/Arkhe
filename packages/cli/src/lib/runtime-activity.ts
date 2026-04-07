export type ActivityRecord = Record<string, unknown>;

export type SurfaceBranchStatus = "running" | "ok" | "error" | "cancelled";

export interface ActivitySessionSnapshot {
  sessionId: string;
  clientId: string;
  status: string;
  stateDir: string;
  metadata: ActivityRecord;
}

export interface ActivityControlsSnapshot {
  paused: boolean;
  pauseReason: string;
  focusedBranchId: number | null;
  fixedWinnerBranchId: number | null;
  branchPriorities: Record<string, number>;
  lastCheckpointPath: string;
  lastOperatorNote: string;
}

export interface ActivityCurrentTaskSnapshot {
  title: string;
  status: string;
}

export interface ActivityParallelSummarySnapshot {
  winnerBranchId: number | null;
  cancelledCount: number;
  failedCount: number;
  totalTasks: number;
  branchCount: number;
  branchStatusCounts: Record<string, number>;
  strategy: ActivityRecord;
  stopEvaluation: ActivityRecord;
  raw: ActivityRecord;
}

export interface ActivityBranchSnapshot {
  id: string;
  branchId: number;
  parentId?: string;
  taskId: number | null;
  parentTaskId: number | null;
  title: string;
  mode: string;
  statusText: string;
  surfaceStatus: SurfaceBranchStatus;
  depth: number | null;
  durationMs?: number;
  errorMessage?: string;
  metadata: ActivityRecord;
  raw: ActivityRecord;
  label: string;
}

export interface ActivityGatewayEvent {
  type: string;
  payload: ActivityRecord;
  ts?: number;
  eventId?: string;
}

export interface ActivityCoordinationEvent {
  operation: string;
  topic: string;
  senderId: number | null;
  receiverId: number | null;
  payloadPreview: string;
  metadata: ActivityRecord;
  timestamp: string;
  raw: ActivityRecord;
}

export interface NormalizedActivityPayload {
  raw: ActivityRecord;
  session: ActivitySessionSnapshot;
  runtime: {
    controls: ActivityControlsSnapshot;
    currentTask: ActivityCurrentTaskSnapshot | null;
    recursiveMessages: ActivityRecord[];
    recursiveEvents: ActivityRecord[];
    timelineEntries: ActivityRecord[];
    coordinationEvents: ActivityCoordinationEvent[];
    branchTasks: ActivityBranchSnapshot[];
    summary: ActivityParallelSummarySnapshot;
  };
  eventLog: ActivityRecord[];
  gatewayEvents: ActivityGatewayEvent[];
}

function asRecord(value: unknown): ActivityRecord {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as ActivityRecord;
  }
  return {};
}

function asRecordArray(value: unknown): ActivityRecord[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => asRecord(item));
}

function asString(value: unknown, fallback = ""): string {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return fallback;
}

function asBoolean(value: unknown): boolean {
  return value === true;
}

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return null;
}

function asNumberRecord(value: unknown): Record<string, number> {
  const raw = asRecord(value);
  const normalized: Record<string, number> = {};
  for (const [key, entry] of Object.entries(raw)) {
    const numberValue = asNumber(entry);
    if (numberValue != null) {
      normalized[key] = numberValue;
    }
  }
  return normalized;
}

function normalizeBranchStatus(statusText: string): SurfaceBranchStatus {
  switch (statusText.trim().toLowerCase()) {
    case "done":
    case "completed":
    case "success":
      return "ok";
    case "cancelled":
    case "canceled":
      return "cancelled";
    case "blocked":
    case "error":
    case "failed":
    case "timeout":
      return "error";
    default:
      return "running";
  }
}

function readDurationMs(raw: ActivityRecord, metadata: ActivityRecord): number | undefined {
  const topLevel = asNumber(raw["duration_ms"]);
  if (topLevel != null) {
    return topLevel;
  }
  const nested = asNumber(metadata["duration_ms"]);
  if (nested != null) {
    return nested;
  }
  const elapsedSeconds = asNumber(metadata["elapsed_s"]);
  if (elapsedSeconds != null) {
    return Math.round(elapsedSeconds * 1000);
  }
  return undefined;
}

function buildBranchLabel(title: string, mode: string, statusText: string, depth: number | null): string {
  const parts = [title || "sem titulo", mode || "-", statusText || "-"];
  if (depth != null) {
    parts.push(`d${depth}`);
  }
  return parts.join(" | ");
}

function normalizeBranchTask(value: unknown): ActivityBranchSnapshot | null {
  const raw = asRecord(value);
  const metadata = asRecord(raw["metadata"]);
  const branchId = asNumber(raw["branch_id"]);
  if (branchId == null) {
    return null;
  }

  const parentBranchId = asNumber(raw["parent_branch_id"]) ?? asNumber(metadata["parent_branch_id"]);
  const depth = asNumber(raw["depth"]) ?? asNumber(metadata["child_depth"]) ?? asNumber(metadata["depth"]);
  const title = asString(raw["title"], "sem titulo");
  const mode = asString(raw["mode"], "-");
  const statusText = asString(raw["status"], "-");
  const durationMs = readDurationMs(raw, metadata);
  const errorMessage = asString(raw["error_message"] ?? metadata["error"], "").trim() || undefined;

  const branch: ActivityBranchSnapshot = {
    id: String(branchId),
    branchId,
    taskId: asNumber(raw["task_id"]),
    parentTaskId: asNumber(raw["parent_task_id"]),
    title,
    mode,
    statusText,
    surfaceStatus: normalizeBranchStatus(statusText),
    depth,
    metadata,
    raw,
    label: buildBranchLabel(title, mode, statusText, depth),
  };
  if (parentBranchId != null) {
    branch.parentId = String(parentBranchId);
  }
  if (durationMs !== undefined) {
    branch.durationMs = durationMs;
  }
  if (errorMessage !== undefined) {
    branch.errorMessage = errorMessage;
  }
  return branch;
}

export function normalizeActivityPayload(value: ActivityRecord): NormalizedActivityPayload {
  const session = asRecord(value["session"] ?? value);
  const runtime = asRecord(value["runtime"]);
  const recursion = asRecord(runtime["recursion"]);
  const controls = asRecord(runtime["controls"]);
  const recursionControls = asRecord(recursion["controls"]);
  const tasks = asRecord(runtime["tasks"]);
  const currentTask = asRecord(tasks["current"]);
  const recursiveSession = asRecord(runtime["recursive_session"]);
  const timeline = asRecord(runtime["timeline"]);
  const coordination = asRecord(runtime["coordination"]);
  const summary = asRecord(coordination["latest_parallel_summary"]);
  const recursionSummary = asRecord(recursion["summary"]);
  const branchSource = Array.isArray(recursion["branches"]) ? recursion["branches"] : coordination["branch_tasks"];
  const coordinationEventSource = Array.isArray(recursion["events"]) ? recursion["events"] : coordination["events"];
  const summarySource = Object.keys(recursionSummary).length > 0 ? recursionSummary : summary;

  return {
    raw: value,
    session: {
      sessionId: asString(session["session_id"], ""),
      clientId: asString(session["client_id"], ""),
      status: asString(session["status"], "idle"),
      stateDir: asString(session["state_dir"], ""),
      metadata: asRecord(session["metadata"]),
    },
    runtime: {
      controls: {
        paused: asBoolean(recursionControls["paused"] ?? controls["paused"]),
        pauseReason: asString(recursionControls["pause_reason"] ?? controls["pause_reason"], ""),
        focusedBranchId: asNumber(recursionControls["focused_branch_id"] ?? controls["focused_branch_id"]),
        fixedWinnerBranchId: asNumber(recursionControls["fixed_winner_branch_id"] ?? controls["fixed_winner_branch_id"]),
        branchPriorities: asNumberRecord(recursionControls["branch_priorities"] ?? controls["branch_priorities"]),
        lastCheckpointPath: asString(recursionControls["last_checkpoint_path"] ?? controls["last_checkpoint_path"], "-"),
        lastOperatorNote: asString(recursionControls["last_operator_note"] ?? controls["last_operator_note"], ""),
      },
      currentTask: currentTask["title"] != null
        ? {
            title: asString(currentTask["title"], ""),
            status: asString(currentTask["status"], "-"),
          }
        : null,
      recursiveMessages: asRecordArray(recursiveSession["messages"]),
      recursiveEvents: asRecordArray(recursiveSession["events"]),
      timelineEntries: asRecordArray(timeline["entries"]),
      coordinationEvents: asRecordArray(coordinationEventSource).map((event) => ({
        operation: asString(event["operation"], "unknown"),
        topic: asString(event["topic"], ""),
        senderId: asNumber(event["sender_id"]),
        receiverId: asNumber(event["receiver_id"]),
        payloadPreview: asString(event["payload_preview"], ""),
        metadata: asRecord(event["metadata"]),
        timestamp: asString(event["timestamp"], ""),
        raw: event,
      })),
      branchTasks: asRecordArray(branchSource)
        .map((entry) => normalizeBranchTask(entry))
        .filter((entry): entry is ActivityBranchSnapshot => entry !== null),
      summary: {
        winnerBranchId: asNumber(summarySource["winner_branch_id"]),
        cancelledCount: asNumber(summarySource["cancelled_count"]) ?? 0,
        failedCount: asNumber(summarySource["failed_count"]) ?? 0,
        totalTasks: asNumber(summarySource["total_tasks"]) ?? 0,
        branchCount: asNumber(summarySource["branch_count"]) ?? 0,
        branchStatusCounts: asNumberRecord(summarySource["branch_status_counts"]),
        strategy: asRecord(summarySource["strategy"]),
        stopEvaluation: asRecord(summarySource["stop_evaluation"]),
        raw: summarySource,
      },
    },
    eventLog: asRecordArray(value["event_log"]),
    gatewayEvents: asRecordArray(value["events"]).map((event) => {
      const normalized: ActivityGatewayEvent = {
        type: asString(event["type"], ""),
        payload: asRecord(event["payload"]),
      };
      const ts = asNumber(event["ts"]);
      const eventId = asString(event["eventId"], "").trim();
      if (ts != null) {
        normalized.ts = ts;
      }
      if (eventId) {
        normalized.eventId = eventId;
      }
      return normalized;
    }),
  };
}