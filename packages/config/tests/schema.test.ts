import { describe, expect, it } from "vitest";
import { ZodError } from "zod";
import {
  AgentConfigSchema,
  ChannelConfigSchema,
  DaemonConfigSchema,
  RlmConfigSchema,
  SecurityConfigSchema,
} from "../src/schema.js";
import { DEFAULT_RLM_CONFIG } from "../src/defaults.js";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const validAgent = {
  name: "arkhe-test",
  model: "gpt-4o",
  max_tokens: 2048,
  temperature: 0.5,
  tools_allowed: ["web_search", "code_exec"],
  memory_enabled: true,
};

const validChannel = {
  channel_id: "telegram-prod",
  channel_type: "telegram",
  enabled: true,
  rate_limit_rpm: 30,
};

const validDaemon = {
  host: "127.0.0.1",
  port: 7860,
  ws_path: "/ws/gateway",
  brain_ws_url: "ws://localhost:8000/ws/brain",
  log_level: "info",
};

const validSecurity = {
  allowed_origins: ["http://localhost:3000"],
  require_auth: false,
  secret_rotation_days: 90,
};

const validRlmConfig = {
  agent: validAgent,
  channels: [validChannel],
  daemon: validDaemon,
  security: validSecurity,
};

// ---------------------------------------------------------------------------
// AgentConfigSchema
// ---------------------------------------------------------------------------

describe("AgentConfigSchema", () => {
  it("parses a valid agent config", () => {
    const result = AgentConfigSchema.parse(validAgent);
    expect(result.name).toBe("arkhe-test");
    expect(result.model).toBe("gpt-4o");
    expect(result.max_tokens).toBe(2048);
    expect(result.temperature).toBe(0.5);
    expect(result.tools_allowed).toEqual(["web_search", "code_exec"]);
    expect(result.memory_enabled).toBe(true);
  });

  it("rejects a negative max_tokens", () => {
    expect(() =>
      AgentConfigSchema.parse({ ...validAgent, max_tokens: -1 })
    ).toThrow(ZodError);
  });

  it("rejects a temperature above 2", () => {
    expect(() =>
      AgentConfigSchema.parse({ ...validAgent, temperature: 2.1 })
    ).toThrow(ZodError);
  });

  it("rejects a temperature below 0", () => {
    expect(() =>
      AgentConfigSchema.parse({ ...validAgent, temperature: -0.1 })
    ).toThrow(ZodError);
  });

  it("rejects an empty agent name", () => {
    expect(() =>
      AgentConfigSchema.parse({ ...validAgent, name: "" })
    ).toThrow(ZodError);
  });

  it("rejects unknown extra fields (strict mode)", () => {
    expect(() =>
      AgentConfigSchema.parse({ ...validAgent, unknown_field: "oops" })
    ).toThrow(ZodError);
  });
});

// ---------------------------------------------------------------------------
// ChannelConfigSchema
// ---------------------------------------------------------------------------

describe("ChannelConfigSchema", () => {
  it("parses a valid telegram channel config", () => {
    const result = ChannelConfigSchema.parse(validChannel);
    expect(result.channel_type).toBe("telegram");
    expect(result.enabled).toBe(true);
  });

  it("accepts all valid channel types", () => {
    const types = ["telegram", "discord", "slack", "whatsapp", "webchat"];
    for (const channel_type of types) {
      expect(() =>
        ChannelConfigSchema.parse({ ...validChannel, channel_type })
      ).not.toThrow();
    }
  });

  it("rejects an invalid channel type", () => {
    expect(() =>
      ChannelConfigSchema.parse({ ...validChannel, channel_type: "carrier_pigeon" })
    ).toThrow(ZodError);
  });

  it("rejects a negative rate_limit_rpm", () => {
    expect(() =>
      ChannelConfigSchema.parse({ ...validChannel, rate_limit_rpm: -1 })
    ).toThrow(ZodError);
  });

  it("allows rate_limit_rpm of 0 (effectively disabled)", () => {
    const result = ChannelConfigSchema.parse({ ...validChannel, rate_limit_rpm: 0 });
    expect(result.rate_limit_rpm).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// DaemonConfigSchema
// ---------------------------------------------------------------------------

describe("DaemonConfigSchema", () => {
  it("parses a valid daemon config", () => {
    const result = DaemonConfigSchema.parse(validDaemon);
    expect(result.port).toBe(7860);
    expect(result.ws_path).toBe("/ws/gateway");
  });

  it("rejects a port above 65535", () => {
    expect(() =>
      DaemonConfigSchema.parse({ ...validDaemon, port: 70000 })
    ).toThrow(ZodError);
  });

  it("rejects a port of 0", () => {
    expect(() =>
      DaemonConfigSchema.parse({ ...validDaemon, port: 0 })
    ).toThrow(ZodError);
  });

  it("rejects an invalid brain_ws_url", () => {
    expect(() =>
      DaemonConfigSchema.parse({ ...validDaemon, brain_ws_url: "not-a-url" })
    ).toThrow(ZodError);
  });

  it("accepts all valid log levels", () => {
    const levels = ["debug", "info", "warn", "error"];
    for (const log_level of levels) {
      expect(() =>
        DaemonConfigSchema.parse({ ...validDaemon, log_level })
      ).not.toThrow();
    }
  });

  it("rejects an invalid log level", () => {
    expect(() =>
      DaemonConfigSchema.parse({ ...validDaemon, log_level: "verbose" })
    ).toThrow(ZodError);
  });

  it("rejects a ws_path that does not start with /", () => {
    expect(() =>
      DaemonConfigSchema.parse({ ...validDaemon, ws_path: "ws/gateway" })
    ).toThrow(ZodError);
  });
});

// ---------------------------------------------------------------------------
// SecurityConfigSchema
// ---------------------------------------------------------------------------

describe("SecurityConfigSchema", () => {
  it("parses a valid security config", () => {
    const result = SecurityConfigSchema.parse(validSecurity);
    expect(result.require_auth).toBe(false);
    expect(result.secret_rotation_days).toBe(90);
  });

  it("rejects a negative secret_rotation_days", () => {
    expect(() =>
      SecurityConfigSchema.parse({ ...validSecurity, secret_rotation_days: -1 })
    ).toThrow(ZodError);
  });

  it("accepts 0 as secret_rotation_days (rotation reminders disabled)", () => {
    const result = SecurityConfigSchema.parse({
      ...validSecurity,
      secret_rotation_days: 0,
    });
    expect(result.secret_rotation_days).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// RlmConfigSchema (root)
// ---------------------------------------------------------------------------

describe("RlmConfigSchema", () => {
  it("parses a complete, valid RLM config", () => {
    const result = RlmConfigSchema.parse(validRlmConfig);
    expect(result.agent.name).toBe("arkhe-test");
    expect(result.channels).toHaveLength(1);
    expect(result.channels[0]?.channel_id).toBe("telegram-prod");
    expect(result.daemon.port).toBe(7860);
    expect(result.security.require_auth).toBe(false);
  });

  it("accepts an empty channels array", () => {
    const result = RlmConfigSchema.parse({ ...validRlmConfig, channels: [] });
    expect(result.channels).toHaveLength(0);
  });

  it("parses multiple channels of different types", () => {
    const result = RlmConfigSchema.parse({
      ...validRlmConfig,
      channels: [
        validChannel,
        {
          channel_id: "discord-prod",
          channel_type: "discord",
          enabled: false,
          rate_limit_rpm: 60,
        },
      ],
    });
    expect(result.channels).toHaveLength(2);
    expect(result.channels[1]?.channel_type).toBe("discord");
  });

  it("rejects a config with a missing required field (daemon.port)", () => {
    const { port: _port, ...daemonWithoutPort } = validDaemon;
    expect(() =>
      RlmConfigSchema.parse({ ...validRlmConfig, daemon: daemonWithoutPort })
    ).toThrow(ZodError);
  });

  it("DEFAULT_RLM_CONFIG satisfies the schema", () => {
    expect(() => RlmConfigSchema.parse(DEFAULT_RLM_CONFIG)).not.toThrow();
  });

  it("ZodError message contains path information on invalid nested field", () => {
    try {
      RlmConfigSchema.parse({ ...validRlmConfig, daemon: { ...validDaemon, port: -1 } });
      expect.fail("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(ZodError);
      const zodErr = err as ZodError;
      const paths = zodErr.issues.map((i) => i.path.join("."));
      expect(paths.some((p) => p.includes("daemon") || p.includes("port"))).toBe(true);
    }
  });
});
