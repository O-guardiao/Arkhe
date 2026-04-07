import { describe, expect, it, vi } from "vitest";
import { TuiApp, normalizeClientId, normalizeRefreshIntervalSeconds, type TuiLiveApi } from "./app.js";

type TuiAppInternals = {
  _initPanels: () => void;
  _ensureLiveSession: () => Promise<boolean>;
  _sendPrompt: (text: string) => Promise<void>;
  _refreshActivity: () => Promise<void>;
  headerPanel: { getData: () => Record<string, unknown> };
  messagesPanel: { runtimeMessages: Array<{ text: string }>; timeline: Array<{ summary: string }> };
  eventsPanel: { latestResponse: string };
  footer: { pauseReason: string; operatorNote: string; stateDir: string };
};

describe("TuiApp option normalization", () => {
  it("falls back to the default client id", () => {
    expect(normalizeClientId(undefined)).toBe("tui:default");
    expect(normalizeClientId("   ")).toBe("tui:default");
    expect(normalizeClientId("tui:ops")).toBe("tui:ops");
  });

  it("falls back to the default refresh interval for invalid values", () => {
    expect(normalizeRefreshIntervalSeconds(undefined)).toBe(0.75);
    expect(normalizeRefreshIntervalSeconds(0)).toBe(0.75);
    expect(normalizeRefreshIntervalSeconds(Number.NaN)).toBe(0.75);
    expect(normalizeRefreshIntervalSeconds(1.5)).toBe(1.5);
  });
});

describe("TuiApp live prompt dispatch", () => {
  it("dispatches prompt text through the live operator session", async () => {
    const liveApi: TuiLiveApi = {
      ensureSession: vi.fn().mockResolvedValue({
        session_id: "sess-1",
        client_id: "tui:test",
        status: "idle",
        state_dir: "",
        metadata: {},
      }),
      dispatchPrompt: vi.fn().mockResolvedValue({ ok: true }),
      fetchChannelsStatus: vi.fn().mockResolvedValue({ channels: {} }),
      fetchActivity: vi.fn().mockResolvedValue({ events: [] }),
      applyCommand: vi.fn().mockResolvedValue({ command: {} }),
      probeChannel: vi.fn().mockResolvedValue({ status: "ok" }),
      crossChannelSend: vi.fn().mockResolvedValue({ status: "ok" }),
    };

    const app = new TuiApp({
      gatewayUrl: "http://localhost:3000",
      token: "secret",
      clientId: "tui:test",
      liveApi,
    });
    const internalApp = app as unknown as TuiAppInternals;

    internalApp._initPanels();
    await internalApp._ensureLiveSession();
    await internalApp._sendPrompt("olá mundo");

    expect(liveApi.ensureSession).toHaveBeenCalledWith("tui:test");
    expect(liveApi.dispatchPrompt).toHaveBeenCalledWith("sess-1", "tui:test", "olá mundo");
  });

  it("routes /probe to the live channel probe API", async () => {
    const liveApi: TuiLiveApi = {
      ensureSession: vi.fn().mockResolvedValue({
        session_id: "sess-1",
        client_id: "tui:test",
        status: "idle",
        state_dir: "",
        metadata: {},
      }),
      dispatchPrompt: vi.fn().mockResolvedValue({ ok: true }),
      fetchChannelsStatus: vi.fn().mockResolvedValue({ channels: {} }),
      fetchActivity: vi.fn().mockResolvedValue({ events: [] }),
      applyCommand: vi.fn().mockResolvedValue({ command: {} }),
      probeChannel: vi.fn().mockResolvedValue({ status: "ok" }),
      crossChannelSend: vi.fn().mockResolvedValue({ status: "ok" }),
    };

    const app = new TuiApp({
      gatewayUrl: "http://localhost:3000",
      token: "secret",
      clientId: "tui:test",
      liveApi,
    });
    const internalApp = app as unknown as TuiAppInternals;

    internalApp._initPanels();
    await internalApp._sendPrompt("/probe telegram");

    expect(liveApi.probeChannel).toHaveBeenCalledWith("telegram");
    expect(liveApi.dispatchPrompt).not.toHaveBeenCalled();
  });

  it("hydrates the Python workbench payload into panels", async () => {
    const liveApi: TuiLiveApi = {
      ensureSession: vi.fn().mockResolvedValue({
        session_id: "sess-1",
        client_id: "tui:test",
        status: "idle",
        state_dir: "C:/tmp/state",
        metadata: {},
      }),
      dispatchPrompt: vi.fn().mockResolvedValue({ ok: true }),
      fetchChannelsStatus: vi.fn().mockResolvedValue({ channels: {} }),
      fetchActivity: vi.fn().mockResolvedValue({
        session: {
          session_id: "sess-1",
          client_id: "tui:test",
          status: "running",
          state_dir: "C:/tmp/state",
          metadata: {
            last_operator_response: "resposta final",
          },
        },
        event_log: [
          {
            event_type: "tui_response_ready",
            timestamp: "2026-04-07T12:34:56Z",
            payload: {
              response_preview: "resposta final",
            },
          },
        ],
        runtime: {
          controls: {
            paused: true,
            pause_reason: "manutenção",
            focused_branch_id: 2,
            last_checkpoint_path: "/tmp/cp-1",
            last_operator_note: "observar ramo 2",
          },
          tasks: {
            current: {
              title: "Resolver incidente",
              status: "running",
            },
          },
          recursive_session: {
            messages: [
              {
                role: "assistant",
                content: "olá runtime",
              },
            ],
            events: [
              {
                event_type: "tool_call",
                payload: {
                  tool: "search",
                },
              },
            ],
          },
          timeline: {
            entries: [
              {
                event_type: "tool_result",
                summary: "resultado ok",
              },
            ],
          },
          coordination: {
            latest_parallel_summary: {
              winner_branch_id: 3,
            },
            branch_tasks: [
              {
                branch_id: 2,
                title: "planner",
                mode: "parallel",
                status: "running",
              },
            ],
            events: [
              {
                operation: "fanout",
                payload_preview: "2 branches",
              },
            ],
          },
        },
      }),
      applyCommand: vi.fn().mockResolvedValue({ command: {} }),
      probeChannel: vi.fn().mockResolvedValue({ status: "ok" }),
      crossChannelSend: vi.fn().mockResolvedValue({ status: "ok" }),
    };

    const app = new TuiApp({
      gatewayUrl: "http://localhost:3000",
      token: "secret",
      clientId: "tui:test",
      liveApi,
    });
    const internalApp = app as unknown as TuiAppInternals;

    internalApp._initPanels();
    await internalApp._ensureLiveSession();
    await internalApp._refreshActivity();

    const header = internalApp.headerPanel.getData();
    expect(header["status"]).toBe("running");
    expect(header["paused"]).toBe(true);
    expect(header["focusedBranchId"]).toBe(2);
    expect(header["winnerBranchId"]).toBe(3);
    expect(header["lastCheckpoint"]).toBe("/tmp/cp-1");

    const messagesPanel = internalApp.messagesPanel;
    expect(messagesPanel.runtimeMessages[0]?.text).toBe("olá runtime");
    expect(messagesPanel.timeline[0]?.summary).toBe("resultado ok");

    const eventsPanel = internalApp.eventsPanel;
    expect(eventsPanel.latestResponse).toBe("resposta final");

    const footer = internalApp.footer;
    expect(footer.pauseReason).toBe("manutenção");
    expect(footer.operatorNote).toBe("observar ramo 2");
    expect(footer.stateDir).toBe("C:/tmp/state");
  });

  it("routes slash operator commands through applyCommand", async () => {
    const liveApi: TuiLiveApi = {
      ensureSession: vi.fn().mockResolvedValue({
        session_id: "sess-1",
        client_id: "tui:test",
        status: "idle",
        state_dir: "",
        metadata: {},
      }),
      dispatchPrompt: vi.fn().mockResolvedValue({ ok: true }),
      fetchChannelsStatus: vi.fn().mockResolvedValue({ channels: {} }),
      fetchActivity: vi.fn().mockResolvedValue({ events: [] }),
      applyCommand: vi.fn().mockResolvedValue({
        command: {
          command_type: "pause_runtime",
          command_id: "cmd-1",
        },
      }),
      probeChannel: vi.fn().mockResolvedValue({ status: "ok" }),
      crossChannelSend: vi.fn().mockResolvedValue({ status: "ok" }),
    };

    const app = new TuiApp({
      gatewayUrl: "http://localhost:3000",
      token: "secret",
      clientId: "tui:test",
      liveApi,
    });
    const internalApp = app as unknown as TuiAppInternals;

    internalApp._initPanels();
    await internalApp._sendPrompt("/pause manutenção");

    expect(liveApi.applyCommand).toHaveBeenCalledWith("sess-1", {
      clientId: "tui:test",
      commandType: "pause_runtime",
      payload: { reason: "manutenção" },
      branchId: null,
    });
    expect(liveApi.dispatchPrompt).not.toHaveBeenCalled();
  });
});