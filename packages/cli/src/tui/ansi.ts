/**
 * Utilitários ANSI para o TUI.
 *
 * Centraliza todas as sequências de escape para que os painéis
 * não dependam directamente de `process.stdout.write`.
 */

/** Move o cursor para (row, col) — 1-indexed. */
export function moveTo(row: number, col: number): string {
  return `\x1b[${row};${col}H`;
}

/** Apaga do cursor até ao fim da linha. */
export const ERASE_LINE = "\x1b[K";

/** Apaga o ecrã inteiro e move cursor para (1,1). */
export const CLEAR_SCREEN = "\x1b[2J\x1b[H";

/** Esconde o cursor. */
export const HIDE_CURSOR = "\x1b[?25l";

/** Mostra o cursor. */
export const SHOW_CURSOR = "\x1b[?25h";

/** Activa modo de ecrã alternativo (buffer secundário). */
export const ENTER_ALT = "\x1b[?1049h";

/** Volta ao ecrã principal. */
export const EXIT_ALT = "\x1b[?1049l";

/** Trunca a string para caber em `width` colunas (considera apenas ASCII). */
export function truncate(s: string, width: number): string {
  // Remove códigos ANSI para calcular comprimento visual
  // eslint-disable-next-line no-control-regex
  const visible = s.replace(/\x1b\[[0-9;]*m/g, "");
  if (visible.length <= width) return s;
  // Trunca pela diferença entre comprimento bruto e códigos ANSI
  const extra = s.length - visible.length;
  return s.slice(0, width + extra - 1) + "…";
}

/** Padding direito com espaços até atingir `width` caracteres visíveis. */
export function padEnd(s: string, width: number): string {
  // eslint-disable-next-line no-control-regex
  const visible = s.replace(/\x1b\[[0-9;]*m/g, "");
  const pad = Math.max(0, width - visible.length);
  return s + " ".repeat(pad);
}

/** Desenha uma linha horizontal simples. */
export function hline(col: number, width: number, char = "─"): string {
  return moveTo(0, col) + char.repeat(width); // moveTo é sobrescrito pelo chamador
}

/** Gera um bloco de texto formatado dentro de um rectângulo. */
export function renderBox(
  buf: string[],
  row: number,
  col: number,
  width: number,
  lines: string[],
): void {
  for (let i = 0; i < lines.length; i++) {
    const line = truncate(lines[i] ?? "", width);
    buf.push(moveTo(row + i, col) + padEnd(line, width));
  }
}
