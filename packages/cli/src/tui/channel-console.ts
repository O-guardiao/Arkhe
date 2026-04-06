/**
 * Channel Console — painel de awareness multichannel para o TUI.
 *
 * Exibe status de todos os canais registrados, permite envio cross-channel
 * e probe sob demanda sem depender de bibliotecas de UI externas.
 *
 * Migrado de rlm/cli/tui/channel_console.py
 */

export interface ChannelStatusApi {
  fetchChannelsStatus(): Promise<Record<string, unknown>>;
}

// ─────────────────────────────────────────────── Ícones de status

const ICON_RUNNING = "●";
const ICON_CONFIGURED = "◑";
const ICON_OFF = "○";
const ICON_ERROR = "✖";

type IconEntry = { icon: string; style: "running" | "error" | "configured" | "off" };

function channelIcon(snap: {
  running: boolean;
  healthy: boolean;
  configured: boolean;
}): IconEntry {
  if (snap.running && snap.healthy) return { icon: ICON_RUNNING, style: "running" };
  if (snap.running && !snap.healthy) return { icon: ICON_ERROR, style: "error" };
  if (snap.configured) return { icon: ICON_CONFIGURED, style: "configured" };
  return { icon: ICON_OFF, style: "off" };
}

// ─────────────────────────────────────────────── Snapshot de canal

export interface ChannelSnapshot {
  channelId: string;
  accountId: string;
  configured: boolean;
  running: boolean;
  healthy: boolean;
  identityName: string;
  lastError: string | null;
  reconnectAttempts: number;
  lastProbeMs: number;
  meta: Record<string, unknown>;
}

export function channelSnapshotFromDict(d: Record<string, unknown>): ChannelSnapshot {
  const ident = (d["identity"] ?? {}) as Record<string, unknown>;
  const name = String(ident["display_name"] ?? ident["username"] ?? "");
  return {
    channelId: String(d["channel_id"] ?? "?"),
    accountId: String(d["account_id"] ?? "default"),
    configured: Boolean(d["configured"]),
    running: Boolean(d["running"]),
    healthy: Boolean(d["healthy"]),
    identityName: name,
    lastError: d["last_error"] != null ? String(d["last_error"]) : null,
    reconnectAttempts: Number(d["reconnect_attempts"] ?? 0),
    lastProbeMs: Number(d["last_probe_ms"] ?? 0),
    meta: (d["meta"] ?? {}) as Record<string, unknown>,
  };
}

// ─────────────────────────────────────────────── Estado do painel

export interface ChannelConsoleState {
  snapshots: ChannelSnapshot[];
  lastFetchAt: number;
  lastSendResult: string;
  fetchError: string;
}

export function createChannelConsoleState(): ChannelConsoleState {
  return {
    snapshots: [],
    lastFetchAt: 0,
    lastSendResult: "",
    fetchError: "",
  };
}

// ─────────────────────────────────────────────── Renderização em texto

/** Estilo ANSI mínimo para exibição no terminal sem dependências externas. */
const ANSI = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  red: "\x1b[31m",
  magenta: "\x1b[35m",
  cyan: "\x1b[36m",
};

function styleIcon(entry: IconEntry): string {
  switch (entry.style) {
    case "running": return `${ANSI.bold}${ANSI.green}${entry.icon}${ANSI.reset}`;
    case "error": return `${ANSI.bold}${ANSI.red}${entry.icon}${ANSI.reset}`;
    case "configured": return `${ANSI.yellow}${entry.icon}${ANSI.reset}`;
    default: return `${ANSI.dim}${entry.icon}${ANSI.reset}`;
  }
}

/**
 * Constrói uma representação em texto do painel de canais.
 * Retorna um array de linhas prontas para `process.stdout.write`.
 */
export function buildChannelPanelLines(state: ChannelConsoleState): string[] {
  const lines: string[] = [];
  const border = `${ANSI.magenta}─────────────────────────────── Canais ───────────────────────────────${ANSI.reset}`;

  lines.push(border);

  if (!state.snapshots.length && !state.fetchError) {
    lines.push(`${ANSI.dim}  Nenhum canal registrado.${ANSI.reset}`);
  } else if (state.fetchError) {
    lines.push(`${ANSI.bold}${ANSI.red}  Erro: ${state.fetchError}${ANSI.reset}`);
  } else {
    // Cabeçalho
    lines.push(
      `  ${ANSI.bold}${"".padEnd(2)} ${"Canal".padEnd(12)} ${"Bot".padEnd(24)} ${"Lat".padStart(7)} ${"Err".padStart(4)}${ANSI.reset}`
    );

    for (const snap of state.snapshots) {
      const entry = channelIcon({
        running: snap.running,
        healthy: snap.healthy,
        configured: snap.configured,
      });
      const icon = styleIcon(entry);
      const latency = snap.lastProbeMs > 0 ? `${snap.lastProbeMs.toFixed(0)}ms` : "-";
      const errors = snap.reconnectAttempts ? String(snap.reconnectAttempts) : "-";
      let name = snap.identityName || "-";
      if (snap.lastError && !snap.healthy) {
        name = `${ANSI.red}${snap.lastError.slice(0, 24)}${ANSI.reset}`;
      }
      lines.push(
        `  ${icon} ${snap.channelId.padEnd(12)} ${name.padEnd(24)} ${latency.padStart(7)} ${errors.padStart(4)}`
      );
    }
  }

  // Linha de status
  const total = state.snapshots.length;
  const running = state.snapshots.filter((s) => s.running).length;
  const summaryParts: string[] = [];
  if (total) summaryParts.push(`${running}/${total} ativos`);
  if (state.lastFetchAt) {
    const age = Math.round((Date.now() / 1000) - state.lastFetchAt);
    summaryParts.push(`atualizado há ${age}s`);
  }
  if (summaryParts.length) {
    lines.push("");
    lines.push(`  ${ANSI.dim}${summaryParts.join(" · ")}${ANSI.reset}`);
  }

  if (state.lastSendResult) {
    lines.push(`  ${ANSI.bold}${state.lastSendResult}${ANSI.reset}`);
  }

  // Ajuda
  lines.push("");
  lines.push(
    `  ${ANSI.cyan}/channels${ANSI.reset}  atualizar status   ` +
    `${ANSI.cyan}/send <canal> <texto>${ANSI.reset}  cross-channel   ` +
    `${ANSI.cyan}/probe <canal>${ANSI.reset}  testar`
  );
  lines.push(border);

  return lines;
}

/**
 * Imprime o painel de canais no stdout.
 */
export function printChannelPanel(state: ChannelConsoleState): void {
  process.stdout.write(buildChannelPanelLines(state).join("\n") + "\n");
}

// ─────────────────────────────────────────────── Busca de snapshots

function extractSnapshots(data: Record<string, unknown>): ChannelSnapshot[] {
  const channels = (data["channels"] ?? {}) as Record<string, unknown>;
  const result: ChannelSnapshot[] = [];
  for (const accounts of Object.values(channels)) {
    if (Array.isArray(accounts)) {
      for (const acc of accounts) {
        result.push(channelSnapshotFromDict(acc as Record<string, unknown>));
      }
    } else if (typeof accounts === "object" && accounts !== null) {
      result.push(channelSnapshotFromDict(accounts as Record<string, unknown>));
    }
  }
  return result;
}

/** Busca snapshots via HTTP /api/channels/status (live mode). */
export async function fetchChannelSnapshotsLive(
  liveApi: ChannelStatusApi
): Promise<ChannelSnapshot[]> {
  const data = await liveApi.fetchChannelsStatus();
  return extractSnapshots(data);
}

/**
 * Atualiza o estado in-place — nunca lança exceção.
 * Suporta modo live (via LiveWorkbenchAPI) ou modo local (sem API).
 */
export async function refreshChannelState(
  state: ChannelConsoleState,
  opts: { liveApi?: ChannelStatusApi }
): Promise<void> {
  try {
    if (opts.liveApi) {
      state.snapshots = await fetchChannelSnapshotsLive(opts.liveApi);
    } else {
      // local mode: sem acesso ao Python backend — retorna vazio
      state.snapshots = [];
    }
    state.lastFetchAt = Date.now() / 1000;
    state.fetchError = "";
  } catch (err) {
    state.fetchError = String(err).slice(0, 120);
  }
}
