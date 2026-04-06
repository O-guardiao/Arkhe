/**
 * Comando `rlm token` — gerenciamento de tokens de API.
 *
 * Subcomandos:
 *   rlm token show    — exibe token atual (mascarado por segurança)
 *   rlm token rotate  — gera e persiste novo token
 *   rlm token verify  — verifica se o token atual é válido
 */

import { Command } from "commander";
import { RlmClient, RlmApiError } from "../client.js";
import { c, printError } from "../format.js";

export function makeTokenCommand(): Command {
  const cmd = new Command("token")
    .description("Gerenciamento de tokens de API RLM");

  // rlm token show
  cmd
    .command("show")
    .description("Exibe o token atual (mascarado)")
    .option("--unmask", "Exibe o token completo sem mascaramento", false)
    .action((opts: { unmask: boolean }) => {
      const token = process.env["RLM_TOKEN"] ?? "";
      if (!token) {
        printError("RLM_TOKEN não está definido. Execute `rlm setup` para configurar.");
        process.exit(1);
      }

      const display = opts.unmask
        ? token
        : maskToken(token);

      process.stdout.write(`${c.bold("Token")}  ${display}\n`);
      if (!opts.unmask) {
        process.stdout.write(c.warn("  Use --unmask para ver o token completo.\n"));
      }
    });

  // rlm token verify
  cmd
    .command("verify")
    .description("Verifica se o token atual é aceito pelo servidor")
    .action(async () => {
      const token = process.env["RLM_TOKEN"] ?? "";
      if (!token) {
        printError("RLM_TOKEN não está definido.");
        process.exit(1);
      }

      const client = new RlmClient();
      try {
        await client.get("/health");
        process.stdout.write(`${c.success("✓")} Token válido e aceito pelo servidor.\n`);
      } catch (err) {
        if (err instanceof RlmApiError && err.statusCode === 401) {
          printError("Token inválido ou expirado (HTTP 401).");
          process.exit(1);
        }
        throw err;
      }
    });

  // rlm token rotate
  cmd
    .command("rotate")
    .description("Gera um novo token e atualiza o servidor")
    .option("--save", "Atualiza o arquivo .env local com o novo token", false)
    .action(async (opts: { save: boolean }) => {
      const client = new RlmClient();

      try {
        const result = await client.post<{ token: string }>("/ops/token/rotate", {});
        const newToken = result.token;

        process.stdout.write(`${c.success("✓")} Novo token gerado.\n`);
        process.stdout.write(`  ${c.bold("Token")}  ${maskToken(newToken)}\n`);
        process.stdout.write(c.warn("  Copie e salve o token agora — ele não será exibido novamente.\n"));
        process.stdout.write(`  ${c.bold("Completo")}  ${newToken}\n\n`);

        if (opts.save) {
          await saveTokenToEnvFile(newToken);
          process.stdout.write(`${c.success("✓")} Token salvo em .env\n`);
        }
      } catch (err) {
        printError(`Falha ao rotacionar token: ${String(err)}`);
        process.exit(1);
      }
    });

  return cmd;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function maskToken(token: string): string {
  if (token.length <= 8) return "***";
  return `${token.slice(0, 4)}${"*".repeat(Math.max(0, token.length - 8))}${token.slice(-4)}`;
}

async function saveTokenToEnvFile(token: string): Promise<void> {
  const { readFile, writeFile } = await import("node:fs/promises");
  const { existsSync } = await import("node:fs");

  const envPath = ".env";
  let content = "";

  if (existsSync(envPath)) {
    content = await readFile(envPath, "utf8");
  }

  if (content.includes("RLM_TOKEN=")) {
    content = content.replace(/^RLM_TOKEN=.*/m, `RLM_TOKEN=${token}`);
  } else {
    content += `\nRLM_TOKEN=${token}\n`;
  }

  await writeFile(envPath, content, "utf8");
}
