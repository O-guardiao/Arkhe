/**
 * TUI — Workbench
 *
 * Layout manager para o painel de monitoramento ao vivo.
 * Calcula as dimensões dos 6 painéis a partir do tamanho actual do terminal.
 *
 * Layout — fiel ao Python workbench.py:
 *
 *  ┌─────────────────────────────────────────────────────────────┐
 *  │  Header (5 linhas — sessão, status, controles)             │
 *  ├──────────────┬───────────────────────┬──────────────────────┤
 *  │  Branches    │  Messages + Timeline  │  Events              │
 *  │   (38 cols)  │       (ratio 2)       │                      │
 *  │              │                       ├──────────────────────┤
 *  │              │                       │  Channels            │
 *  ├──────────────┴───────────────────────┴──────────────────────┤
 *  │  Footer (5 linhas — notice, estado, input)                 │
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
  branch: Rect;
  messages: Rect;
  events: Rect;
  channels: Rect;
  footer: Rect;
}

/** Retorna o layout calculado para o tamanho actual do terminal. */
export function computeLayout(): WorkbenchLayout {
  const cols = process.stdout.columns ?? 120;
  const rows = process.stdout.rows ?? 30;

  const headerRows = 5;
  const footerRows = 5;
  const bodyRows = Math.max(rows - headerRows - footerRows, 4);

  let branchCols = 38;
  let rightCols = 48;
  const minCenterCols = 28;

  if (branchCols + rightCols + minCenterCols > cols) {
    const overflow = branchCols + rightCols + minCenterCols - cols;
    const shrinkBranch = Math.min(branchCols - 24, Math.ceil(overflow / 2));
    branchCols -= Math.max(shrinkBranch, 0);
    const remainingOverflow = branchCols + rightCols + minCenterCols - cols;
    const shrinkRight = Math.min(rightCols - 30, Math.max(remainingOverflow, 0));
    rightCols -= Math.max(shrinkRight, 0);
  }

  const centerCols = Math.max(cols - branchCols - rightCols, minCenterCols);

  const eventsRows = Math.max(Math.floor(bodyRows * (2 / 3)), 8);
  const channelsRows = Math.max(bodyRows - eventsRows, 6);

  const bodyTop = headerRows + 1;

  const branchLeft = 1;
  const centerLeft = branchCols + 1;
  const rightLeft = branchCols + centerCols + 1;

  return {
    header:   { top: 1, left: 1, width: cols, height: headerRows },
    branch:   { top: bodyTop, left: branchLeft, width: branchCols, height: bodyRows },
    messages: { top: bodyTop, left: centerLeft, width: centerCols, height: bodyRows },
    events:   { top: bodyTop, left: rightLeft, width: rightCols, height: eventsRows },
    channels: { top: bodyTop + eventsRows, left: rightLeft, width: rightCols, height: channelsRows },
    footer:   { top: rows - footerRows + 1, left: 1, width: cols, height: footerRows },
  };
}
