import { afterEach, describe, expect, it, vi } from "vitest";
import { getRlmConfigDir, getServiceFilePath } from "../src/paths.js";

// ---------------------------------------------------------------------------
// getServiceFilePath
// ---------------------------------------------------------------------------

describe("getServiceFilePath()", () => {
  it("returns a systemd .service path for 'systemd' platform", () => {
    const p = getServiceFilePath("rlm-brain", "systemd");
    expect(p).toMatch(/rlm-brain\.service$/);
    // Must be in the system systemd unit directory
    expect(p).toContain("systemd");
  });

  it("starts with /etc/systemd/system/ for 'systemd' platform", () => {
    const p = getServiceFilePath("rlm-brain", "systemd");
    expect(p).toBe("/etc/systemd/system/rlm-brain.service");
  });

  it("returns a launchd .plist path for 'launchd' platform", () => {
    const p = getServiceFilePath("rlm-brain", "launchd");
    expect(p).toMatch(/rlm-brain\.plist$/);
    expect(p).toContain("LaunchAgents");
  });

  it("returns a .xml path for 'schtasks' platform", () => {
    const p = getServiceFilePath("rlm-brain", "schtasks");
    expect(p).toMatch(/rlm-brain\.xml$/);
  });

  it("returns a path containing the service name for 'unknown' platform", () => {
    const p = getServiceFilePath("rlm-brain", "unknown");
    expect(p).toContain("rlm-brain");
  });

  it("includes the provided name in all platform paths", () => {
    const name = "my-custom-service";
    for (const platform of ["systemd", "launchd", "schtasks", "unknown"] as const) {
      expect(getServiceFilePath(name, platform)).toContain(name);
    }
  });
});

// ---------------------------------------------------------------------------
// getRlmConfigDir — platform-specific via vi.stubGlobal
// ---------------------------------------------------------------------------

describe("getRlmConfigDir()", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns a path containing '.config' and 'rlm' on linux", () => {
    vi.stubGlobal("process", { ...process, platform: "linux" });
    const dir = getRlmConfigDir();
    expect(dir).toContain(".config");
    expect(dir).toContain("rlm");
  });

  it("returns a path containing 'Application Support' and 'rlm' on darwin", () => {
    vi.stubGlobal("process", { ...process, platform: "darwin" });
    const dir = getRlmConfigDir();
    expect(dir).toContain("Application Support");
    expect(dir).toContain("rlm");
  });

  it("returns a path ending in 'rlm' on win32 with APPDATA set", () => {
    vi.stubGlobal("process", {
      ...process,
      platform: "win32",
      env: {
        ...process.env,
        APPDATA: "C:\\Users\\tester\\AppData\\Roaming",
      },
    });
    const dir = getRlmConfigDir();
    expect(dir).toContain("rlm");
    // On win32, path includes the APPDATA base
    expect(dir).toContain("AppData");
  });

  it("falls back to homedir-based path on win32 without APPDATA", () => {
    vi.stubGlobal("process", {
      ...process,
      platform: "win32",
      env: { ...process.env, APPDATA: undefined },
    });
    const dir = getRlmConfigDir();
    expect(dir).toContain("rlm");
  });

  it("uses a ~/.config/rlm style path for unknown platforms", () => {
    vi.stubGlobal("process", { ...process, platform: "freebsd" });
    const dir = getRlmConfigDir();
    expect(dir).toContain(".config");
    expect(dir).toContain("rlm");
  });

  it("linux path does NOT contain 'Application Support'", () => {
    vi.stubGlobal("process", { ...process, platform: "linux" });
    const dir = getRlmConfigDir();
    expect(dir).not.toContain("Application Support");
  });

  it("darwin path does NOT contain '.config'", () => {
    vi.stubGlobal("process", { ...process, platform: "darwin" });
    const dir = getRlmConfigDir();
    expect(dir).not.toContain(".config");
  });
});
