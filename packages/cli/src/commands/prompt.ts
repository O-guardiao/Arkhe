/**
 * Comando `rlm prompt` — envia uma mensagem ao brain e exibe a resposta.
 *
 * Uso:
 *   rlm prompt "Qual é a hora?" --session minha-sessao
 *   echo "Olá mundo" | rlm prompt --session pipe-demo
 */

import { Command } from "commander";
import ora from "ora";
import chalk from "chalk";
import * as readline from "node:readline";
import { RlmClient } from "../client.js";
import { c, fmtMs, printError } from "../format.js";

interface PromptOptions {
  session: string;
  actor: string;
  json: boolean;
  raw: boolean;
}

interface PromptResponseBody {
  session_id: string;
  response: string;
  elapsed_ms: number;
  tool_calls: unknown[];
}

async function readStdin(): Promise<string> {
  if (process.stdin.isTTY) return "";

  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    process.stdin.on("data", (chunk: Buffer) => chunks.push(chunk));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8").trim()));
    process.stdin.on("error", reject);
  });
}

export function makePromptCommand(): Command {
  const cmd = new Command("prompt")
    .description("Envia um prompt ao brain do RLM e exibe a resposta")
    .argument("[text...]", "Texto do prompt (ou via stdin)")
    .option("-s, --session <id>", "ID da sessão", `cli-${Date.now()}`)
    .option("-a, --actor <name>", "Nome do actor", "user")
    .option("--json", "Saída em JSON bruto", false)
    .option("--raw", "Apenas a resposta, sem decoração", false)
    .action(async (textParts: string[], opts: PromptOptions) => {
      const stdin = await readStdin();
      const content = textParts.join(" ").trim() || stdin;

      if (!content) {
        console.error(c.error("Nenhum prompt fornecido. Use: rlm prompt \"texto\" ou stdin."));
        process.exit(1);
      }

      const client = new RlmClient();
      const spinner = opts.raw || opts.json
        ? null
        : ora(c.dim("Aguardando resposta…")).start();

      try {
        const res = await client.post<PromptResponseBody>("/brain/prompt", {
          session_id: opts.session,
          content,
          actor: opts.actor,
        });

        spinner?.stop();

        if (opts.json) {
          console.log(JSON.stringify(res, null, 2));
          return;
        }

        if (opts.raw) {
          console.log(res.response);
          return;
        }

        console.log();
        console.log(chalk.bold("Resposta:"));
        console.log(res.response);
        console.log();
        console.log(c.dim(`sessão: ${res.session_id}  |  tempo: ${fmtMs(res.elapsed_ms)}`));

        if (res.tool_calls.length > 0) {
          console.log(c.dim(`ferramentas usadas: ${res.tool_calls.length}`));
        }
      } catch (err) {
        spinner?.fail("Falha ao obter resposta");
        printError(err);
        process.exit(1);
      }
    });

  return cmd;
}
