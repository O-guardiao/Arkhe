/**
 * Comando `rlm repl` — REPL interativo que mantém sessão ativa.
 *
 * O REPL envia cada linha ao brain e exibe a resposta, mantendo
 * o session_id fixo durante toda a sessão interativa.
 *
 * Uso:
 *   rlm repl
 *   rlm repl --session minha-sessao
 */

import * as readline from "node:readline";
import { Command } from "commander";
import chalk from "chalk";
import { RlmClient } from "../client.js";
import { c, fmtMs, printError } from "../format.js";

interface PromptResponseBody {
  session_id: string;
  response: string;
  elapsed_ms: number;
  tool_calls: unknown[];
}

const BANNER = `
${chalk.bold.cyan("RLM REPL")} ${chalk.gray("— Modo interativo")}
${chalk.gray("Digite uma mensagem e pressione Enter. Ctrl+C ou 'exit' para sair.")}
`;

export function makeReplCommand(): Command {
  return new Command("repl")
    .description("REPL interativo com o brain do RLM")
    .option("-s, --session <id>", "ID da sessão", `repl-${Date.now()}`)
    .option("-a, --actor <name>", "Nome do actor", "user")
    .option("--no-history", "Não exibir ferramenta usadas")
    .action(async (opts: { session: string; actor: string; history: boolean }) => {
      const client = new RlmClient();

      console.log(BANNER);
      console.log(c.dim(`sessão: ${opts.session}\n`));

      const rl = readline.createInterface({
        input: process.stdin,
        output: process.stdout,
        prompt: chalk.cyan("você> "),
        historySize: 100,
      });

      rl.prompt();

      rl.on("line", async (line: string) => {
        const content = line.trim();

        if (!content) {
          rl.prompt();
          return;
        }

        if (content.toLowerCase() === "exit" || content.toLowerCase() === "quit") {
          console.log(c.dim("Até logo!"));
          rl.close();
          process.exit(0);
        }

        try {
          const res = await client.post<PromptResponseBody>("/brain/prompt", {
            session_id: opts.session,
            content,
            actor: opts.actor,
          });

          console.log();
          console.log(chalk.bold.green("rlm> ") + res.response);

          if (opts.history && res.tool_calls.length > 0) {
            console.log(c.dim(`  [${res.tool_calls.length} ferramenta(s) usada(s) em ${fmtMs(res.elapsed_ms)}]`));
          } else {
            console.log(c.dim(`  [${fmtMs(res.elapsed_ms)}]`));
          }
          console.log();
        } catch (err) {
          console.log();
          printError(err);
          console.log();
        }

        rl.prompt();
      });

      rl.on("close", () => {
        console.log(c.dim("\nSessão encerrada."));
        process.exit(0);
      });
    });
}
