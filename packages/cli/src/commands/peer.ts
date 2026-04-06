/**
 * Comando `rlm peer` — gerenciamento de peers (nós do cluster RLM).
 *
 * Subcomandos:
 *   rlm peer list                  — lista peers conhecidos
 *   rlm peer add <url>             — adiciona um novo peer
 *   rlm peer remove <id>           — remove um peer pelo ID
 *   rlm peer ping <id>             — verifica latência com um peer
 *   rlm peer sync                  — sincroniza estado com todos os peers
 */

import { Command } from "commander";
import { RlmClient } from "../client.js";
import { c, printTable, printError } from "../format.js";

interface PeerInfo {
  id: string;
  url: string;
  status: string;
  latencyMs?: number;
  lastSeenMs?: number;
  version?: string;
}

function statusBadge(status: string): string {
  if (status === "online" || status === "connected") return c.success(status);
  if (status === "degraded") return c.warn(status);
  return c.error(status);
}

export function makePeerCommand(): Command {
  const cmd = new Command("peer")
    .description("Gerenciamento de peers no cluster RLM");

  // rlm peer list
  cmd
    .command("list")
    .aliases(["ls"])
    .description("Lista todos os peers conhecidos")
    .option("--json", "Saída em JSON bruto", false)
    .action(async (opts: { json: boolean }) => {
      const client = new RlmClient();
      const data = await client.get<{ peers: PeerInfo[] }>("/peers");
      const peers = data.peers ?? [];

      if (opts.json) {
        process.stdout.write(JSON.stringify(peers, null, 2) + "\n");
        return;
      }

      if (peers.length === 0) {
        process.stdout.write(c.warn("Nenhum peer configurado.\n"));
        return;
      }

      printTable(
        ["ID", "URL", "Status", "Latência", "Versão"],
        peers.map((p) => [
          p.id.slice(0, 12),
          p.url,
          statusBadge(p.status),
          p.latencyMs != null ? `${p.latencyMs}ms` : "—",
          p.version ?? "—",
        ]),
      );
    });

  // rlm peer add <url>
  cmd
    .command("add <url>")
    .description("Adiciona um peer pelo URL")
    .option("--name <name>", "Nome amigável para o peer")
    .action(async (url: string, opts: { name?: string }) => {
      const client = new RlmClient();
      try {
        const result = await client.post<{ peer: PeerInfo }>("/peers", {
          url: url.trim(),
          name: opts.name,
        });
        process.stdout.write(`${c.success("✓")} Peer adicionado: ${c.bold(result.peer.id.slice(0, 12))} → ${url}\n`);
      } catch (err) {
        printError(`Falha ao adicionar peer: ${String(err)}`);
        process.exit(1);
      }
    });

  // rlm peer remove <id>
  cmd
    .command("remove <id>")
    .aliases(["rm"])
    .description("Remove um peer pelo ID")
    .action(async (id: string) => {
      const client = new RlmClient();
      try {
        await client.post(`/peers/${encodeURIComponent(id)}/remove`, {});
        process.stdout.write(`${c.success("✓")} Peer ${c.bold(id.slice(0, 12))} removido.\n`);
      } catch (err) {
        printError(`Falha ao remover peer: ${String(err)}`);
        process.exit(1);
      }
    });

  // rlm peer ping <id>
  cmd
    .command("ping <id>")
    .description("Mede latência com um peer")
    .option("-c, --count <n>", "Número de pings", "3")
    .action(async (id: string, opts: { count: string }) => {
      const client = new RlmClient();
      const count = parseInt(opts.count, 10);
      const latencies: number[] = [];

      for (let i = 0; i < count; i++) {
        try {
          const start = Date.now();
          await client.get(`/peers/${encodeURIComponent(id)}/ping`);
          const latency = Date.now() - start;
          latencies.push(latency);
          process.stdout.write(`  ping ${i + 1}/${count}: ${latency}ms\n`);
        } catch {
          process.stdout.write(`  ping ${i + 1}/${count}: ${c.error("falhou")}\n`);
        }
      }

      if (latencies.length > 0) {
        const avg = Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length);
        process.stdout.write(`\n  Média: ${c.bold(`${avg}ms`)} (${latencies.length}/${count} respostas)\n`);
      } else {
        printError(`Peer ${id} inacessível.`);
        process.exit(1);
      }
    });

  // rlm peer sync
  cmd
    .command("sync")
    .description("Sincroniza estado com todos os peers")
    .action(async () => {
      const client = new RlmClient();
      try {
        const result = await client.post<{ synced: number; failed: number }>("/peers/sync", {});
        process.stdout.write(
          `${c.success("✓")} Sync concluído: ${result.synced} ok, ${result.failed} falhou\n`,
        );
      } catch (err) {
        printError(`Falha na sincronização: ${String(err)}`);
        process.exit(1);
      }
    });

  return cmd;
}
