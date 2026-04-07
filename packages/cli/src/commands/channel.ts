/**
 * Comando `rlm channel` — gerencia canais de comunicação.
 *
 * Subcomandos:
 *   rlm channel list                  — lista canais configurados
 *   rlm channel probe <name>          — verifica conectividade de um canal
 *   rlm channel enable <name>         — habilita canal
 *   rlm channel disable <name>        — desabilita canal
 */

import { Command } from "commander";
import { RlmClient } from "../client.js";
import { c, healthBadge, printTable, printError } from "../format.js";

interface ChannelInfo {
  id: string;
  name: string;
  type: string;
  status: string;
  lastSeenMs?: number;
  messagesSent: number;
  messagesReceived: number;
  errors: number;
}

function statusBadge(status: string): string {
  if (status === "disabled") return c.dim(status);
  return `${healthBadge(status)} ${status}`;
}

export function makeChannelCommand(): Command {
  const cmd = new Command("channel")
    .aliases(["ch"])
    .description("Gerenciamento de canais de comunicação");

  // rlm channel list
  cmd
    .command("list")
    .aliases(["ls"])
    .description("Lista todos os canais configurados")
    .option("--json", "Saída em JSON bruto", false)
    .action(async (opts: { json: boolean }) => {
      const client = new RlmClient();
      const data = await client.get<{ channels: Record<string, ChannelInfo> }>("/health");
      const channels = Object.values(data.channels ?? {});

      if (opts.json) {
        process.stdout.write(JSON.stringify(channels, null, 2) + "\n");
        return;
      }

      if (channels.length === 0) {
        process.stdout.write(c.warn("Nenhum canal configurado.\n"));
        return;
      }

      printTable(
        ["Canal", "Tipo", "Status", "Enviados", "Recebidos", "Erros"],
        channels.map((ch) => [
          c.bold(ch.id),
          ch.type,
          statusBadge(ch.status),
          String(ch.messagesSent),
          String(ch.messagesReceived),
          ch.errors > 0 ? c.error(String(ch.errors)) : String(ch.errors),
        ]),
      );
    });

  // rlm channel probe <name>
  cmd
    .command("probe <name>")
    .description("Verifica conectividade de um canal específico")
    .action(async (name: string) => {
      const client = new RlmClient();
      try {
        const result = await client.post<{ ok: boolean; latencyMs?: number; error?: string }>(
          `/channels/${encodeURIComponent(name)}/probe`,
          {},
        );
        if (result.ok) {
          process.stdout.write(`${c.success("✓")} Canal ${c.bold(name)} acessível${result.latencyMs ? ` (${result.latencyMs}ms)` : ""}\n`);
        } else {
          printError(`Canal ${name} indisponível: ${result.error ?? "erro desconhecido"}`);
          process.exit(1);
        }
      } catch {
        printError(`Falha ao verificar canal ${name}`);
        process.exit(1);
      }
    });

  // rlm channel enable <name>
  cmd
    .command("enable <name>")
    .description("Habilita um canal")
    .action(async (name: string) => {
      const client = new RlmClient();
      await client.post(`/channels/${encodeURIComponent(name)}/enable`, {});
      process.stdout.write(`${c.success("✓")} Canal ${c.bold(name)} habilitado.\n`);
    });

  // rlm channel disable <name>
  cmd
    .command("disable <name>")
    .description("Desabilita um canal")
    .action(async (name: string) => {
      const client = new RlmClient();
      await client.post(`/channels/${encodeURIComponent(name)}/disable`, {});
      process.stdout.write(`${c.warn("!")} Canal ${c.bold(name)} desabilitado.\n`);
    });

  return cmd;
}
