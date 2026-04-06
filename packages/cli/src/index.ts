/**
 * Entrypoint do CLI do RLM — `rlm`
 *
 * Comandos disponíveis:
 *   rlm prompt <text>            — Envia prompt ao brain
 *   rlm repl                     — REPL interativo
 *   rlm session list <sid>       — Histórico de sessão
 *   rlm session clear <sid>      — Remove histórico
 *   rlm tools list               — Lista ferramentas
 *   rlm tools exec <name>        — Executa ferramenta
 *   rlm health                   — Status do servidor
 *   rlm version                  — Versão instalada e do brain
 *   rlm doctor                   — Diagnóstico completo do sistema
 *   rlm channel <sub>            — Gestão de canais
 *   rlm skill <sub>              — Gestão de skills do brain
 *   rlm ops <sub>                — Gestão do processo do gateway
 *   rlm token <sub>              — Gestão do token de API
 *   rlm setup                    — Assistente de configuração inicial
 *   rlm peer <sub>               — Gestão de gateways peers
 *   rlm tui                      — Painel TUI ao vivo
 */

import { config as dotenvConfig } from "dotenv";
dotenvConfig();

import { Command } from "commander";
import { createRequire } from "node:module";
import { makePromptCommand } from "./commands/prompt.js";
import { makeSessionCommand } from "./commands/session.js";
import { makeToolsCommand } from "./commands/tools.js";
import { makeHealthCommand } from "./commands/health.js";
import { makeReplCommand } from "./commands/repl.js";
import { makeVersionCommand } from "./commands/version.js";
import { makeDoctorCommand } from "./commands/doctor.js";
import { makeChannelCommand } from "./commands/channel.js";
import { makeSkillCommand } from "./commands/skill.js";
import { makeOpsCommand } from "./commands/ops.js";
import { makeTokenCommand } from "./commands/token.js";
import { makeSetupCommand } from "./commands/setup.js";
import { makePeerCommand } from "./commands/peer.js";

const require = createRequire(import.meta.url);
// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const pkg = require("../package.json") as { version: string; description: string };

const program = new Command();

program
  .name("rlm")
  .description(pkg.description)
  .version(pkg.version, "-v, --version", "Exibir versão");

// Comandos existentes
program.addCommand(makePromptCommand());
program.addCommand(makeReplCommand());
program.addCommand(makeSessionCommand());
program.addCommand(makeToolsCommand());
program.addCommand(makeHealthCommand());

// Novos comandos
program.addCommand(makeVersionCommand());
program.addCommand(makeDoctorCommand());
program.addCommand(makeChannelCommand());
program.addCommand(makeSkillCommand());
program.addCommand(makeOpsCommand());
program.addCommand(makeTokenCommand());
program.addCommand(makeSetupCommand());
program.addCommand(makePeerCommand());

// Comando TUI — abre o painel interactivo ao vivo
program
  .command("tui")
  .description("Painel TUI ao vivo com eventos, mensagens e canais em tempo real")
  .option("--url <url>", "URL base do gateway", process.env["GATEWAY_URL"] ?? "http://localhost:3000")
  .option("--token <token>", "Token de autenticação", process.env["RLM_API_TOKEN"] ?? "")
  .action(async (opts: { url: string; token: string }) => {
    const { TuiApp } = await import("./tui/app.js");
    const app = new TuiApp({ gatewayUrl: opts.url, token: opts.token });
    await app.run();
  });

// Tratar erros globais do Commander
program.exitOverride((err) => {
  if (err.code !== "commander.helpDisplayed" && err.code !== "commander.version") {
    console.error(`Erro: ${err.message}`);
    process.exit(err.exitCode ?? 1);
  }
});

program.parseAsync(process.argv).catch((err: unknown) => {
  if (err instanceof Error) {
    console.error(`Erro fatal: ${err.message}`);
  } else {
    console.error("Erro fatal desconhecido:", err);
  }
  process.exit(1);
});
