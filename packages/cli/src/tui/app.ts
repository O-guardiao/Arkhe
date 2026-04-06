/**
 * TUI App — orquestrador do painel ao vivo do RLM.
 *
 * Responsabilidades:
 *  1. Inicializa o WsEventClient e conecta ao Gateway
 *  2. Instancia os painéis e dispõe o layout
 *  3. Executa o loop de render (30fps por padrão)
 *  4. Trata input de teclado em modo raw
 *  5. Encaminha eventos WS para os painéis correctos
 *  6. Limpa o terminal ao sair
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
import { MessagesPanel, type MessageEntry } from "./messages-panel.js";
import { EventsPanel, type ObsEvent } from "./events-panel.js";
import { BranchTree } from "./branch-tree.js";
import { Footer } from "./footer.js";
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
  private running = false;
  private lastChannelRefreshError = "";
  private lastActivityTs = 0;

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
    await this._bootstrapLiveMode();
    this._enterAlt();

    if (this.once) {
      this._render();
      this._cleanup();
      return;
    }

    this._setupInput();
    this._subscribeEvents();
    this._startChannelRefresh();

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
    this.channelPanel  = new ChannelPanel(layout.channels);
    this.messagesPanel = new MessagesPanel(layout.messages);
    this.eventsPanel   = new EventsPanel(layout.events);
    this.branchTree    = new BranchTree(layout.branch);
    this.footer        = new Footer(layout.footer);

    // Recalcula layout se o terminal for redimensionado
    process.stdout.on("resize", () => {
      const l = computeLayout();
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
    this.client.disconnect();
    if (process.stdin.isTTY && typeof process.stdin.setRawMode === "function") {
      process.stdin.setRawMode(false);
    }
    process.stdin.pause();
    process.stdout.write(EXIT_ALT + SHOW_CURSOR);
    this.running = false;
  }

  private async _bootstrapLiveMode(): Promise<void> {
    const hasLiveSession = await this._ensureLiveSession();
    if (hasLiveSession) {
      await this._refreshChannels(true);
    }
  }

  private _startChannelRefresh(): void {
    if (!this.liveApi) return;
    this.channelRefreshTimer = setInterval(() => {
      void this._refreshChannels(false);
    }, this.refreshIntervalMs);
    // Activity polling — matches Python TUI's _fetch_activity() loop
    this.activityRefreshTimer = setInterval(() => {
      void this._refreshActivity();
    }, this.refreshIntervalMs);
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
      this._pushSystemMessage(`Sessão viva: ${info.session_id}`);
      return true;
    } catch (error) {
      this._pushSystemMessage(`Workbench live indisponível: ${this._errorMessage(error)}`);
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
      const events = (data["events"] ?? []) as Array<{
        type?: string;
        payload?: Record<string, unknown>;
        ts?: number;
        eventId?: string;
      }>;

      for (const evt of events) {
        // Skip events we've already processed
        if (evt.ts && evt.ts <= this.lastActivityTs) continue;

        const ts = nowHms();
        const payload = evt.payload ?? {};
        const eventType = evt.type ?? "";

        switch (eventType) {
          case "brain.reply":
          case "outbound_message": {
            const channel = String(payload["channel"] ?? payload["target_channel"] ?? "brain");
            const text = String(payload["text"] ?? "");
            if (text) {
              this.messagesPanel.push({ ts, role: "agent", channel, text });
            }
            break;
          }
          case "inbound_message":
          case "operator_message_sent": {
            const text = String(payload["text"] ?? payload["text_preview"] ?? "");
            if (text && eventType === "inbound_message") {
              const channel = String(payload["channel"] ?? "tui");
              this.channelPanel.incrementCount(channel);
              this.messagesPanel.push({ ts, role: "user", channel, text });
            }
            break;
          }
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
            this.eventsPanel.push({ ts, kind: "llm_latency", label: `${model} ${ms}ms` });
            break;
          }
          case "error": {
            const msg = String(payload["message"] ?? JSON.stringify(payload));
            this.eventsPanel.push({ ts, kind: "error", label: msg });
            break;
          }
          case "operator_command_sent": {
            const cmdType = String(payload["command_type"] ?? "?");
            this.eventsPanel.push({ ts, kind: "command", label: cmdType });
            break;
          }
          default: {
            if (eventType) {
              this.eventsPanel.push({ ts, kind: "event", label: `${eventType}: ${JSON.stringify(payload).slice(0, 80)}` });
            }
            break;
          }
        }

        if (evt.ts) {
          this.lastActivityTs = Math.max(this.lastActivityTs, evt.ts);
        }
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

    const hasLiveSession = await this._ensureLiveSession();
    if (!hasLiveSession || !this.liveApi || !this.liveSession) {
      this._pushSystemMessage("Comando não enviado: servidor vivo indisponível.");
      return;
    }

    const command = parts[0]?.toLowerCase() ?? "";

    try {
      if (command === "/probe") {
        if (parts.length < 2) {
          throw new Error("Uso: /probe <channel_id>");
        }
        const channelId = parts[1] ?? "";
        const result = await this.liveApi.probeChannel(channelId);
        this._pushSystemMessage(`Probe ${channelId}: ${String(result["status"] ?? "ok")}`);
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
        this._pushSystemMessage(
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
      this._pushSystemMessage(`Comando aplicado: ${commandType}#${commandId}`);
    } catch (error) {
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

  // -------------------------------------------------------------------------
  // WS events
  // -------------------------------------------------------------------------

  private _subscribeEvents(): void {
    this.client.onStateChange((state) => {
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

    this.channelPanel.render(buf);
    this.messagesPanel.render(buf);
    this.eventsPanel.render(buf);
    this.branchTree.render(buf);

    const connState = this.client.getState();
    this.footer.render(buf, connState.status, connState.reconnects);

    process.stdout.write(buf.join(""));
  }
}
