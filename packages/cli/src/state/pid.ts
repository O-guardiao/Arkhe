/**
 * state/pid.ts — Gerenciamento de arquivos PID.
 *
 * Porta fiel de rlm/cli/state/pid.py
 */

import { readFileSync, writeFileSync, unlinkSync, mkdirSync } from "node:fs";
import { execSync } from "node:child_process";
import { dirname } from "node:path";

/** Lê o PID de um arquivo, retorna null se inválido/ausente. */
export function readPidFile(pidPath: string): number | null {
  try {
    const text = readFileSync(pidPath, "utf8").trim();
    const n = parseInt(text, 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

/** Retorna true se o processo com o PID dado está em execução. */
export function pidAlive(pid: number): boolean {
  if (process.platform === "win32") {
    try {
      const out = execSync(
        `tasklist /fi "PID eq ${pid}" /nh /fo csv`,
        { encoding: "utf8", timeout: 5000 }
      );
      return out.includes(`"${pid}"`) || out.includes(String(pid));
    } catch {
      return false;
    }
  }
  // POSIX: kill -0 não mata, apenas verifica existência
  try {
    process.kill(pid, 0);
    return true;
  } catch (err: unknown) {
    const code = (err as NodeJS.ErrnoException).code;
    // EPERM = processo existe mas sem permissão → está vivo
    return code === "EPERM";
  }
}

/** Grava o PID no arquivo, criando diretórios necessários. */
export function writePid(pidFile: string, pid: number): void {
  mkdirSync(dirname(pidFile), { recursive: true });
  writeFileSync(pidFile, String(pid), "utf8");
}

/** Remove o arquivo PID silenciosamente. */
export function removePid(pidFile: string): void {
  try {
    unlinkSync(pidFile);
  } catch {
    // ignora se não existe
  }
}
