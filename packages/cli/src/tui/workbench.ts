/**
 * TUI — Workbench
 *
 * Layout manager para o painel de monitoramento ao vivo.
 * Calcula as dimensões dos 5 painéis a partir do tamanho actual do terminal.
 *
 * Layout (proporcional):
 *
 *  ┌─────────────────────────────────────────────────────────────┐
 *  │  ChannelPanel (20%)  │  MessagesPanel (50%)  │  EventsPanel │
 *  │                      │                       │   (30%)       │
 *  │                      ├───────────────────────┤              │
 *  │                      │  BranchTree (50%)     │              │
 *  ├──────────────────────┴───────────────────────┴──────────────┤
 *  │  Footer (1 linha de input)                                  │
 *  └─────────────────────────────────────────────────────────────┘
 */

export interface Rect {
  top: number;    // 1-indexed row
  left: number;   // 1-indexed col
  width: number;
  height: number;
}

export interface WorkbenchLayout {
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

  const footerRows = 2;
  const bodyRows = rows - footerRows;

  const channelCols = Math.floor(cols * 0.20);
  const eventsCols  = Math.floor(cols * 0.28);
  const centerCols  = cols - channelCols - eventsCols;

  const topRows    = Math.floor(bodyRows * 0.55);
  const bottomRows = bodyRows - topRows;

  const channelLeft  = 1;
  const centerLeft   = channelCols + 1;
  const eventsLeft   = channelCols + centerCols + 1;

  return {
    channels: { top: 1, left: channelLeft,  width: channelCols, height: bodyRows },
    messages: { top: 1, left: centerLeft,   width: centerCols,  height: topRows    },
    branch:   { top: topRows + 1, left: centerLeft, width: centerCols, height: bottomRows },
    events:   { top: 1, left: eventsLeft,   width: eventsCols,  height: bodyRows },
    footer:   { top: rows - footerRows + 1, left: 1, width: cols, height: footerRows },
  };
}
