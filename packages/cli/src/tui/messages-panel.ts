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

const ROLE_PREFIX: Record<MessageRole, string> = {
  user:   chalk.green("▸ user  "),
  agent:  chalk.cyan("◂ agent "),
  system: chalk.gray("⊙ sys   "),
};

export class MessagesPanel {
  private messages: MessageEntry[] = [];
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

  render(buf: string[]): void {
    const { top, left, width, height } = this.rect;

    // Cabeçalho
    buf.push(moveTo(top, left) + chalk.bold.cyan(padEnd(" Messages", width)));
    buf.push(moveTo(top + 1, left) + chalk.dim("─".repeat(width)));

    const bodyHeight = height - 2;
    const visible = this.messages.slice(-bodyHeight);

    for (let i = 0; i < bodyHeight; i++) {
      const rowNum = top + 2 + i;
      const msg = visible[i];
      if (!msg) {
        buf.push(moveTo(rowNum, left) + " ".repeat(width));
        continue;
      }

      const prefix = ROLE_PREFIX[msg.role];
      const ts = chalk.dim(msg.ts + " ");
      const ch = chalk.dim(`[${msg.channel}] `);
      const text = msg.text;

      const composed = ts + prefix + ch + text;
      buf.push(moveTo(rowNum, left) + truncate(composed, width));
    }
  }
}
