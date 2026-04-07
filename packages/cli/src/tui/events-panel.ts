/**
 * EventsPanel — painel direito do TUI.
 *
 * Exibe eventos de observabilidade / trace em tempo real:
 *  - ferramenta invocada
 *  - memória lida/escrita
 *  - tokens consumidos
 *  - erros
 *  - latência de LLM
 */

import chalk from "chalk";
import type { Rect } from "./workbench.js";
import { moveTo, padEnd, truncate } from "./ansi.js";

export interface ObsEvent {
  ts: string;      // HH:MM:SS
  kind: string;    // "tool_call" | "memory_read" | "llm_latency" | "error" | ...
  label: string;   // texto curto exibível
  detail?: string; // detalhe extra (opcional)
}

const KIND_COLOR: Record<string, (s: string) => string> = {
  tool_call:    (s) => chalk.yellow(s),
  memory_read:  (s) => chalk.blue(s),
  memory_write: (s) => chalk.magenta(s),
  llm_latency:  (s) => chalk.cyan(s),
  command:      (s) => chalk.green(s),
  event:        (s) => chalk.white(s),
  error:        (s) => chalk.red(s),
  default:      (s) => chalk.white(s),
};

function colorKind(kind: string, text: string): string {
  const fn = KIND_COLOR[kind] ?? KIND_COLOR["default"]!;
  return fn(text);
}

export class EventsPanel {
  private events: ObsEvent[] = [];
  private latestResponse = "-";
  private readonly MAX_ENTRIES = 1000;

  constructor(private rect: Rect) {}

  updateRect(rect: Rect): void {
    this.rect = rect;
  }

  push(event: ObsEvent): void {
    this.events.push(event);
    if (this.events.length > this.MAX_ENTRIES) {
      this.events.shift();
    }
  }

  setLatestResponse(value: string): void {
    const trimmed = value.trim();
    this.latestResponse = trimmed || "-";
  }

  render(buf: string[]): void {
    const { top, left, width, height } = this.rect;

    buf.push(moveTo(top, left) + chalk.bold.cyan(padEnd(" Events", width)));
    buf.push(moveTo(top + 1, left) + chalk.dim("─".repeat(width)));

    const bodyHeight = height - 2;
    const helpLines = [
      "/pause /resume /checkpoint",
      "/focus /winner /priority /note",
      "/channels /probe /send /watch /quit",
    ];
    const reservedTop = 3;
    const reservedBottom = Math.min(helpLines.length + 1, Math.max(bodyHeight - reservedTop, 0));
    const eventsBudget = Math.max(bodyHeight - reservedTop - reservedBottom, 0);
    const visible = this.events.slice(-eventsBudget);

    const lines: string[] = [
      chalk.bold.yellow(" Resposta mais recente"),
      truncate(` ${this.latestResponse}`, width),
      "",
    ];

    for (const ev of visible) {
      const ts = ev.ts ? chalk.dim(ev.ts + " ") : "";
      const kind = colorKind(ev.kind, ev.kind.padEnd(12));
      lines.push(truncate(ts + kind + " " + ev.label, width));
    }

    if (helpLines.length > 0) {
      lines.push("");
      for (const help of helpLines.slice(0, Math.max(bodyHeight - lines.length, 0))) {
        lines.push(chalk.dim(help));
      }
    }

    for (let i = 0; i < bodyHeight; i++) {
      const rowNum = top + 2 + i;
      const line = lines[i];
      if (!line) {
        buf.push(moveTo(rowNum, left) + " ".repeat(width));
        continue;
      }
      buf.push(moveTo(rowNum, left) + truncate(line, width));
    }
  }
}
