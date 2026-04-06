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
import { moveTo, padEnd, truncate, renderBox } from "./ansi.js";

const STATUS_COLORS = {
  active:   chalk.green("●"),
  inactive: chalk.gray("○"),
  error:    chalk.red("✕"),
} as const;

type ChannelStatus = keyof typeof STATUS_COLORS;

interface ChannelEntry {
  name: string;
  status: ChannelStatus;
  count: number;
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
      this.channels.set(name, { name, status, count: countDelta });
    }
  }

  incrementCount(name: string): void {
    const c = this.channels.get(name);
    if (c) c.count++;
    else this.channels.set(name, { name, status: "active", count: 1 });
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

    // Cabeçalho
    const header = chalk.bold.cyan(padEnd(" Channels", width));
    buf.push(moveTo(top, left) + truncate(header, width));
    buf.push(moveTo(top + 1, left) + chalk.dim("─".repeat(width)));

    const entries = [...this.channels.values()];
    const maxVisible = height - 2;

    for (let i = 0; i < maxVisible; i++) {
      const entry = entries[i];
      const rowNum = top + 2 + i;
      if (!entry) {
        buf.push(moveTo(rowNum, left) + " ".repeat(width));
        continue;
      }

      const isSelected = i === this.selectedIdx;
      const badge = STATUS_COLORS[entry.status];
      const countStr = chalk.dim(` (${entry.count})`);
      const nameStr = entry.name;
      const raw = ` ${nameStr}${countStr}`;

      const line = isSelected ? chalk.bgBlue.white(padEnd(` ${nameStr}`, width - 2)) : raw;
      buf.push(moveTo(rowNum, left) + badge + " " + truncate(line, width - 2));
    }
  }
}
