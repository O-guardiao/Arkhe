import { describe, expect, it, vi } from "vitest";
import { TuiApp, normalizeClientId, normalizeRefreshIntervalSeconds, type TuiLiveApi } from "./app.js";

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

    (app as { _initPanels: () => void })._initPanels();
    await (app as { _ensureLiveSession: () => Promise<boolean> })._ensureLiveSession();
    await (app as { _sendPrompt: (text: string) => Promise<void> })._sendPrompt("olá mundo");

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

    (app as { _initPanels: () => void })._initPanels();
    await (app as { _sendPrompt: (text: string) => Promise<void> })._sendPrompt("/probe telegram");

    expect(liveApi.probeChannel).toHaveBeenCalledWith("telegram");
    expect(liveApi.dispatchPrompt).not.toHaveBeenCalled();
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

    (app as { _initPanels: () => void })._initPanels();
    await (app as { _sendPrompt: (text: string) => Promise<void> })._sendPrompt("/pause manutenção");

    expect(liveApi.applyCommand).toHaveBeenCalledWith("sess-1", {
      clientId: "tui:test",
      commandType: "pause_runtime",
      payload: { reason: "manutenção" },
      branchId: null,
    });
    expect(liveApi.dispatchPrompt).not.toHaveBeenCalled();
  });
});