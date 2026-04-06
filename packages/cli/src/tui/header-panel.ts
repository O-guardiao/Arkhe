/**
 * Header Panel — cabeçalho do TUI com metadados da sessão.
 *
 * Migrado de workbench.py _build_header():
 *  - Session ID, Client ID, Status
 *  - Modo (live/local), Modelo
 *  - Controles: paused, focus, winner, checkpoint
 */

import chalk from "chalk";
import type { Rect } from "./workbench.js";
import { moveTo, padEnd, ERASE_LINE } from "./ansi.js";

export interface HeaderData {
  sessionId: string;
  clientId: string;
  status: string;
  mode: "live" | "local" | "disconnected" | "connecting";
  model: string;
  paused: boolean;
  focusedBranchId: number | string | null;
  winnerBranchId: number | string | null;
  lastCheckpoint: string;
  lastNotice: string;
}

export function createDefaultHeaderData(): HeaderData {
  return {
    sessionId: "-",
    clientId: "-",
    status: "idle",
    mode: "disconnected",
    model: "unknown",
    paused: false,
    focusedBranchId: null,
    winnerBranchId: null,
    lastCheckpoint: "-",
    lastNotice: "Use /help para ver os comandos do operador.",
  };
}

export class HeaderPanel {
  private data: HeaderData = createDefaultHeaderData();

  constructor(private rect: Rect) {}

  updateRect(rect: Rect): void {
    this.rect = rect;
  }

  update(partial: Partial<HeaderData>): void {
    Object.assign(this.data, partial);
  }

  getData(): Readonly<HeaderData> {
    return this.data;
  }

  render(buf: string[]): void {
    const { top, left, width } = this.rect;
    const d = this.data;

    // Linha 0: título
    const title = chalk.bold.cyan("Arkhe TUI Workbench");
    const modeBadge =
      d.mode === "live" ? chalk.bold.green(" LIVE ") :
      d.mode === "local" ? chalk.bold.yellow(" LOCAL ") :
      d.mode === "connecting" ? chalk.bold.yellow(" CONNECTING… ") :
      chalk.bold.red(" DISCONNECTED ");

    buf.push(moveTo(top, left) + ERASE_LINE + padEnd(` ${title}  ${modeBadge}`, width));

    // Linha 1: sessão, cliente, status
    const statusColor =
      d.status === "running" ? chalk.green(d.status) :
      d.status === "idle" ? chalk.gray(d.status) :
      chalk.yellow(d.status);

    const line1 = ` Sessão: ${chalk.white(d.sessionId)}  Cliente: ${chalk.white(d.clientId)}  Status: ${statusColor}  Modelo: ${chalk.cyan(d.model)}`;
    buf.push(moveTo(top + 1, left) + ERASE_LINE + padEnd(line1, width));

    // Linha 2: controles
    const pauseStr = d.paused ? chalk.bgYellow.black(" PAUSED ") : chalk.dim("paused: no");
    const focusStr = d.focusedBranchId != null ? chalk.white(String(d.focusedBranchId)) : chalk.dim("-");
    const winnerStr = d.winnerBranchId != null ? chalk.white(String(d.winnerBranchId)) : chalk.dim("-");
    const cpStr = d.lastCheckpoint !== "-" ? chalk.white(d.lastCheckpoint) : chalk.dim("-");
    const line2 = ` ${pauseStr}  Focus: ${focusStr}  Winner: ${winnerStr}  Checkpoint: ${cpStr}`;
    buf.push(moveTo(top + 2, left) + ERASE_LINE + padEnd(line2, width));

    // Linha 3: último aviso
    if (this.rect.height >= 4) {
      const notice = d.lastNotice ? chalk.bold(` ${d.lastNotice}`) : "";
      buf.push(moveTo(top + 3, left) + ERASE_LINE + padEnd(notice, width));
    }

    // Linha 4: separador
    if (this.rect.height >= 5) {
      buf.push(moveTo(top + 4, left) + chalk.cyan("─".repeat(width)));
    }
  }
}
