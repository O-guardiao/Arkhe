/**
 * Formatadores de saída para o CLI — suporte a texto colorido e tabelas.
 */

import {
  ANSI,
  renderHealthBadge,
  renderNote,
  renderTable,
  sanitizeForTerminal,
  type HealthStatus,
  type NoteRenderOptions,
  type NoteStyle,
  wrapWords,
  getTheme,
} from "@arkhe/terminal";

function applyStyle(style: string, text: string): string {
  return `${style}${text}${ANSI.RESET}`;
}

function currentTheme() {
  return getTheme();
}

function normalizeInline(value: unknown): string {
  return sanitizeForTerminal(String(value ?? ""))
    .replace(/\n+/g, " ")
    .replace(/\t+/g, " ")
    .trim();
}

function terminalWidth(): number | undefined {
  const columns = process.stdout.columns;
  if (!columns || columns <= 0) {
    return undefined;
  }
  return Math.max(40, columns - 2);
}

// ---------------------------------------------------------------------------
// Cores semânticas
// ---------------------------------------------------------------------------

export const c = {
  success: (value: string) => applyStyle(currentTheme().success, value),
  error: (value: string) => applyStyle(currentTheme().error, value),
  warn: (value: string) => applyStyle(currentTheme().warning, value),
  info: (value: string) => applyStyle(currentTheme().info, value),
  dim: (value: string) => applyStyle(currentTheme().muted, value),
  bold: (value: string) => applyStyle(ANSI.BOLD, value),
  tool: (value: string) => applyStyle(currentTheme().secondary, value),
  session: (value: string) => applyStyle(currentTheme().info, value),
  heading: (value: string) => applyStyle(currentTheme().heading, value),
  code: (value: string) => applyStyle(currentTheme().code, value),
} as const;

// ---------------------------------------------------------------------------
// Formatadores
// ---------------------------------------------------------------------------

export function fmtMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

export function fmtTimestamp(ts: number): string {
  return new Date(ts * 1000).toLocaleString("pt-BR");
}

export function fmtBool(v: boolean): string {
  return v ? c.success("✓") : c.error("✗");
}

export function coerceHealthStatus(status: string): HealthStatus {
  const normalized = status.trim().toLowerCase();
  if (["ok", "healthy", "online", "connected", "active", "running", "up"].includes(normalized)) {
    return "up";
  }
  if (["degraded", "warning", "warn"].includes(normalized)) {
    return "degraded";
  }
  if (["unknown", "idle", "disabled", "inactive"].includes(normalized)) {
    return "unknown";
  }
  return "down";
}

export function healthBadge(status: string): string {
  return renderHealthBadge(coerceHealthStatus(status));
}

export function printNote(
  style: NoteStyle,
  message: string,
  opts: NoteRenderOptions = {},
): void {
  const width = opts.width ?? Math.max(48, Math.min(88, (process.stdout.columns ?? 80) - 2));
  const innerWidth = Math.max(4, width - 4);
  const wrapped = sanitizeForTerminal(message)
    .split("\n")
    .flatMap((line: string) => wrapWords(line, innerWidth))
    .join("\n");
  console.log(renderNote(style, wrapped, { ...opts, width }));
}

export function printTable(rows: Record<string, string>[], columns?: string[]): void;
export function printTable(headers: string[], rows: string[][]): void;
export function printTable(
  rowsOrHeaders: Record<string, string>[] | string[],
  columnsOrRows?: string[] | string[][],
): void {
  const matrixMode = Array.isArray(columnsOrRows) && (rowsOrHeaders.length === 0 || typeof rowsOrHeaders[0] === "string");

  let headers: string[];
  let rows: string[][];

  if (matrixMode) {
    headers = (rowsOrHeaders as string[]).map((header) => normalizeInline(header));
    rows = (columnsOrRows as string[][]).map((row) =>
      headers.map((_, index) => normalizeInline(row[index] ?? "")),
    );
  } else {
    const records = rowsOrHeaders as Record<string, string>[];
    if (records.length === 0) {
      console.log(c.dim("(sem resultados)"));
      return;
    }
    headers = ((columnsOrRows as string[] | undefined) ?? Object.keys(records[0] ?? {})).map((header) =>
      normalizeInline(header),
    );
    rows = records.map((record) => headers.map((header) => normalizeInline(record[header] ?? "")));
  }

  if (rows.length === 0) {
    console.log(c.dim("(sem resultados)"));
    return;
  }

  const maxWidth = terminalWidth();
  const tableOptions =
    maxWidth === undefined
      ? {
          headers,
          rows,
          borderStyle: "single" as const,
        }
      : {
          headers,
          rows,
          borderStyle: "single" as const,
          maxWidth,
        };

  console.log(renderTable(tableOptions));
}

export function printJson(data: unknown): void {
  console.log(JSON.stringify(data, null, 2));
}

export function printError(err: unknown): void {
  if (err instanceof Error) {
    console.error(c.error(`Erro: ${err.message}`));
  } else {
    console.error(c.error(`Erro: ${String(err)}`));
  }
}
