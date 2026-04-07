/**
 * TUI App — orquestrador do painel ao vivo do RLM.
 *
 * Migrado com fidelidade de rlm/cli/commands/workbench.py:
 *  1. Probe /health antes de criar sessão (como Python faz)
 *  2. Retry periódico se sessão falhar (Python re-cria a cada render)
 *  3. Header panel com metadados da sessão (Python: _build_header)
 *  4. Comandos completos: /help /channels /watch /quit /exit
 *  5. WS events + HTTP polling (adição TypeScript não existente em Python)
 *  6. ANSI rendering direto (TypeScript) vs Rich (Python)
 *
 * Uso:
 *   const app = new TuiApp({ gatewayUrl, token });
 *   await app.run();  // bloqueia até 'q' ou SIGINT
 */

import readline from "node:readline";
import { EventEmitter } from "node:events";
import { WsEventClient } from "../lib/ws-client.js";
import { computeLayout } from "./workbench.js";
import { ChannelPanel, type ChannelStatus } from "./channel-panel.js";
import { MessagesPanel, type MessageEntry, type TimelineEntry } from "./messages-panel.js";
import { EventsPanel } from "./events-panel.js";
import { BranchTree } from "./branch-tree.js";
import { Footer } from "./footer.js";
import { HeaderPanel, type HeaderData } from "./header-panel.js";
import { fetchChannelSnapshotsLive } from "./channel-console.js";
import type { LiveSessionInfo } from "./live-api.js";
import {
  ENTER_ALT, EXIT_ALT,
  HIDE_CURSOR, SHOW_CURSOR,
  CLEAR_SCREEN,
} from "./ansi.js";

const FPS = 30;
const FRAME_MS = Math.floor(1000 / FPS);
const DEFAULT_CLIENT_ID = "tui:default";
const DEFAULT_REFRESH_INTERVAL_SECONDS = 0.75;
/** Intervalo de retry para criação de sessão quando falha. */
const SESSION_RETRY_MS = 5_000;

interface TuiApplyCommandOptions {
  clientId: string;
  commandType: string;
  payload: Record<string, unknown>;
  branchId: number | null;
}

interface OperatorCommand {
  commandType: string;
  payload: Record<string, unknown>;
  branchId: number | null;
}

export interface TuiLiveApi {
  ensureSession(clientId: string): Promise<LiveSessionInfo>;
  dispatchPrompt(sessionId: string, clientId: string, text: string): Promise<Record<string, unknown>>;
  fetchChannelsStatus(): Promise<Record<string, unknown>>;
  fetchActivity(sessionId: string): Promise<Record<string, unknown>>;
  applyCommand(sessionId: string, opts: TuiApplyCommandOptions): Promise<Record<string, unknown>>;
  probeChannel(channelId: string): Promise<Record<string, unknown>>;
  crossChannelSend(targetClientId: string, message: string): Promise<Record<string, unknown>>;
}

export interface TuiAppOptions {
  /** URL base do gateway (ex: http://localhost:3000) */
  gatewayUrl: string;
  /** Token de autenticação */
  token: string;
  /** Client id da sessão do operador. */
  clientId?: string;
  /** Intervalo de refresh auxiliar do modo live. */
  refreshIntervalSeconds?: number;
  /** Renderiza uma vez e encerra. */
  once?: boolean;
  /** Backend vivo para sessão do operador e polling HTTP. */
  liveApi?: TuiLiveApi;
}

export function normalizeClientId(value: string | undefined): string {
  const trimmed = value?.trim();
  return trimmed ? trimmed : DEFAULT_CLIENT_ID;
}

export function normalizeRefreshIntervalSeconds(value: number | undefined): number {
  if (value == null || !Number.isFinite(value) || value <= 0) {
    return DEFAULT_REFRESH_INTERVAL_SECONDS;
  }
  return value;
}

interface LiveSessionRef {
  sessionId: string;
  clientId: string;
}

function nowHms(): string {
  return new Date().toTimeString().slice(0, 8);
}

function formatClockValue(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return new Date(value).toTimeString().slice(0, 8);
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toTimeString().slice(0, 8);
    }
    if (value.length >= 19 && value[10] === "T") {
      return value.slice(11, 19);
    }
  }
  return nowHms();
}

function channelStatusFromSnapshot(snapshot: {
  running: boolean;
  healthy: boolean;
  configured: boolean;
}): ChannelStatus {
  if (snapshot.running && snapshot.healthy) return "active";
  if (snapshot.running && !snapshot.healthy) return "error";
  if (snapshot.configured) return "inactive";
  return "inactive";
}

export class TuiApp {
  private client: WsEventClient;
  private readonly clientId: string;
  private readonly refreshIntervalMs: number;
  private readonly once: boolean;
  private readonly liveApi: TuiLiveApi | undefined;
  private readonly lifecycle = new EventEmitter();

  private headerPanel!: HeaderPanel;
  private channelPanel!: ChannelPanel;
  private messagesPanel!: MessagesPanel;
  private eventsPanel!: EventsPanel;
  private branchTree!: BranchTree;
  private footer!: Footer;

  private liveSession: LiveSessionRef | null = null;
  private dirty = true;
  private frameTimer: ReturnType<typeof setInterval> | undefined;
  private channelRefreshTimer: ReturnType<typeof setInterval> | undefined;
  private activityRefreshTimer: ReturnType<typeof setInterval> | undefined;
  private sessionRetryTimer: ReturnType<typeof setInterval> | undefined;
  private running = false;
  private lastChannelRefreshError = "";
  private lastActivityTs = 0;
  private readonly seenActivityKeys = new Set<string>();
  /** Fiel ao Python: última mensagem de status exibida no header. */
  private lastNotice = "Use /help para ver os comandos do operador.";

  constructor(private opts: TuiAppOptions) {
    this.client = new WsEventClient(opts.gatewayUrl, opts.token);
    this.clientId = normalizeClientId(opts.clientId);
    this.refreshIntervalMs = Math.round(normalizeRefreshIntervalSeconds(opts.refreshIntervalSeconds) * 1000);
    this.once = opts.once ?? false;
    this.liveApi = opts.liveApi;
  }

  // -------------------------------------------------------------------------
  // Public
  // -------------------------------------------------------------------------

  async run(): Promise<void> {
    this._initPanels();

    // Fiel ao Python: probe /health antes de tentar sessão
    await this._bootstrapLiveMode();
    this._enterAlt();

    if (this.once) {
      this._render();
      this._cleanup();
      return;
    }

    this._setupInput();
    this._subscribeEvents();
    this._startRefreshTimers();

    this.client.connect();
    this.running = true;

    // Render loop
    await new Promise<void>((resolve) => {
      this.frameTimer = setInterval(() => {
        if (this.dirty) {
          this._render();
          this.dirty = false;
        }
      }, FRAME_MS);

      // Promise resolve quando sair
      this.lifecycle.once("exit", resolve);
    });

    this._cleanup();
  }

  // -------------------------------------------------------------------------
  // Init
  // -------------------------------------------------------------------------

  private _initPanels(): void {
    const layout = computeLayout();
    this.headerPanel   = new HeaderPanel(layout.header);
    this.channelPanel  = new ChannelPanel(layout.channels);
    this.messagesPanel = new MessagesPanel(layout.messages);
    this.eventsPanel   = new EventsPanel(layout.events);
    this.branchTree    = new BranchTree(layout.branch);
    this.footer        = new Footer(layout.footer);

    // Inicializa header com dados conhecidos
    this.headerPanel.update({
      clientId: this.clientId,
      mode: "disconnected",
      lastNotice: this.lastNotice,
    });
    this.footer.updateRuntime({
      lastNotice: this.lastNotice,
      refreshIntervalSeconds: this.refreshIntervalMs / 1000,
    });

    // Recalcula layout se o terminal for redimensionado
    process.stdout.on("resize", () => {
      const l = computeLayout();
      this.headerPanel.updateRect(l.header);
      this.channelPanel.updateRect(l.channels);
      this.messagesPanel.updateRect(l.messages);
      this.eventsPanel.updateRect(l.events);
      this.branchTree.updateRect(l.branch);
      this.footer.updateRect(l.footer);
      this.dirty = true;
    });
  }

  private _enterAlt(): void {
    process.stdout.write(ENTER_ALT + HIDE_CURSOR + CLEAR_SCREEN);
  }

  private _cleanup(): void {
    if (this.frameTimer !== undefined) {
      clearInterval(this.frameTimer);
    }
    if (this.channelRefreshTimer !== undefined) {
      clearInterval(this.channelRefreshTimer);
    }
    if (this.activityRefreshTimer !== undefined) {
      clearInterval(this.activityRefreshTimer);
    }
    if (this.sessionRetryTimer !== undefined) {
      clearInterval(this.sessionRetryTimer);
    }
    this.client.disconnect();
    if (process.stdin.isTTY && typeof process.stdin.setRawMode === "function") {
      process.stdin.setRawMode(false);
    }
    process.stdin.pause();
    process.stdout.write(EXIT_ALT + SHOW_CURSOR);
    this.running = false;
  }

  /**
   * Fiel ao Python run_workbench():
   *  1. Probe /health primeiro
   *  2. Se acessível, cria sessão
   *  3. Se falhar, marca como disconnected e agenda retry
   */
  private async _bootstrapLiveMode(): Promise<void> {
    if (!this.liveApi) return;

    // Probe como Python faz: live_api.probe()
    const probeOk = await this._probeLive();
    if (!probeOk) {
      this._setNotice("Servidor indisponível — tentando reconexão periódica...");
      this.headerPanel.update({ mode: "disconnected" });
      return;
    }

    const hasLiveSession = await this._ensureLiveSession();
    if (hasLiveSession) {
      await this._refreshChannels(true);
    }
  }

  /** Probe /health — fiel a live_api.py probe(). */
  private async _probeLive(): Promise<boolean> {
    if (!this.liveApi) return false;
    try {
      // LiveWorkbenchAPI tem probe() — usar se disponível
      if ("probe" in this.liveApi && typeof (this.liveApi as any).probe === "function") {
        return await (this.liveApi as any).probe();
      }
      // Fallback: testar fetchChannelsStatus
      await this.liveApi.fetchChannelsStatus();
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Inicia timers de refresh — channels, activity, e retry de sessão.
   * Fiel ao Python: _fetch_activity() roda a cada refresh_interval.
   */
  private _startRefreshTimers(): void {
    if (!this.liveApi) return;

    // Channel polling — Python: refresh_channel_state() a cada render
    this.channelRefreshTimer = setInterval(() => {
      void this._refreshChannels(false);
    }, this.refreshIntervalMs);

    // Activity polling — Python: _fetch_activity() a cada render
    this.activityRefreshTimer = setInterval(() => {
      void this._refreshActivity();
    }, this.refreshIntervalMs);

    // Session retry — se sessão falhou, tentar novamente periodicamente
    this.sessionRetryTimer = setInterval(() => {
      if (!this.liveSession) {
        void this._ensureLiveSession();
      }
    }, SESSION_RETRY_MS);
  }

  private async _ensureLiveSession(): Promise<boolean> {
    if (!this.liveApi) return false;
    if (this.liveSession) return true;

    try {
      const info = await this.liveApi.ensureSession(this.clientId);
      this.liveSession = {
        sessionId: info.session_id,
        clientId: info.client_id,
      };
      // Fiel ao Python: atualiza header com dados da sessão
      this.headerPanel.update({
        sessionId: info.session_id,
        clientId: info.client_id,
        status: info.status || "idle",
        mode: "live",
      });
      this._setNotice(`Sessão viva: ${info.session_id}`);
      this._pushSystemMessage(`Sessão viva: ${info.session_id}`);
      return true;
    } catch (error) {
      const msg = `Workbench live indisponível: ${this._errorMessage(error)}`;
      this._pushSystemMessage(msg);
      this.headerPanel.update({ mode: "disconnected" });
      return false;
    }
  }

  private async _refreshChannels(notifyOnError: boolean): Promise<void> {
    if (!this.liveApi) return;

    try {
      const snapshots = await fetchChannelSnapshotsLive(this.liveApi);
      this.channelPanel.sync(
        snapshots.map((snapshot) => ({
          name: snapshot.channelId,
          status: channelStatusFromSnapshot(snapshot),
          identityName: snapshot.identityName,
          lastProbeMs: snapshot.lastProbeMs,
          reconnectAttempts: snapshot.reconnectAttempts,
          lastError: snapshot.lastError,
          configured: snapshot.configured,
        }))
      );
      this.lastChannelRefreshError = "";
      this.dirty = true;
    } catch (error) {
      const message = `Falha ao atualizar canais: ${this._errorMessage(error)}`;
      if (notifyOnError || message !== this.lastChannelRefreshError) {
        this._pushSystemMessage(message);
      }
      this.lastChannelRefreshError = message;
    }
  }

  private async _refreshActivity(): Promise<void> {
    if (!this.liveApi || !this.liveSession) return;

    try {
      const data = await this.liveApi.fetchActivity(this.liveSession.sessionId);

      const sessionData = (data["session"] ?? data) as Record<string, unknown>;
      const runtime = (data["runtime"] ?? {}) as Record<string, unknown>;
      const controls = (runtime["controls"] ?? {}) as Record<string, unknown>;
      const coordination = (runtime["coordination"] ?? {}) as Record<string, unknown>;
      const summary = ((coordination["latest_parallel_summary"]) ?? {}) as Record<string, unknown>;
      const sessionMetadata = (sessionData["metadata"] ?? {}) as Record<string, unknown>;

      this.headerPanel.update({
        sessionId: String(sessionData["session_id"] ?? this.liveSession.sessionId),
        clientId: String(sessionData["client_id"] ?? this.liveSession.clientId),
        status: String(sessionData["status"] ?? "idle"),
        mode: "live",
        paused: Boolean(controls["paused"]),
        focusedBranchId: controls["focused_branch_id"] != null ? controls["focused_branch_id"] as number : null,
        winnerBranchId: summary["winner_branch_id"] != null ? summary["winner_branch_id"] as number : null,
        lastCheckpoint: String(controls["last_checkpoint_path"] ?? "-"),
      });

      this.footer.updateRuntime({
        lastNotice: this.lastNotice,
        pauseReason: String(controls["pause_reason"] ?? ""),
        operatorNote: String(controls["last_operator_note"] ?? ""),
        stateDir: String(sessionData["state_dir"] ?? ""),
        refreshIntervalSeconds: this.refreshIntervalMs / 1000,
      });
      this.eventsPanel.setLatestResponse(String(sessionMetadata["last_operator_response"] ?? "-"));

      const recursiveSession = (runtime["recursive_session"] ?? {}) as Record<string, unknown>;
      const runtimeMessages = ((recursiveSession["messages"] ?? []) as Array<Record<string, unknown>>)
        .map((message) => ({
          ts: formatClockValue(message["timestamp"] ?? message["created_at"] ?? message["ts"]),
          role: this._normalizeMessageRole(message["role"]),
          channel: String(message["channel"] ?? "runtime"),
          text: String(message["content"] ?? message["text"] ?? ""),
        }))
        .filter((message) => message.text.trim().length > 0);
      const timelineEntries = ((runtime["timeline"] ?? {}) as Record<string, unknown>)["entries"] as Array<Record<string, unknown>> | undefined;
      const runtimeTimeline: TimelineEntry[] = (timelineEntries ?? [])
        .map((entry) => ({
          kind: String(entry["event_type"] ?? entry["kind"] ?? "-"),
          summary: this._summarizeTimelineEntry(entry),
        }))
        .filter((entry) => entry.summary.trim().length > 0);
      this.messagesPanel.setRuntimeSnapshot(runtimeMessages, runtimeTimeline);

      const eventLog = (data["event_log"] ?? []) as Array<Record<string, unknown>>;
      for (const item of eventLog) {
        const payload = (item["payload"] ?? {}) as Record<string, unknown>;
        const eventType = String(item["event_type"] ?? item["type"] ?? "event");
        const kind = eventType.includes("error") ? "error" : eventType.includes("command") ? "command" : "event";
        this._recordEvent(
          "session",
          kind,
          `${eventType}: ${this._summarizePayload(payload)}`,
          formatClockValue(item["timestamp"] ?? item["created_at"] ?? item["ts"]),
        );
      }

      for (const item of ((recursiveSession["events"] ?? []) as Array<Record<string, unknown>>)) {
        const payload = (item["payload"] ?? {}) as Record<string, unknown>;
        const eventType = String(item["event_type"] ?? item["type"] ?? "runtime");
        const kind = eventType.includes("error") ? "error" : eventType.includes("tool") ? "tool_call" : "event";
        this._recordEvent(
          "runtime",
          kind,
          `${eventType}: ${this._summarizePayload(payload)}`,
          formatClockValue(item["timestamp"] ?? item["created_at"] ?? item["ts"]),
        );
      }

      for (const item of ((coordination["events"] ?? []) as Array<Record<string, unknown>>)) {
        const payloadPreview = String(item["payload_preview"] ?? item["topic"] ?? this._summarizePayload(item));
        this._recordEvent(
          "coord",
          "event",
          `${String(item["operation"] ?? "coord")}: ${payloadPreview}`,
          formatClockValue(item["timestamp"] ?? item["created_at"] ?? item["ts"]),
        );
      }

      const events = (data["events"] ?? []) as Array<{
        type?: string;
        payload?: Record<string, unknown>;
        ts?: number;
        eventId?: string;
      }>;

      for (const evt of events) {
        if (evt.ts && evt.ts <= this.lastActivityTs) continue;
        this._applyGatewayEvent(evt.type ?? "", evt.payload ?? {}, formatClockValue(evt.ts));

        if (evt.ts) {
          this.lastActivityTs = Math.max(this.lastActivityTs, evt.ts);
        }
      }

      if (runtime["tasks"]) {
        const tasks = runtime["tasks"] as Record<string, unknown>;
        const current = (tasks["current"] ?? {}) as Record<string, unknown>;
        if (current["title"]) {
          this._setNotice(`Task: ${current["title"]} [${current["status"] ?? "-"}]`);
        }
      }

      // Processa branch_tasks do runtime (Python: _build_branches_panel)
      const branchTasks = ((coordination["branch_tasks"]) ?? []) as Array<Record<string, unknown>>;
      for (const bt of branchTasks) {
        this.branchTree.upsert({
          id: String(bt["branch_id"] ?? ""),
          label: `${bt["title"] ?? "sem titulo"} | ${bt["mode"] ?? "-"} | ${bt["status"] ?? "-"}`,
          status: bt["status"] === "done" ? "ok" : bt["status"] === "error" ? "error" : "running",
        });
      }

      this.dirty = true;
    } catch {
      // Silent — activity polling failure is not critical
    }
  }
  // -------------------------------------------------------------------------
  // Input
  // -------------------------------------------------------------------------

  private _setupInput(): void {
    readline.emitKeypressEvents(process.stdin);
    if (process.stdin.isTTY) process.stdin.setRawMode(true);

    process.stdin.on("keypress", (_chunk, key: readline.Key) => {
      if (!key) return;

      // Sair
      if (key.name === "q" || (key.ctrl && key.name === "c")) {
        this.lifecycle.emit("exit");
        return;
      }

      // Navegação
      if (key.name === "up") { this.channelPanel.moveUp(); this.dirty = true; return; }
      if (key.name === "down") { this.channelPanel.moveDown(); this.dirty = true; return; }

      // Pausar/retomar
      if (key.name === "p") { this.footer.togglePause(); this.dirty = true; return; }

      // Limpar
      if (key.name === "c" && !key.ctrl) {
        this.dirty = true;
        return;
      }

      // Input de prompt
      if (key.name === "return") {
        const text = this.footer.flushInput();
        if (text) void this._sendPrompt(text);
        this.dirty = true;
        return;
      }
      if (key.name === "backspace") {
        this.footer.backspace();
        this.dirty = true;
        return;
      }
      if (!key.ctrl && !key.meta && key.sequence) {
        this.footer.typeChar(key.sequence);
        this.dirty = true;
      }
    });

    process.stdin.resume();
  }

  private async _sendPrompt(text: string): Promise<void> {
    if (text.startsWith("/")) {
      await this._handleSlashCommand(text);
      return;
    }

    const channel = this.channelPanel.selectedChannel() ?? "webchat";
    // Exibe localmente imediatamente
    this.messagesPanel.push({
      ts: nowHms(),
      role: "user",
      channel,
      text,
    });
    this.dirty = true;

    const hasLiveSession = await this._ensureLiveSession();
    if (!hasLiveSession || !this.liveApi || !this.liveSession) {
      this._pushSystemMessage("Prompt não enviado: servidor vivo indisponível.");
      return;
    }

    try {
      await this.liveApi.dispatchPrompt(this.liveSession.sessionId, this.liveSession.clientId, text);
    } catch (error) {
      this._pushSystemMessage(`Falha ao enviar prompt: ${this._errorMessage(error)}`);
    }
  }

  private async _handleSlashCommand(text: string): Promise<void> {
    const parts = text.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) {
      return;
    }

    const command = parts[0]?.toLowerCase() ?? "";

    // ── Comandos que não precisam de sessão viva ──────────────────

    // /help — fiel ao Python _handle_operator_command
    if (command === "/help") {
      this._setNotice(
        "Comandos: /pause, /resume, /checkpoint, /focus, /winner, " +
        "/priority, /note, /watch, /channels, /send, /probe, /quit"
      );
      return;
    }

    // /quit ou /exit — fiel ao Python
    if (command === "/quit" || command === "/exit") {
      this.lifecycle.emit("exit");
      return;
    }

    // /channels — fiel ao Python: refresh_channel_state + contagem
    if (command === "/channels") {
      await this._refreshChannels(true);
      this._setNotice("Canais atualizados.");
      return;
    }

    // /watch [seconds] — fiel ao Python: watch_until_idle
    if (command === "/watch") {
      const durationS = parts.length > 1 ? Number.parseFloat(parts[1] ?? "0") : undefined;
      void this._watchUntilIdle(Number.isFinite(durationS) ? durationS : undefined);
      return;
    }

    // ── Comandos que precisam de sessão viva ──────────────────────

    const hasLiveSession = await this._ensureLiveSession();
    if (!hasLiveSession || !this.liveApi || !this.liveSession) {
      this._setNotice("Comando não enviado: servidor vivo indisponível.");
      this._pushSystemMessage("Comando não enviado: servidor vivo indisponível.");
      return;
    }

    try {
      if (command === "/probe") {
        if (parts.length < 2) {
          throw new Error("Uso: /probe <channel_id>");
        }
        const channelId = parts[1] ?? "";
        const result = await this.liveApi.probeChannel(channelId);
        this._setNotice(`Probe ${channelId}: ${String(result["status"] ?? "ok")}`);
        await this._refreshChannels(false);
        return;
      }

      if (command === "/send") {
        if (parts.length < 3) {
          throw new Error("Uso: /send <target_client_id> <mensagem>");
        }
        const targetClientId = parts[1] ?? "";
        const message = parts.slice(2).join(" ");
        const result = await this.liveApi.crossChannelSend(targetClientId, message);
        this._setNotice(
          `Enviado para ${targetClientId}: ${String(result["status"] ?? "ok")}`
        );
        return;
      }

      const translated = this._translateOperatorCommand(parts);
      const result = await this.liveApi.applyCommand(this.liveSession.sessionId, {
        clientId: this.liveSession.clientId,
        commandType: translated.commandType,
        payload: translated.payload,
        branchId: translated.branchId,
      });
      const commandInfo = (result["command"] ?? {}) as Record<string, unknown>;
      const commandType = String(commandInfo["command_type"] ?? translated.commandType);
      const commandId = String(commandInfo["command_id"] ?? "?");
      this._setNotice(`Comando aplicado: ${commandType}#${commandId}`);
    } catch (error) {
      this._setNotice(this._errorMessage(error));
      this._pushSystemMessage(this._errorMessage(error));
    }
  }

  private _translateOperatorCommand(parts: string[]): OperatorCommand {
    const command = (parts[0] ?? "").toLowerCase();

    if (command === "/pause") {
      return { commandType: "pause_runtime", payload: { reason: parts.slice(1).join(" ").trim() }, branchId: null };
    }
    if (command === "/resume") {
      return { commandType: "resume_runtime", payload: { reason: parts.slice(1).join(" ").trim() }, branchId: null };
    }
    if (command === "/checkpoint") {
      const checkpointName = parts[1] ?? `tui-${Date.now()}`;
      return { commandType: "create_checkpoint", payload: { checkpoint_name: checkpointName }, branchId: null };
    }
    if (command === "/focus") {
      if (parts.length < 2) {
        throw new Error("/focus exige branch_id");
      }
      return {
        commandType: "focus_branch",
        payload: { note: parts.slice(2).join(" ").trim() },
        branchId: this._parseBranchId(parts[1]),
      };
    }
    if (command === "/winner") {
      if (parts.length < 2) {
        throw new Error("/winner exige branch_id");
      }
      return {
        commandType: "fix_winner_branch",
        payload: { note: parts.slice(2).join(" ").trim() },
        branchId: this._parseBranchId(parts[1]),
      };
    }
    if (command === "/priority") {
      if (parts.length < 3) {
        throw new Error("/priority exige branch_id e prioridade");
      }
      const branchId = this._parseBranchId(parts[1]);
      const priority = this._parseBranchId(parts[2]);
      return {
        commandType: "reprioritize_branch",
        payload: { priority, reason: parts.slice(3).join(" ").trim() },
        branchId,
      };
    }
    if (command === "/note") {
      const note = parts.slice(1).join(" ").trim();
      if (!note) {
        throw new Error("/note exige texto");
      }
      return { commandType: "operator_note", payload: { note }, branchId: null };
    }

    throw new Error(`Comando desconhecido: ${parts[0] ?? ""}`);
  }

  private _parseBranchId(value: string | undefined): number {
    const parsed = Number.parseInt(value ?? "", 10);
    if (!Number.isFinite(parsed)) {
      throw new Error("branch_id inválido");
    }
    return parsed;
  }

  /**
   * Fiel ao Python: watch_until_idle(duration_s=None).
   * Poll activity a cada refreshInterval até todas as tasks estarem "done"
   * ou o timeout expirar.
   */
  private async _watchUntilIdle(durationS?: number): Promise<void> {
    this._setNotice(`Observando atividade${durationS != null ? ` por ${durationS}s` : ""}...`);
    const deadline = durationS != null ? Date.now() + durationS * 1000 : Number.POSITIVE_INFINITY;

    const poll = async (): Promise<boolean> => {
      if (!this.liveApi || !this.liveSession) return true; // sem sessão = para de esperar
      try {
        const data = await this.liveApi.fetchActivity(this.liveSession.sessionId);
        const sessionData = (data["session"] ?? data) as Record<string, unknown>;
        const status = String(sessionData["status"] ?? "idle");
        // Idle ou completed = trabalho terminou
        return status === "idle" || status === "completed" || status === "done";
      } catch {
        return false;
      }
    };

    const step = (): Promise<void> =>
      new Promise<void>((resolve) => {
        const timer = setInterval(async () => {
          if (Date.now() >= deadline) {
            clearInterval(timer);
            this._setNotice("Watch: timeout atingido.");
            resolve();
            return;
          }
          const isIdle = await poll();
          if (isIdle) {
            clearInterval(timer);
            this._setNotice("Watch: runtime em idle.");
            resolve();
          }
        }, this.refreshIntervalMs);
      });

    await step();
  }

  /** Fiel ao Python: self.last_notice — actualiza header. */
  private _setNotice(text: string): void {
    this.lastNotice = text;
    this.headerPanel.update({ lastNotice: text });
    this.footer.updateRuntime({ lastNotice: text });
    this.dirty = true;
  }

  private _pushSystemMessage(text: string): void {
    this.messagesPanel.push({
      ts: nowHms(),
      role: "system",
      channel: "tui",
      text,
    });
    this.dirty = true;
  }

  private _errorMessage(error: unknown): string {
    if (error instanceof Error) {
      return error.message;
    }
    return String(error);
  }

  private _normalizeMessageRole(value: unknown): MessageEntry["role"] {
    const role = String(value ?? "").toLowerCase();
    if (role === "assistant" || role === "agent") {
      return "agent";
    }
    if (role === "system" || role === "tool") {
      return "system";
    }
    return "user";
  }

  private _summarizePayload(payload: Record<string, unknown>): string {
    const fields = [
      payload["text_preview"],
      payload["response_preview"],
      payload["error"],
      payload["command_type"],
      payload["message"],
      payload["note"],
      payload["text"],
      payload["content"],
    ];
    for (const field of fields) {
      const value = String(field ?? "").trim();
      if (value) {
        return value;
      }
    }
    return JSON.stringify(payload).slice(0, 120);
  }

  private _summarizeTimelineEntry(entry: Record<string, unknown>): string {
    const summary = entry["summary"] ?? entry["message"] ?? entry["title"] ?? entry["payload_preview"] ?? entry["payload"];
    if (typeof summary === "string") {
      return summary;
    }
    return JSON.stringify(summary ?? {}).slice(0, 120);
  }

  private _recordEvent(source: string, kind: string, label: string, ts: string): void {
    const key = `${source}:${kind}:${label}`;
    if (this.seenActivityKeys.has(key)) {
      return;
    }
    if (this.seenActivityKeys.size > 4000) {
      this.seenActivityKeys.clear();
    }
    this.seenActivityKeys.add(key);
    this.eventsPanel.push({ ts, kind, label });
  }

  private _applyGatewayEvent(eventType: string, payload: Record<string, unknown>, ts: string): void {
    switch (eventType) {
      case "brain.reply":
      case "outbound_message": {
        const channel = String(payload["channel"] ?? payload["target_channel"] ?? "brain");
        const text = String(payload["text"] ?? payload["content"] ?? "");
        if (text) {
          this.messagesPanel.push({ ts, role: "agent", channel, text });
        }
        break;
      }
      case "inbound_message": {
        const text = String(payload["text"] ?? payload["text_preview"] ?? "");
        if (text) {
          const channel = String(payload["channel"] ?? "tui");
          this.channelPanel.incrementCount(channel);
          this.messagesPanel.push({ ts, role: "user", channel, text });
        }
        break;
      }
      case "operator_message_sent": {
        this._recordEvent("gateway", "event", `${eventType}: ${this._summarizePayload(payload)}`, ts);
        break;
      }
      case "tool_call": {
        const label = `${String(payload["tool"] ?? "?")} ${String(payload["args"] ?? "")}`.trimEnd();
        this._recordEvent("gateway", "tool_call", label, ts);
        this.branchTree.upsert({
          id: String(payload["call_id"] ?? crypto.randomUUID()),
          parentId: String(payload["parent_id"] ?? ""),
          label,
          status: "running",
        });
        break;
      }
      case "tool_result": {
        const callId = String(payload["call_id"] ?? "");
        const ok = payload["ok"] === true;
        this.branchTree.upsert({
          id: callId,
          label: ok ? "done" : String(payload["error"] ?? "error"),
          status: ok ? "ok" : "error",
        });
        break;
      }
      case "llm_latency": {
        const ms = Number(payload["ms"] ?? 0);
        const model = String(payload["model"] ?? "?");
        this._recordEvent("gateway", "llm_latency", `${model} ${ms}ms`, ts);
        if (model !== "?") {
          this.headerPanel.update({ model });
        }
        break;
      }
      case "operator_command_sent": {
        this._recordEvent("gateway", "command", String(payload["command_type"] ?? "?"), ts);
        break;
      }
      case "error": {
        this._recordEvent("gateway", "error", String(payload["message"] ?? JSON.stringify(payload)), ts);
        break;
      }
      default: {
        if (eventType) {
          this._recordEvent("gateway", "event", `${eventType}: ${this._summarizePayload(payload)}`, ts);
        }
        break;
      }
    }
  }

  // -------------------------------------------------------------------------
  // WS events
  // -------------------------------------------------------------------------

  private _subscribeEvents(): void {
    this.client.onStateChange((state) => {
      // Fiel ao Python: status do header reflete conexão WS
      if (state.status === "connected") {
        this.headerPanel.update({ mode: this.liveSession ? "live" : "connecting" });
      } else if (state.status === "disconnected" || state.status === "error") {
        this.headerPanel.update({ mode: "disconnected" });
      }
      this.dirty = true;
    });

    this.client.on("*", (event) => {
      if (this.footer.isPaused()) return;

      const payload = event.payload;
      const ts = nowHms();

      switch (event.type) {
        // Mensagem do utilizador chegou ao brain
        case "inbound_message": {
          const channel = String(payload["channel"] ?? "?");
          const text = String(payload["text"] ?? "");
          this.channelPanel.incrementCount(channel);
          this.messagesPanel.push({ ts, role: "user", channel, text });
          break;
        }

        // Resposta do brain para o utilizador
        case "brain.reply":
        case "outbound_message": {
          const channel = String(payload["channel"] ?? "?");
          const text = String(payload["text"] ?? String(payload["content"] ?? ""));
          this.messagesPanel.push({ ts, role: "agent", channel, text });
          break;
        }

        // Canal mudou de status
        case "channel.status": {
          const name = String(payload["channel"] ?? "?");
          const status = String(payload["status"] ?? "inactive") as "active" | "inactive" | "error";
          this.channelPanel.upsert(name, status);
          break;
        }

        // Invocação de ferramenta
        case "tool_call": {
          const label = `${String(payload["tool"] ?? "?")} ${String(payload["args"] ?? "")}`.trimEnd();
          this.eventsPanel.push({ ts, kind: "tool_call", label });
          this.branchTree.upsert({
            id: String(payload["call_id"] ?? crypto.randomUUID()),
            parentId: String(payload["parent_id"] ?? ""),
            label,
            status: "running",
          });
          break;
        }

        // Resultado de ferramenta
        case "tool_result": {
          const callId = String(payload["call_id"] ?? "");
          const ok = payload["ok"] === true;
          const durationMs: number | undefined = typeof payload["duration_ms"] === "number" ? payload["duration_ms"] as number : undefined;
          this.branchTree.upsert({
            id: callId,
            label: ok ? "done" : String(payload["error"] ?? "error"),
            status: ok ? "ok" : "error",
            durationMs,
          });
          break;
        }

        // Latência LLM
        case "llm_latency": {
          const ms = Number(payload["ms"] ?? 0);
          const model = String(payload["model"] ?? "?");
          this.eventsPanel.push({ ts, kind: "llm_latency", label: `${model} ${ms}ms` });
          break;
        }

        // Erro genérico
        case "error": {
          const msg = String(payload["message"] ?? JSON.stringify(payload));
          this.eventsPanel.push({ ts, kind: "error", label: msg });
          break;
        }
      }

      this.dirty = true;
    });
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  private _render(): void {
    const buf: string[] = [];

    this.headerPanel.render(buf);
    this.channelPanel.render(buf);
    this.messagesPanel.render(buf);
    this.eventsPanel.render(buf);
    this.branchTree.render(buf);

    const connState = this.client.getState();
    this.footer.render(buf, connState.status, connState.reconnects);

    process.stdout.write(buf.join(""));
  }
}
