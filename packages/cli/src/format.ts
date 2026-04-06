/**
 * Formatadores de saída para o CLI — suporte a texto colorido e tabelas.
 */

import chalk from "chalk";

// ---------------------------------------------------------------------------
// Cores semânticas
// ---------------------------------------------------------------------------

export const c = {
  success: chalk.green,
  error: chalk.red,
  warn: chalk.yellow,
  info: chalk.cyan,
  dim: chalk.gray,
  bold: chalk.bold,
  tool: chalk.magenta,
  session: chalk.blue,
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

export function printTable(
  rows: Record<string, string>[],
  columns?: string[]
): void {
  if (rows.length === 0) {
    console.log(c.dim("(sem resultados)"));
    return;
  }

  const cols = columns ?? Object.keys(rows[0] ?? {});
  const widths: Record<string, number> = {};

  for (const col of cols) {
    widths[col] = Math.max(
      col.length,
      ...rows.map((r) => String(r[col] ?? "").length)
    );
  }

  const sep = cols.map((col) => "-".repeat(widths[col] ?? col.length)).join("-+-");
  const header = cols
    .map((col) => c.bold(col.padEnd(widths[col] ?? col.length)))
    .join(" | ");

  console.log(header);
  console.log(c.dim(sep));

  for (const row of rows) {
    const line = cols
      .map((col) => String(row[col] ?? "").padEnd(widths[col] ?? col.length))
      .join(" | ");
    console.log(line);
  }
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
