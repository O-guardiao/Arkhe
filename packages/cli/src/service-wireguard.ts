/**
 * service-wireguard.ts — Gerenciamento de peers WireGuard do RLM.
 *
 * Porta fiel de rlm/cli/service_wireguard.py
 */

import {
  existsSync,
  readFileSync,
  appendFileSync,
} from "node:fs";
import { spawnSync } from "node:child_process";

// ---------------------------------------------------------------------------
// addWireguardPeer
// ---------------------------------------------------------------------------

export interface WireguardCallbacks {
  ok: (msg: string) => void;
  warn: (msg: string) => void;
  err: (msg: string) => void;
  info: (msg: string) => void;
}

export function addWireguardPeer(
  name: string,
  pubkey: string,
  ip: string,
  wgConf: string,
  callbacks: WireguardCallbacks,
): number {
  const { ok, warn, err, info } = callbacks;

  if (!existsSync(wgConf)) {
    err("wg0.conf não encontrado em /etc/wireguard/wg0.conf");
    info("Verifique a documentação em docs/seguranca-rede-e-multiconexoes.md");
    return 1;
  }

  const block =
    `\n# peer: ${name}\n[Peer]\nPublicKey = ${pubkey}\nAllowedIPs = ${ip}/32\n`;

  const existing = readFileSync(wgConf, "utf8");
  if (existing.includes(pubkey)) {
    warn(`Peer com pubkey ...${pubkey.slice(-12)} já existe no wg0.conf`);
    return 0;
  }

  // Determina se precisa de elevação (não-root no POSIX)
  const uid = process.getuid?.() ?? -1;
  const needsSudo = process.platform !== "win32" && uid !== 0;

  if (needsSudo) {
    const proc = spawnSync("sudo", ["tee", "-a", wgConf], {
      input: block,
      timeout: 10_000,
    });
    if (proc.status !== 0) {
      err(`Falha ao escrever wg0.conf: ${proc.stderr?.toString() ?? ""}`);
      return 1;
    }
  } else {
    try {
      appendFileSync(wgConf, block, "utf8");
    } catch (e) {
      err(`Falha ao escrever wg0.conf: ${e}`);
      return 1;
    }
  }

  // Recarrega WireGuard se disponível
  const wgBin = which("wg");
  if (wgBin) {
    spawnSync("sudo", ["wg", "addconf", "wg0", "/dev/stdin"], {
      input: block,
      timeout: 5000,
    });
  }

  const systemctl = which("systemctl");
  if (systemctl) {
    spawnSync("sudo", ["systemctl", "reload", "wg-quick@wg0"], { timeout: 5000 });
  }

  ok(`Peer '${name}' (${ip}) adicionado ao wg0.conf`);
  return 0;
}

// ---------------------------------------------------------------------------
// helper
// ---------------------------------------------------------------------------

function which(bin: string): boolean {
  const result = spawnSync(
    process.platform === "win32" ? "where" : "which",
    [bin],
    { timeout: 2000 },
  );
  return result.status === 0;
}
