/**
 * Footer — barra inferior do TUI.
 *
 * Exibe:
 *  - modo actual (ex: "LIVE" vs "PAUSED")
 *  - atalhos de teclado disponíveis
 *  - linha de input para enviar prompts ao Brain sem sair do TUI
 *  - status da conexão WS
 */

import chalk from "chalk";
import type { Rect } from "./workbench.js";
import { moveTo, padEnd, ERASE_LINE } from "./ansi.js";
import type { ConnectionStatus } from "../lib/ws-client.js";

const CONN_LABEL: Record<ConnectionStatus, string> = {
  idle:         chalk.gray("IDLE"),
  connecting:   chalk.yellow("CONNECTING…"),
  connected:    chalk.green("LIVE"),
  disconnected: chalk.red("DISCONNECTED"),
  error:        chalk.red("ERROR"),
};

const SHORTCUTS = [
  ["↑↓", "navegar"],
  ["Enter", "seleccionar"],
  ["p", "pausar/retomar"],
  ["c", "limpar"],
  ["q", "sair"],
].map(([k, v]) => chalk.bold.white(k) + chalk.dim(":" + v)).join("  ");

export class Footer {
  private inputBuffer = "";
  private paused = false;

  constructor(private rect: Rect) {}

  updateRect(rect: Rect): void {
    this.rect = rect;
  }

  /** Appends a character to the input buffer. */
  typeChar(char: string): void {
    this.inputBuffer += char;
  }

  /** Removes last char (backspace). */
  backspace(): void {
    this.inputBuffer = this.inputBuffer.slice(0, -1);
  }

  /** Flushes and returns the current input buffer. */
  flushInput(): string {
    const text = this.inputBuffer.trim();
    this.inputBuffer = "";
    return text;
  }

  togglePause(): boolean {
    this.paused = !this.paused;
    return this.paused;
  }

  isPaused(): boolean {
    return this.paused;
  }

  render(buf: string[], connStatus: ConnectionStatus, reconnects: number): void {
    const { top, left, width } = this.rect;

    // Linha 1: status + shortcuts
    const statusBadge = CONN_LABEL[connStatus] ?? chalk.gray("?");
    const reconStr = reconnects > 0 ? chalk.dim(` (${reconnects}r)`) : "";
    const pauseStr = this.paused ? chalk.bgYellow.black(" PAUSED ") : "";
    const statusLine = ` ${statusBadge}${reconStr} ${pauseStr}  ${SHORTCUTS}`;

    buf.push(moveTo(top, left) + chalk.dim("─".repeat(width)));
    buf.push(moveTo(top + 1, left) + ERASE_LINE + padEnd(statusLine, width));

    // Linha 2: input bar
    if (this.rect.height >= 3) {
      const prompt = chalk.cyan("> ");
      const inputRow = top + 2;
      buf.push(moveTo(inputRow, left) + ERASE_LINE + prompt + this.inputBuffer + "█");
    }
  }
}
