/**
 * service-installers.ts — Instaladores de daemon do RLM (systemd / launchd).
 *
 * Porta fiel de rlm/cli/service_installers.py
 */

import {
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { spawnSync } from "node:child_process";

// ---------------------------------------------------------------------------
// Templates
// ---------------------------------------------------------------------------

const SYSTEMD_UNIT_TEMPLATE = `[Unit]
Description=RLM — Recursive Language Model Server
After=network.target

[Service]
Type=simple
WorkingDirectory={work_dir}
EnvironmentFile={env_file}
ExecStart={node} {entry}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
`;

const PLIST_TEMPLATE = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>        <string>com.rlm.server</string>
  <key>ProgramArguments</key>
  <array>
    <string>{node}</string>
    <string>{entry}</string>
    <string>api</string>
  </array>
  <key>WorkingDirectory</key>  <string>{work_dir}</string>
  <key>EnvironmentVariables</key>
  <dict>
{env_dict}
  </dict>
  <key>RunAtLoad</key>     <true/>
  <key>KeepAlive</key>     <true/>
  <key>StandardOutPath</key>  <string>{log_dir}/api.log</string>
  <key>StandardErrorPath</key> <string>{log_dir}/api.log</string>
</dict>
</plist>
`;

// ---------------------------------------------------------------------------
// installSystemdService
// ---------------------------------------------------------------------------

export interface InstallResult {
  exitCode: number;
  unitPath: string;
}

export function installSystemdService(opts: {
  projectRoot: string;
  envPath: string;
  logDir: string;
  serviceName?: string;
  nodeEntry?: string;
  callbacks: {
    ok: (msg: string) => void;
    err: (msg: string) => void;
    info: (msg: string) => void;
  };
}): InstallResult {
  const {
    projectRoot,
    envPath,
    serviceName = "rlm",
    nodeEntry = "rlm-server",
    callbacks: { ok, err, info },
  } = opts;

  const systemdDir = join(homedir(), ".config", "systemd", "user");
  mkdirSync(systemdDir, { recursive: true });

  const unitFile = join(systemdDir, `${serviceName}.service`);
  const content = SYSTEMD_UNIT_TEMPLATE
    .replace(/\{work_dir\}/g, projectRoot)
    .replace(/\{env_file\}/g, envPath)
    .replace(/\{node\}/g, process.execPath)
    .replace(/\{entry\}/g, nodeEntry);

  writeFileSync(unitFile, content, "utf8");
  ok(`Unit file criado: ${unitFile}`);

  for (const cmd of [
    ["systemctl", "--user", "daemon-reload"],
    ["systemctl", "--user", "enable", serviceName],
    ["systemctl", "--user", "start", serviceName],
  ]) {
    const result = spawnSync(cmd[0], cmd.slice(1), { timeout: 10_000 });
    if (result.status !== 0) {
      err(`Falha em \`${cmd.join(" ")}\`: ${result.stderr?.toString() ?? ""}`);
      return { exitCode: 1, unitPath: unitFile };
    }
    ok(`$ ${cmd.join(" ")}`);
  }

  ok("Serviço systemd instalado e iniciado");
  info(`Use \`systemctl --user status ${serviceName}\` para verificar`);
  return { exitCode: 0, unitPath: unitFile };
}

// ---------------------------------------------------------------------------
// installLaunchdService
// ---------------------------------------------------------------------------

export function installLaunchdService(opts: {
  projectRoot: string;
  envPath: string;
  logDir: string;
  nodeEntry?: string;
  callbacks: {
    ok: (msg: string) => void;
    info: (msg: string) => void;
  };
}): InstallResult {
  const {
    projectRoot,
    envPath,
    logDir,
    nodeEntry = "rlm-server",
    callbacks: { ok, info },
  } = opts;

  const launchDir = join(homedir(), "Library", "LaunchAgents");
  mkdirSync(launchDir, { recursive: true });
  mkdirSync(logDir, { recursive: true });

  const plistPath = join(launchDir, "com.rlm.server.plist");

  // Constrói dict de variáveis de ambiente a partir do .env
  const envItems: string[] = [];
  if (existsSync(envPath)) {
    const lines = readFileSync(envPath, "utf8").split("\n");
    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line || line.startsWith("#") || !line.includes("=")) continue;
      const eqIdx = line.indexOf("=");
      const key = line.slice(0, eqIdx).trim();
      const value = line.slice(eqIdx + 1).trim().replace(/^['"]|['"]$/g, "");
      envItems.push(`    <key>${key}</key><string>${value}</string>`);
    }
  }

  const content = PLIST_TEMPLATE
    .replace(/\{node\}/g, process.execPath)
    .replace(/\{entry\}/g, nodeEntry)
    .replace(/\{work_dir\}/g, projectRoot)
    .replace(/\{log_dir\}/g, logDir)
    .replace(/\{env_dict\}/g, envItems.join("\n"));

  writeFileSync(plistPath, content, "utf8");
  ok(`Plist criada: ${plistPath}`);

  const uid = process.getuid?.() ?? 501;
  for (const args of [
    ["bootout", `gui/${uid}`, plistPath],
    ["bootstrap", `gui/${uid}`, plistPath],
  ]) {
    spawnSync("launchctl", args, { timeout: 5000 });
  }

  ok("LaunchAgent instalado");
  info("Use `launchctl list com.rlm.server` para verificar");
  return { exitCode: 0, unitPath: plistPath };
}
