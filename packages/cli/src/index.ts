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
 *   rlm client <sub>             — Gestão de dispositivos/clientes
 */

import { config as dotenvConfig } from "dotenv";
dotenvConfig();

import { Command, CommanderError, InvalidArgumentError } from "commander";
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
import { makeClientCommand } from "./commands/client.js";

const require = createRequire(import.meta.url);
// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const pkg = require("../package.json") as { version: string; description: string };

const program = new Command();

function parsePositiveFloatOption(value: string): number {
  const parsed = Number.parseFloat(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new InvalidArgumentError("valor deve ser um número positivo");
  }
  return parsed;
}

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
program.addCommand(makeClientCommand());

// Comando TUI — abre o painel interactivo ao vivo
async function runTuiCommand(opts: {
  url: string;
  token: string;
  clientId?: string;
  refreshInterval: number;
  once: boolean;
}): Promise<void> {
  const { TuiApp } = await import("./tui/app.js");
  const { LiveWorkbenchAPI } = await import("./tui/live-api.js");
  const { CliContext } = await import("./context.js");
  const context = CliContext.fromEnvironment();
  const appOptions = {
    gatewayUrl: opts.url,
    token: opts.token,
    refreshIntervalSeconds: opts.refreshInterval,
    once: opts.once,
    liveApi: new LiveWorkbenchAPI(context),
  };
  const app = new TuiApp(
    opts.clientId ? { ...appOptions, clientId: opts.clientId } : appOptions
  );
  await app.run();
}

function registerWorkbenchCommand(name: "tui" | "workbench", description: string): void {
  program
    .command(name)
    .description(description)
    .option("--url <url>", "URL base do gateway", process.env["GATEWAY_URL"] ?? "http://localhost:3000")
    .option("--token <token>", "Token de autenticação", process.env["RLM_API_TOKEN"] ?? "")
    .option("--client-id <id>", "Client id da sessão viva (default: tui:default)")
    .option("--refresh-interval <seconds>", "Intervalo de atualização auxiliar do modo live", parsePositiveFloatOption, 0.75)
    .option("--once", "Renderiza o painel uma vez e encerra", false)
    .action(runTuiCommand);
}

registerWorkbenchCommand("tui", "Painel TUI ao vivo com eventos, mensagens e canais em tempo real");
registerWorkbenchCommand("workbench", "Alias do painel TUI/live workbench do operador");

// Tratar erros globais do Commander
program.exitOverride((err: CommanderError) => {
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
