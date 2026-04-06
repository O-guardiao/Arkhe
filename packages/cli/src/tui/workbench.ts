/**
 * TUI — Workbench
 *
 * Layout manager para o painel de monitoramento ao vivo.
 * Calcula as dimensões dos 6 painéis a partir do tamanho actual do terminal.
 *
 * Layout (proporcional) — fiel ao Python workbench.py:
 *
 *  ┌─────────────────────────────────────────────────────────────┐
 *  │  Header (5 linhas — sessão, status, controles)             │
 *  ├──────────────┬───────────────────────┬──────────────────────┤
 *  │  Channels    │  Messages + Timeline  │  Events + Comandos  │
 *  │   (20%)      │       (52%)           │      (28%)          │
 *  │              ├───────────────────────┤                     │
 *  │              │  BranchTree           │                     │
 *  ├──────────────┴───────────────────────┴──────────────────────┤
 *  │  Footer (3 linhas — status, input, notice)                 │
 *  └─────────────────────────────────────────────────────────────┘
 */

export interface Rect {
  top: number;    // 1-indexed row
  left: number;   // 1-indexed col
  width: number;
  height: number;
}

export interface WorkbenchLayout {
  header: Rect;
  channels: Rect;
  messages: Rect;
  events: Rect;
  branch: Rect;
  footer: Rect;
}

/** Retorna o layout calculado para o tamanho actual do terminal. */
export function computeLayout(): WorkbenchLayout {
  const cols = process.stdout.columns ?? 120;
  const rows = process.stdout.rows ?? 30;

  const headerRows = 5;
  const footerRows = 3;
  const bodyRows = Math.max(rows - headerRows - footerRows, 4);

  const channelCols = Math.floor(cols * 0.20);
  const eventsCols  = Math.floor(cols * 0.28);
  const centerCols  = cols - channelCols - eventsCols;

  const topRows    = Math.floor(bodyRows * 0.55);
  const bottomRows = bodyRows - topRows;

  const bodyTop = headerRows + 1;

  const channelLeft  = 1;
  const centerLeft   = channelCols + 1;
  const eventsLeft   = channelCols + centerCols + 1;

  return {
    header:   { top: 1, left: 1, width: cols, height: headerRows },
    channels: { top: bodyTop, left: channelLeft,  width: channelCols, height: bodyRows },
    messages: { top: bodyTop, left: centerLeft,   width: centerCols,  height: topRows    },
    branch:   { top: bodyTop + topRows, left: centerLeft, width: centerCols, height: bottomRows },
    events:   { top: bodyTop, left: eventsLeft,   width: eventsCols,  height: bodyRows },
    footer:   { top: rows - footerRows + 1, left: 1, width: cols, height: footerRows },
  };
}
