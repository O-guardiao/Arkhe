import { describe, expect, it } from "vitest";
import {
  generateUnitFile,
  parseSystemctlStatus,
} from "../src/systemd.js";
import type { ServiceConfig } from "../src/types.js";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const baseConfig: ServiceConfig = {
  name: "rlm-brain",
  displayName: "RLM Brain",
  description: "RLM recursive language model brain service",
  execPath: "/usr/local/bin/node",
  scriptPath: "/opt/rlm/dist/main.js",
  args: ["--port", "8080"],
  env: { NODE_ENV: "production", LOG_LEVEL: "info" },
  workingDir: "/opt/rlm",
  restartPolicy: "always",
};

const RUNNING_STATUS = `
● rlm-brain.service - RLM Brain
     Loaded: loaded (/etc/systemd/system/rlm-brain.service; enabled)
     Active: active (running) since Mon 2025-01-01 12:00:00 UTC; 5h 30min ago
   Main PID: 1234 (node)
      Tasks: 12 (limit: 4915)
     Memory: 50.3M
     CGroup: /system.slice/rlm-brain.service
             └─1234 /usr/local/bin/node /opt/rlm/dist/main.js
`.trim();

const STOPPED_STATUS = `
● rlm-brain.service - RLM Brain
     Loaded: loaded (/etc/systemd/system/rlm-brain.service; enabled)
     Active: inactive (dead) since Mon 2025-01-01 13:00:00 UTC; 1min ago
   Process: 1234 ExecStart=/usr/local/bin/node /opt/rlm/dist/main.js (code=exited, status=0/SUCCESS)
  Main PID: 1234 (code=exited, status=0/SUCCESS)
`.trim();

const FAILED_STATUS = `
● rlm-brain.service - RLM Brain
     Loaded: loaded (/etc/systemd/system/rlm-brain.service; enabled)
     Active: failed (Result: exit-code) since Mon 2025-01-01 13:00:00 UTC; 2min ago
   Process: 1235 ExecStart=/usr/local/bin/node /opt/rlm/dist/main.js (code=exited, status=1/FAILURE)
  Main PID: 1235 (code=exited, status=1/FAILURE)
`.trim();

const NOT_FOUND_STATUS = `
Unit rlm-brain.service could not be found.
`.trim();

const ACTIVATING_STATUS = `
● rlm-brain.service - RLM Brain
     Active: activating (start) since Mon 2025-01-01 13:00:00 UTC; 2s ago
`.trim();

// ---------------------------------------------------------------------------
// generateUnitFile
// ---------------------------------------------------------------------------

describe("generateUnitFile()", () => {
  it("contains a [Unit] section header", () => {
    const unit = generateUnitFile(baseConfig);
    expect(unit).toContain("[Unit]");
  });

  it("contains the Description from config", () => {
    const unit = generateUnitFile(baseConfig);
    expect(unit).toContain(
      "Description=RLM recursive language model brain service",
    );
  });

  it("contains a [Service] section header", () => {
    const unit = generateUnitFile(baseConfig);
    expect(unit).toContain("[Service]");
  });

  it("contains ExecStart= with exec path and script path", () => {
    const unit = generateUnitFile(baseConfig);
    expect(unit).toContain("ExecStart=/usr/local/bin/node");
    expect(unit).toContain("/opt/rlm/dist/main.js");
  });

  it("contains WorkingDirectory from config", () => {
    const unit = generateUnitFile(baseConfig);
    expect(unit).toContain("WorkingDirectory=/opt/rlm");
  });

  it("maps restartPolicy 'always' to Restart=always", () => {
    const unit = generateUnitFile({ ...baseConfig, restartPolicy: "always" });
    expect(unit).toContain("Restart=always");
  });

  it("maps restartPolicy 'on-failure' to Restart=on-failure", () => {
    const unit = generateUnitFile({
      ...baseConfig,
      restartPolicy: "on-failure",
    });
    expect(unit).toContain("Restart=on-failure");
  });

  it("maps restartPolicy 'never' to Restart=no", () => {
    const unit = generateUnitFile({ ...baseConfig, restartPolicy: "never" });
    expect(unit).toContain("Restart=no");
  });

  it("emits Environment= lines for each env var", () => {
    const unit = generateUnitFile(baseConfig);
    expect(unit).toContain("NODE_ENV=production");
    expect(unit).toContain("LOG_LEVEL=info");
  });

  it("does NOT include User= when user is not specified", () => {
    const unit = generateUnitFile(baseConfig);
    expect(unit).not.toMatch(/^User=/m);
  });

  it("includes User= when user is specified", () => {
    const unit = generateUnitFile({ ...baseConfig, user: "rlmuser" });
    expect(unit).toContain("User=rlmuser");
  });

  it("includes Group= when group is specified", () => {
    const unit = generateUnitFile({ ...baseConfig, group: "rlmgroup" });
    expect(unit).toContain("Group=rlmgroup");
  });

  it("uses journal output when no logFile is set", () => {
    const unit = generateUnitFile(baseConfig);
    expect(unit).toContain("StandardOutput=journal");
    expect(unit).toContain("StandardError=journal");
  });

  it("uses append: output when logFile is set", () => {
    const unit = generateUnitFile({
      ...baseConfig,
      logFile: "/var/log/rlm/brain.log",
    });
    expect(unit).toContain("StandardOutput=append:/var/log/rlm/brain.log");
  });

  it("includes [Install] section with WantedBy=multi-user.target", () => {
    const unit = generateUnitFile(baseConfig);
    expect(unit).toContain("[Install]");
    expect(unit).toContain("WantedBy=multi-user.target");
  });

  it("includes extra args after scriptPath in ExecStart", () => {
    const unit = generateUnitFile(baseConfig);
    expect(unit).toContain("--port");
    expect(unit).toContain("8080");
  });
});

// ---------------------------------------------------------------------------
// parseSystemctlStatus
// ---------------------------------------------------------------------------

describe("parseSystemctlStatus()", () => {
  it("returns status=running for an active (running) service", () => {
    const info = parseSystemctlStatus(RUNNING_STATUS, "rlm-brain");
    expect(info.status).toBe("running");
  });

  it("parses Main PID for a running service", () => {
    const info = parseSystemctlStatus(RUNNING_STATUS, "rlm-brain");
    expect(info.pid).toBe(1234);
  });

  it("parses Memory as memory_bytes for a running service", () => {
    const info = parseSystemctlStatus(RUNNING_STATUS, "rlm-brain");
    // 50.3M ≈ 52,739,276 bytes — just verify it's in the right order of magnitude
    expect(info.memory_bytes).toBeGreaterThan(50 * 1024 * 1024);
    expect(info.memory_bytes).toBeLessThan(55 * 1024 * 1024);
  });

  it("returns status=stopped for an inactive (dead) service", () => {
    const info = parseSystemctlStatus(STOPPED_STATUS, "rlm-brain");
    expect(info.status).toBe("stopped");
  });

  it("returns status=failed for a failed service", () => {
    const info = parseSystemctlStatus(FAILED_STATUS, "rlm-brain");
    expect(info.status).toBe("failed");
  });

  it("returns status=not-installed when unit is not found", () => {
    const info = parseSystemctlStatus(NOT_FOUND_STATUS, "rlm-brain");
    expect(info.status).toBe("not-installed");
  });

  it("returns status=unknown for an activating service", () => {
    const info = parseSystemctlStatus(ACTIVATING_STATUS, "rlm-brain");
    expect(info.status).toBe("unknown");
  });

  it("always echoes back the provided name", () => {
    const info = parseSystemctlStatus(RUNNING_STATUS, "rlm-brain");
    expect(info.name).toBe("rlm-brain");
  });

  it("parses exit code from Process: line with status=", () => {
    const info = parseSystemctlStatus(FAILED_STATUS, "rlm-brain");
    expect(info.exit_code).toBe(1);
  });

  it("does not set pid for a not-installed service", () => {
    const info = parseSystemctlStatus(NOT_FOUND_STATUS, "rlm-brain");
    expect(info.pid).toBeUndefined();
  });

  it("handles empty output gracefully", () => {
    const info = parseSystemctlStatus("", "rlm-brain");
    expect(info.name).toBe("rlm-brain");
    expect(info.status).toBe("unknown");
  });

  it("returns not-installed for 'No files found for' output", () => {
    const info = parseSystemctlStatus(
      "No files found for rlm-brain.service.",
      "rlm-brain",
    );
    expect(info.status).toBe("not-installed");
  });
});
