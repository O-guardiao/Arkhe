/**
 * MessagesPanel — painel central superior do TUI.
 *
 * Exibe o fluxo de mensagens trocadas entre utilizadores e o Brain.
 * As mensagens mais recentes ficam sempre visíveis (auto-scroll).
 */

import chalk from "chalk";
import type { Rect } from "./workbench.js";
import { moveTo, padEnd, truncate } from "./ansi.js";

export type MessageRole = "user" | "agent" | "system";

export interface MessageEntry {
  ts: string;       // HH:MM:SS
  role: MessageRole;
  channel: string;
  text: string;
}

export interface TimelineEntry {
  kind: string;
  summary: string;
}

const ROLE_PREFIX: Record<MessageRole, string> = {
  user:   chalk.green("▸ user  "),
  agent:  chalk.cyan("◂ agent "),
  system: chalk.gray("⊙ sys   "),
};

export class MessagesPanel {
  private messages: MessageEntry[] = [];
  private runtimeMessages: MessageEntry[] = [];
  private timeline: TimelineEntry[] = [];
  private readonly MAX_ENTRIES = 500;

  constructor(private rect: Rect) {}

  updateRect(rect: Rect): void {
    this.rect = rect;
  }

  push(msg: MessageEntry): void {
    this.messages.push(msg);
    if (this.messages.length > this.MAX_ENTRIES) {
      this.messages.shift();
    }
  }

  setRuntimeSnapshot(messages: MessageEntry[], timeline: TimelineEntry[]): void {
    this.runtimeMessages = messages.slice(-40);
    this.timeline = timeline.slice(-20);
  }

  render(buf: string[]): void {
    const { top, left, width, height } = this.rect;

    // Cabeçalho
    buf.push(moveTo(top, left) + chalk.bold.cyan(padEnd(" Messages", width)));
    buf.push(moveTo(top + 1, left) + chalk.dim("─".repeat(width)));

    const bodyHeight = height - 2;
    const activeMessages = (this.runtimeMessages.length > 0 ? this.runtimeMessages : this.messages).slice(-40);
    const activeTimeline = this.timeline.slice(-20);
    const hasTimeline = activeTimeline.length > 0;

    const messageBudget = hasTimeline
      ? Math.max(Math.floor(bodyHeight * 0.55), 4)
      : bodyHeight;
    const visibleMessages = activeMessages.slice(-messageBudget);

    const lines: string[] = [];
    for (const msg of visibleMessages) {
      const prefix = ROLE_PREFIX[msg.role];
      const ts = msg.ts ? chalk.dim(msg.ts + " ") : "";
      const ch = chalk.dim(`[${msg.channel}] `);
      lines.push(truncate(ts + prefix + ch + msg.text, width));
    }

    if (hasTimeline) {
      if (lines.length < bodyHeight) {
        lines.push("");
      }
      if (lines.length < bodyHeight) {
        lines.push(chalk.bold.magenta(" Timeline"));
      }
      const remaining = Math.max(bodyHeight - lines.length, 0);
      for (const entry of activeTimeline.slice(-remaining)) {
        lines.push(
          truncate(
            chalk.magenta((entry.kind || "-").padEnd(16)) + " " + entry.summary,
            width,
          ),
        );
      }
    }

    for (let i = 0; i < bodyHeight; i++) {
      const rowNum = top + 2 + i;
      const line = lines[i];
      if (!line) {
        buf.push(moveTo(rowNum, left) + " ".repeat(width));
        continue;
      }
      buf.push(moveTo(rowNum, left) + padEnd(line, width));
    }
  }
}
