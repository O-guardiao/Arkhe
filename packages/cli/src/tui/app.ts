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
import { WsEventClient } from "../lib/ws-client.js";
import { computeLayout } from "./workbench.js";
import { ChannelPanel } from "./channel-panel.js";
import { MessagesPanel, type MessageEntry } from "./messages-panel.js";
import { EventsPanel, type ObsEvent } from "./events-panel.js";
import { BranchTree } from "./branch-tree.js";
import { Footer } from "./footer.js";
import {
  ENTER_ALT, EXIT_ALT,
  HIDE_CURSOR, SHOW_CURSOR,
  CLEAR_SCREEN,
} from "./ansi.js";

const FPS = 30;
const FRAME_MS = Math.floor(1000 / FPS);

export interface TuiAppOptions {
  /** URL base do gateway (ex: http://localhost:3000) */
  gatewayUrl: string;
  /** Token de autenticação */
  token: string;
}

function nowHms(): string {
  return new Date().toTimeString().slice(0, 8);
}

export class TuiApp {
  private client: WsEventClient;
  private channelPanel!: ChannelPanel;
  private messagesPanel!: MessagesPanel;
  private eventsPanel!: EventsPanel;
  private branchTree!: BranchTree;
  private footer!: Footer;

  private dirty = true;
  private frameTimer: ReturnType<typeof setInterval> | undefined;
  private running = false;

  constructor(private opts: TuiAppOptions) {
    this.client = new WsEventClient(opts.gatewayUrl, opts.token);
  }

  // -------------------------------------------------------------------------
  // Public
  // -------------------------------------------------------------------------

  async run(): Promise<void> {
    this._initPanels();
    this._enterAlt();
    this._setupInput();
    this._subscribeEvents();

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
      process.once("_tui_exit", resolve);
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
    this.client.disconnect();
    process.stdout.write(EXIT_ALT + SHOW_CURSOR);
    this.running = false;
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
        process.emit("_tui_exit" as "exit");
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
        if (text) this._sendPrompt(text);
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

  private _sendPrompt(text: string): void {
    const channel = this.channelPanel.selectedChannel() ?? "webchat";
    // Exibe localmente imediatamente
    this.messagesPanel.push({
      ts: nowHms(),
      role: "user",
      channel,
      text,
    });
    // A resposta real chegará via WS — sem HTTP adicional aqui
    this.dirty = true;
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
