import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { execFileUtf8 } from "./exec-file.js";
import type {
  ServiceConfig,
  ServiceInfo,
  ServiceManager,
  ServiceStatus,
} from "./types.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Path used when writing the temporary XML task file for import. */
function xmlTaskPath(name: string): string {
  return path.join(os.tmpdir(), `${name}.xml`);
}

/**
 * Escapes a string for insertion into Windows Task Scheduler XML.
 * XML-encodes `&`, `<`, `>`, and `"`.
 */
function escapeXml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// Public pure functions
// ---------------------------------------------------------------------------

/**
 * Generates a Windows Task Scheduler XML string from a `ServiceConfig`.
 * The generated XML is suitable for `schtasks /Create /XML`.
 */
export function generateXmlTask(cfg: ServiceConfig): string {
  const restartCount = cfg.restartPolicy === "never" ? "0" : "10";
  const restartEnabled = cfg.restartPolicy !== "never";

  const envVars = Object.entries(cfg.env)
    .map(
      ([k, v]) =>
        `        <EnvironmentVariable>\n          <Name>${escapeXml(k)}</Name>\n          <Value>${escapeXml(v)}</Value>\n        </EnvironmentVariable>`,
    )
    .join("\n");

  const extraArgs = [cfg.scriptPath, ...cfg.args]
    .map((a) => escapeXml(a))
    .join(" ");

  const lines: string[] = [
    `<?xml version="1.0" encoding="UTF-16"?>`,
    `<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">`,
    `  <RegistrationInfo>`,
    `    <Description>${escapeXml(cfg.description)}</Description>`,
    `    <Author>${escapeXml(cfg.displayName)}</Author>`,
    `  </RegistrationInfo>`,
    `  <Triggers>`,
    `    <LogonTrigger>`,
    `      <Enabled>true</Enabled>`,
    `    </LogonTrigger>`,
    `  </Triggers>`,
    `  <Principals>`,
    `    <Principal id="Author">`,
    `      <LogonType>InteractiveToken</LogonType>`,
    `      <RunLevel>LeastPrivilege</RunLevel>`,
    `    </Principal>`,
    `  </Principals>`,
    `  <Settings>`,
    `    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>`,
    `    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>`,
    `    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>`,
    `    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>`,
    `    <Enabled>true</Enabled>`,
  ];

  if (restartEnabled) {
    lines.push(`    <RestartOnFailure>`);
    lines.push(`      <Interval>PT1M</Interval>`);
    lines.push(`      <Count>${restartCount}</Count>`);
    lines.push(`    </RestartOnFailure>`);
  }

  lines.push(`  </Settings>`);
  lines.push(`  <Actions Context="Author">`);
  lines.push(`    <Exec>`);
  lines.push(`      <Command>${escapeXml(cfg.execPath)}</Command>`);
  lines.push(`      <Arguments>${extraArgs}</Arguments>`);
  lines.push(`      <WorkingDirectory>${escapeXml(cfg.workingDir)}</WorkingDirectory>`);
  lines.push(`    </Exec>`);
  lines.push(`  </Actions>`);

  if (Object.keys(cfg.env).length > 0) {
    lines.push(`  <EnvironmentVariables>`);
    lines.push(envVars);
    lines.push(`  </EnvironmentVariables>`);
  }

  lines.push(`</Task>`);
  lines.push("");

  return lines.join("\n");
}

/**
 * Parses the output of `schtasks /Query /FO LIST /V /TN <name>` and returns
 * a `ServiceInfo`.
 *
 * Relevant fields in the output:
 * ```
 * TaskName:   \RlmBrain
 * Status:     Running | Ready | Disabled
 * Last Result: 0
 * ```
 */
export function parseSchtasksQuery(output: string, name: string): ServiceInfo {
  const lowerOutput = output.toLowerCase();

  if (
    lowerOutput.includes("error:") ||
    lowerOutput.includes("cannot find") ||
    lowerOutput.includes("the system cannot find")
  ) {
    return { name, status: "not-installed" };
  }

  let status: ServiceStatus = "unknown";
  let exitCode: number | undefined;

  for (const rawLine of output.split("\n")) {
    const line = rawLine.trim();

    if (line.toLowerCase().startsWith("status:")) {
      const value = line.slice("status:".length).trim().toLowerCase();
      if (value === "running") {
        status = "running";
      } else if (value === "ready" || value === "queued") {
        status = "stopped";
      } else if (value === "disabled") {
        status = "failed";
      }
      continue;
    }

    if (line.toLowerCase().startsWith("last result:")) {
      const value = line.slice("last result:".length).trim();
      const parsed = parseInt(value, 10);
      if (!isNaN(parsed)) {
        exitCode = parsed;
      }
      continue;
    }
  }

  return {
    name,
    status,
    ...(exitCode !== undefined ? { exit_code: exitCode } : {}),
  };
}

// ---------------------------------------------------------------------------
// SchtasksManager — implements ServiceManager
// ---------------------------------------------------------------------------

export class SchtasksManager implements ServiceManager {
  async install(cfg: ServiceConfig): Promise<void> {
    const xml = generateXmlTask(cfg);
    const xmlPath = xmlTaskPath(cfg.name);
    await fs.writeFile(xmlPath, xml, "utf16le");
    const result = await execFileUtf8("schtasks.exe", [
      "/Create",
      "/XML",
      xmlPath,
      "/TN",
      cfg.name,
      "/F",
    ]);
    if (result.code !== 0) {
      throw new Error(
        `schtasks /Create failed (code ${result.code}): ${result.stderr}`,
      );
    }
    await fs.unlink(xmlPath).catch(() => undefined);
  }

  async uninstall(name: string): Promise<void> {
    await execFileUtf8("schtasks.exe", ["/Delete", "/TN", name, "/F"]).catch(
      () => undefined,
    );
  }

  async start(name: string): Promise<void> {
    const result = await execFileUtf8("schtasks.exe", ["/Run", "/TN", name]);
    if (result.code !== 0) {
      throw new Error(
        `schtasks /Run ${name} failed (code ${result.code}): ${result.stderr}`,
      );
    }
  }

  async stop(name: string): Promise<void> {
    const result = await execFileUtf8("schtasks.exe", ["/End", "/TN", name]);
    if (result.code !== 0) {
      throw new Error(
        `schtasks /End ${name} failed (code ${result.code}): ${result.stderr}`,
      );
    }
  }

  async restart(name: string): Promise<void> {
    await this.stop(name).catch(() => undefined);
    await this.start(name);
  }

  async status(name: string): Promise<ServiceInfo> {
    const result = await execFileUtf8("schtasks.exe", [
      "/Query",
      "/TN",
      name,
      "/FO",
      "LIST",
      "/V",
    ]);
    return parseSchtasksQuery(result.stdout + "\n" + result.stderr, name);
  }

  async logs(_name: string, lines = 100): Promise<string[]> {
    // Read from Windows Event Log (Application source) via wevtutil
    const result = await execFileUtf8("wevtutil.exe", [
      "qe",
      "Application",
      "/c",
      String(lines),
      "/rd:true",
      "/f:text",
    ]);
    if (result.code !== 0) return [];
    return result.stdout.split("\n").filter((l) => l.length > 0);
  }
}
