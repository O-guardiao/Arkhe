/**
 * Comando `rlm ops` — controle operacional do servidor RLM (daemon).
 *
 * Subcomandos:
 *   rlm ops start    — inicia o daemon
 *   rlm ops stop     — encerra o daemon
 *   rlm ops restart  — reinicia o daemon
 *   rlm ops status   — exibe estado atual
 *   rlm ops logs     — exibe logs recentes
 */

import { Command } from "commander";
import { RlmClient, RlmApiError } from "../client.js";
import { c, printError } from "../format.js";

export function makeOpsCommand(): Command {
  const cmd = new Command("ops")
    .description("Controle operacional do daemon RLM");

  // rlm ops status
  cmd
    .command("status")
    .description("Exibe estado atual do daemon")
    .option("--json", "Saída em JSON bruto", false)
    .action(async (opts: { json: boolean }) => {
      const client = new RlmClient();
      try {
        const data = await client.get<{
          status: string;
          uptime_seconds?: number;
          pid?: number;
          memory_mb?: number;
          bridge?: { connected: boolean };
        }>("/health");

        if (opts.json) {
          process.stdout.write(JSON.stringify(data, null, 2) + "\n");
          return;
        }

        const uptimeStr = data.uptime_seconds != null
          ? formatUptime(data.uptime_seconds)
          : "—";

        process.stdout.write(`\n`);
        process.stdout.write(`  ${c.bold("Status")}    ${data.status === "ok" ? c.success("rodando") : c.warn(data.status)}\n`);
        if (data.pid) process.stdout.write(`  ${c.bold("PID")}       ${data.pid}\n`);
        process.stdout.write(`  ${c.bold("Uptime")}    ${uptimeStr}\n`);
        if (data.memory_mb) process.stdout.write(`  ${c.bold("Memória")}   ${data.memory_mb.toFixed(1)} MB\n`);
        process.stdout.write(`  ${c.bold("Bridge")}    ${data.bridge?.connected ? c.success("conectada") : c.error("desconectada")}\n`);
        process.stdout.write(`\n`);
      } catch (err) {
        if (err instanceof RlmApiError && (err.statusCode === 0 || err.statusCode >= 500)) {
          process.stdout.write(`${c.error("●")} Daemon ${c.error("offline")}\n`);
          process.exit(1);
        }
        throw err;
      }
    });

  // rlm ops start
  cmd
    .command("start")
    .description("Inicia o daemon RLM")
    .option("--port <port>", "Porta do servidor", "8000")
    .option("--detach", "Inicia em background (não bloqueia terminal)", false)
    .action(async (opts: { port: string; detach: boolean }) => {
      const client = new RlmClient();
      try {
        await client.post("/ops/start", { port: parseInt(opts.port, 10), detach: opts.detach });
        process.stdout.write(`${c.success("✓")} Daemon iniciado na porta ${opts.port}\n`);
      } catch (err) {
        printError(`Falha ao iniciar daemon: ${String(err)}`);
        process.exit(1);
      }
    });

  // rlm ops stop
  cmd
    .command("stop")
    .description("Encerra o daemon RLM")
    .option("--force", "Força encerramento imediato (SIGKILL)", false)
    .action(async (opts: { force: boolean }) => {
      const client = new RlmClient();
      try {
        await client.post("/ops/stop", { force: opts.force });
        process.stdout.write(`${c.success("✓")} Daemon encerrado.\n`);
      } catch {
        printError("Daemon não acessível ou já parado.");
        process.exit(1);
      }
    });

  // rlm ops restart
  cmd
    .command("restart")
    .description("Reinicia o daemon RLM")
    .action(async () => {
      const client = new RlmClient();
      try {
        await client.post("/ops/restart", {});
        process.stdout.write(`${c.success("✓")} Daemon reiniciado.\n`);
      } catch {
        printError("Falha ao reiniciar daemon.");
        process.exit(1);
      }
    });

  // rlm ops logs
  cmd
    .command("logs")
    .description("Exibe logs recentes do daemon")
    .option("-n, --lines <n>", "Número de linhas", "50")
    .option("--level <level>", "Filtro de nível (debug|info|warn|error)", "info")
    .option("--follow", "Segue logs em tempo real", false)
    .action(async (opts: { lines: string; level: string; follow: boolean }) => {
      const client = new RlmClient();
      const data = await client.get<{ lines: string[] }>(
        `/ops/logs?n=${opts.lines}&level=${opts.level}`,
      );
      for (const line of data.lines ?? []) {
        process.stdout.write(line + "\n");
      }
      if (opts.follow) {
        process.stdout.write(c.warn("\n[--follow não implementado ainda — use SSE diretamente]\n"));
      }
    });

  return cmd;
}

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}
