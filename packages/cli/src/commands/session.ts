/**
 * Comando `rlm session` — gerencia histórico de sessões.
 *
 * Uso:
 *   rlm session list <session-id>
 *   rlm session clear <session-id>
 */

import { Command } from "commander";
import { RlmClient } from "../client.js";
import { c, fmtTimestamp, printError, printJson } from "../format.js";

interface SessionMessage {
  role: string;
  content: unknown;
  timestamp: number;
  session_id: string;
  extra?: Record<string, unknown>;
}

interface SessionResponse {
  session_id: string;
  messages: SessionMessage[];
  count: number;
}

function roleColor(role: string): string {
  switch (role) {
    case "user": return c.info(role);
    case "assistant": return c.success(role);
    case "system": return c.dim(role);
    case "tool_result": return c.tool(role);
    default: return c.warn(role);
  }
}

export function makeSessionCommand(): Command {
  const session = new Command("session").description("Gerencia histórico de sessões do brain");

  // --- list ---
  session
    .command("list <session-id>")
    .description("Exibe mensagens de uma sessão")
    .option("--rotated", "Incluir arquivos rotacionados", false)
    .option("--json", "Saída em JSON bruto", false)
    .action(async (sessionId: string, opts: { rotated: boolean; json: boolean }) => {
      const client = new RlmClient();
      try {
        const res = await client.get<SessionResponse>(
          `/brain/session/${sessionId}${opts.rotated ? "?include_rotated=true" : ""}`
        );

        if (opts.json) {
          printJson(res);
          return;
        }

        console.log(c.bold(`\nSessão: ${c.session(res.session_id)} — ${res.count} mensagens\n`));

        for (const msg of res.messages) {
          const ts = msg.timestamp ? c.dim(fmtTimestamp(msg.timestamp)) : "";
          const content =
            typeof msg.content === "string"
              ? msg.content.slice(0, 200) + (msg.content.length > 200 ? "…" : "")
              : JSON.stringify(msg.content).slice(0, 200);

          console.log(`${roleColor(msg.role).padEnd(20)}${ts}`);
          console.log(`  ${content}`);
          console.log();
        }
      } catch (err) {
        printError(err);
        process.exit(1);
      }
    });

  // --- clear ---
  session
    .command("clear <session-id>")
    .description("Remove histórico persistido de uma sessão")
    .option("-y, --yes", "Confirmar sem interação", false)
    .action(async (sessionId: string, opts: { yes: boolean }) => {
      if (!opts.yes) {
        const { default: inquirer } = await import("inquirer");
        const { ok } = await inquirer.prompt([
          {
            type: "confirm",
            name: "ok",
            message: `Remover histórico da sessão "${sessionId}"?`,
            default: false,
          },
        ]);
        if (!ok) {
          console.log(c.dim("Cancelado."));
          return;
        }
      }

      const client = new RlmClient();
      try {
        await client.delete(`/brain/session/${sessionId}`);
        console.log(c.success(`Sessão "${sessionId}" removida.`));
      } catch (err) {
        printError(err);
        process.exit(1);
      }
    });

  return session;
}
