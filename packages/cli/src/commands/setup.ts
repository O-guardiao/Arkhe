/**
 * Comando `rlm setup` — assistente de configuração inicial (first-run wizard).
 *
 * Coleta configurações essenciais interativamente:
 *   - URL do servidor RLM
 *   - Token de autenticação
 *   - Canal padrão (Telegram, Discord, Slack...)
 *
 * Escreve um arquivo .env na diretório atual.
 *
 * Uso:
 *   rlm setup
 *   rlm setup --non-interactive  (usa apenas variáveis de ambiente)
 */

import { Command } from "commander";
import { createInterface } from "node:readline";
import { writeFile } from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";
import { c, printError } from "../format.js";

function prompt(rl: ReturnType<typeof createInterface>, question: string): Promise<string> {
  return new Promise((resolve) => rl.question(question, resolve));
}

export function makeSetupCommand(): Command {
  return new Command("setup")
    .description("Assistente de configuração inicial")
    .option("--non-interactive", "Usa variáveis de ambiente sem interação", false)
    .option("--force", "Sobrescreve .env existente", false)
    .action(async (opts: { nonInteractive: boolean; force: boolean }) => {
      const envPath = ".env";

      if (existsSync(envPath) && !opts.force) {
        process.stdout.write(c.warn(`Arquivo ${envPath} já existe. Use --force para sobrescrever.\n`));
        process.stdout.write(`  Para reconfigurar: ${c.bold("rlm setup --force")}\n`);
        return;
      }

      if (opts.nonInteractive) {
        await runNonInteractive(envPath);
        return;
      }

      await runInteractive(envPath);
    });
}

async function runInteractive(envPath: string): Promise<void> {
  const rl = createInterface({ input: process.stdin, output: process.stdout });

  process.stdout.write(`\n${c.bold("Bem-vindo ao RLM Setup Wizard")} 🤖\n\n`);
  process.stdout.write("Pressione Enter para aceitar os valores padrão [entre colchetes].\n\n");

  try {
    const host = await prompt(rl, `  URL do servidor RLM [http://localhost:8000]: `);
    const token = await prompt(rl, `  Token de autenticação (RLM_TOKEN) [gerar automático]: `);
    const channel = await prompt(rl, `  Canal padrão (telegram|discord|slack|webchat) [webchat]: `);

    // Tokens opcionais de canais
    let telegramToken = "";
    let discordToken = "";
    let slackToken = "";

    const normalizedChannel = channel.trim().toLowerCase() || "webchat";

    if (normalizedChannel === "telegram" || normalizedChannel === "all") {
      telegramToken = await prompt(rl, `  Telegram Bot Token [pular]: `);
    }
    if (normalizedChannel === "discord" || normalizedChannel === "all") {
      discordToken = await prompt(rl, `  Discord Bot Token [pular]: `);
    }
    if (normalizedChannel === "slack" || normalizedChannel === "all") {
      slackToken = await prompt(rl, `  Slack Bot Token (xoxb-...) [pular]: `);
    }

    rl.close();

    const finalToken = token.trim() || generateToken();
    const finalHost = host.trim() || "http://localhost:8000";

    const lines: string[] = [
      `# RLM Configuration — gerado por \`rlm setup\``,
      `# https://github.com/seu-org/rlm`,
      ``,
      `RLM_HOST=${finalHost}`,
      `RLM_PORT=8000`,
      `RLM_TOKEN=${finalToken}`,
      ``,
      `# Canais`,
    ];

    if (telegramToken.trim()) lines.push(`RLM_TELEGRAM_TOKEN=${telegramToken.trim()}`);
    if (discordToken.trim()) lines.push(`RLM_DISCORD_BOT_TOKEN=${discordToken.trim()}`);
    if (slackToken.trim()) lines.push(`RLM_SLACK_BOT_TOKEN=${slackToken.trim()}`);

    await writeFile(envPath, lines.join("\n") + "\n", "utf8");

    process.stdout.write(`\n${c.success("✓")} Configuração salva em ${c.bold(envPath)}\n`);
    process.stdout.write(`\n${c.bold("Próximos passos:")}\n`);
    process.stdout.write(`  1. ${c.bold("source .env")} (ou use dotenv no projeto)\n`);
    process.stdout.write(`  2. ${c.bold("rlm doctor")} — verifica se tudo está configurado\n`);
    process.stdout.write(`  3. ${c.bold("rlm health")} — confirma conexão com o servidor\n\n`);
  } catch (err) {
    rl.close();
    printError(`Setup interrompido: ${String(err)}`);
    process.exit(1);
  }
}

async function runNonInteractive(envPath: string): Promise<void> {
  const host = process.env["RLM_HOST"] ?? "http://localhost:8000";
  const token = process.env["RLM_TOKEN"] ?? generateToken();

  // Lê arquivo existente para não apagar configurações extras
  let existing = "";
  if (existsSync(envPath)) {
    existing = readFileSync(envPath, "utf8");
  }

  const lines: string[] = existing ? [existing] : [];
  if (!existing.includes("RLM_HOST=")) lines.push(`RLM_HOST=${host}`);
  if (!existing.includes("RLM_TOKEN=")) lines.push(`RLM_TOKEN=${token}`);

  await writeFile(envPath, lines.join("\n") + "\n", "utf8");
  process.stdout.write(`${c.success("✓")} .env atualizado (modo não-interativo)\n`);
}

function generateToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("");
}
