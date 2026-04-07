/**
 * ChannelPanel — painel esquerdo do TUI.
 *
 * Exibe a lista de canais registados no gateway com:
 *  • status badge (● verde / ○ cinzento)
 *  • nome do canal
 *  • contagem de mensagens recebidas (incrementa em tempo real)
 */

import chalk from "chalk";
import type { Rect } from "./workbench.js";
import { moveTo, padEnd, truncate } from "./ansi.js";

const STATUS_COLORS = {
  active:   chalk.green("●"),
  inactive: chalk.gray("○"),
  error:    chalk.red("✕"),
} as const;

const CONFIGURED_BADGE = chalk.yellow("◑");

export type ChannelStatus = keyof typeof STATUS_COLORS;

interface ChannelEntry {
  name: string;
  status: ChannelStatus;
  count: number;
  identityName: string;
  lastProbeMs: number;
  reconnectAttempts: number;
  lastError: string | null;
  configured: boolean;
}

interface ChannelSyncEntry {
  name: string;
  status: ChannelStatus;
  identityName?: string;
  lastProbeMs?: number;
  reconnectAttempts?: number;
  lastError?: string | null;
  configured?: boolean;
}

function renderStatusBadge(entry: ChannelEntry): string {
  if (entry.status === "inactive" && entry.configured) {
    return CONFIGURED_BADGE;
  }
  return STATUS_COLORS[entry.status];
}

export class ChannelPanel {
  private channels = new Map<string, ChannelEntry>();
  private selectedIdx = 0;

  constructor(private rect: Rect) {}

  updateRect(rect: Rect): void {
    this.rect = rect;
  }

  /** Atualiza ou cria um canal. */
  upsert(name: string, status: ChannelStatus, countDelta = 0): void {
    const existing = this.channels.get(name);
    if (existing) {
      existing.status = status;
      existing.count += countDelta;
    } else {
      this.channels.set(name, {
        name,
        status,
        count: countDelta,
        identityName: "",
        lastProbeMs: 0,
        reconnectAttempts: 0,
        lastError: null,
        configured: status !== "inactive",
      });
    }
  }

  incrementCount(name: string): void {
    const c = this.channels.get(name);
    if (c) c.count++;
    else this.channels.set(name, {
      name,
      status: "active",
      count: 1,
      identityName: "",
      lastProbeMs: 0,
      reconnectAttempts: 0,
      lastError: null,
      configured: true,
    });
  }

  sync(entries: ReadonlyArray<ChannelSyncEntry>): void {
    const next = new Map<string, ChannelEntry>();
    for (const entry of entries) {
      const existing = this.channels.get(entry.name);
      next.set(entry.name, {
        name: entry.name,
        status: entry.status,
        count: existing?.count ?? 0,
        identityName: entry.identityName ?? existing?.identityName ?? "",
        lastProbeMs: entry.lastProbeMs ?? existing?.lastProbeMs ?? 0,
        reconnectAttempts: entry.reconnectAttempts ?? existing?.reconnectAttempts ?? 0,
        lastError: entry.lastError ?? existing?.lastError ?? null,
        configured: entry.configured ?? existing?.configured ?? entry.status !== "inactive",
      });
    }
    this.channels = next;
    this.selectedIdx = Math.min(this.selectedIdx, Math.max(this.channels.size - 1, 0));
  }

  moveDown(): void {
    this.selectedIdx = Math.min(this.selectedIdx + 1, this.channels.size - 1);
  }

  moveUp(): void {
    this.selectedIdx = Math.max(this.selectedIdx - 1, 0);
  }

  selectedChannel(): string | undefined {
    return [...this.channels.keys()][this.selectedIdx];
  }

  render(buf: string[]): void {
    const { top, left, width, height } = this.rect;
    const channelWidth = Math.min(12, Math.max(8, Math.floor(width * 0.24)));
    const latencyWidth = 7;
    const errorsWidth = 4;
    const botWidth = Math.max(width - channelWidth - latencyWidth - errorsWidth - 8, 12);

    // Cabeçalho
    const header = chalk.bold.cyan(padEnd(" Channels", width));
    buf.push(moveTo(top, left) + truncate(header, width));
    buf.push(moveTo(top + 1, left) + chalk.dim("─".repeat(width)));

    const entries = [...this.channels.values()];
    const maxVisible = height - 2;

    if (entries.length > 0) {
      const columns =
        `${" ".repeat(2)}${chalk.bold("Canal".padEnd(channelWidth))} ` +
        `${chalk.bold("Bot".padEnd(botWidth))} ` +
        `${chalk.bold("Lat".padStart(latencyWidth))} ` +
        `${chalk.bold("Err".padStart(errorsWidth))}`;
      buf.push(moveTo(top + 2, left) + truncate(padEnd(columns, width), width));
    }

    const startRow = entries.length > 0 ? top + 3 : top + 2;
    const bodyRows = entries.length > 0 ? maxVisible - 1 : maxVisible;

    for (let i = 0; i < bodyRows; i++) {
      const entry = entries[i];
      const rowNum = startRow + i;
      if (!entry) {
        buf.push(moveTo(rowNum, left) + " ".repeat(width));
        continue;
      }

      const isSelected = i === this.selectedIdx;
      const selector = isSelected ? chalk.cyan("›") : " ";
      const badge = renderStatusBadge(entry);
      const latency = entry.lastProbeMs > 0 ? `${Math.round(entry.lastProbeMs)}ms` : "-";
      const errors = entry.reconnectAttempts > 0 ? String(entry.reconnectAttempts) : "-";

      let botLabel = entry.identityName || "-";
      if (entry.lastError && entry.status === "error") {
        botLabel = entry.lastError;
      }
      if (entry.count > 0) {
        botLabel = `${botLabel} [${entry.count}]`;
      }

      const details =
        `${entry.name.padEnd(channelWidth)} ` +
        `${botLabel.padEnd(botWidth)} ` +
        `${latency.padStart(latencyWidth)} ` +
        `${errors.padStart(errorsWidth)}`;
      const content = `${selector} ${badge} ${truncate(details, width - 4)}`;
      const line = isSelected ? chalk.bgBlue.white(padEnd(content, width)) : padEnd(content, width);
      buf.push(moveTo(rowNum, left) + truncate(line, width));
    }
  }
}
