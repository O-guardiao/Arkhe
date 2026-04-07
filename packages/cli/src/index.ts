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
import { pathToFileURL } from "node:url";
import type { CliContext } from "./context.js";
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
import {
  showStatus,
  startServices,
  stopServices,
  updateInstallationFacade,
} from "./service.js";

const require = createRequire(import.meta.url);
// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const pkg = require("../package.json") as { version: string; description: string };

function parsePositiveFloatOption(value: string): number {
  const parsed = Number.parseFloat(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new InvalidArgumentError("valor deve ser um número positivo");
  }
  return parsed;
}

function resolveOperatorBaseUrl(context: CliContext, explicitUrl?: string): string {
  const candidates = [
    explicitUrl,
    process.env["RLM_TUI_OPERATOR_URL"],
    context.env["RLM_TUI_OPERATOR_URL"],
    process.env["PYTHON_BRAIN_BASE_URL"],
    context.env["PYTHON_BRAIN_BASE_URL"],
    process.env["RLM_INTERNAL_HOST"],
    context.env["RLM_INTERNAL_HOST"],
    context.apiBaseUrl(),
  ];

  for (const candidate of candidates) {
    const trimmed = candidate?.trim();
    if (trimmed) {
      return trimmed;
    }
  }

  return context.apiBaseUrl();
}

async function exitWithCommandResult(commandPromise: Promise<number>): Promise<void> {
  const exitCode = await commandPromise;
  if (exitCode !== 0) {
    process.exit(exitCode);
  }
}

function registerOperationalCommands(program: Command): void {
  program
    .command("start")
    .description("Inicia o servidor Arkhe")
    .option("--foreground, -f", "Não lança em background (bloqueia o terminal)", false)
    .option("--api-only", "Inicia somente o servidor FastAPI (sem WebSocket)", false)
    .option("--ws-only", "Inicia somente o servidor WebSocket", false)
    .action(async (opts: { foreground: boolean; apiOnly: boolean; wsOnly: boolean }) => {
      await exitWithCommandResult(
        startServices({
          foreground: opts.foreground,
          apiOnly: opts.apiOnly,
          wsOnly: opts.wsOnly,
        }),
      );
    });

  program
    .command("stop")
    .description("Para o daemon Arkhe")
    .action(async () => {
      await exitWithCommandResult(stopServices());
    });

  program
    .command("status")
    .alias("ps")
    .description("Mostra status dos processos e configuração")
    .option("--json", "Emite um snapshot estruturado do status operacional e do launcher-state", false)
    .action(async (opts: { json: boolean }) => {
      await exitWithCommandResult(showStatus({ jsonOutput: opts.json }));
    });

  program
    .command("update")
    .description("Atualiza checkout git e dependências")
    .option("--check", "Apenas verifica se há commits remotos pendentes", false)
    .option("--no-restart", "Não reinicia os serviços após atualizar", false)
    .option("--path <path>", "Checkout do Arkhe a atualizar; default tenta detectar a instalação ativa")
    .action(async (opts: { check: boolean; noRestart: boolean; path?: string }) => {
      const updateOptions = opts.path
        ? {
            checkOnly: opts.check,
            restart: !opts.noRestart,
            targetPath: opts.path,
          }
        : {
            checkOnly: opts.check,
            restart: !opts.noRestart,
          };
      await exitWithCommandResult(
        updateInstallationFacade(updateOptions),
      );
    });
}

// Comando TUI — abre o painel interactivo ao vivo
async function runTuiCommand(opts: {
  url: string;
  operatorUrl?: string;
  token: string;
  clientId?: string;
  refreshInterval: number;
  once: boolean;
}): Promise<void> {
  const { TuiApp } = await import("./tui/app.js");
  const { LiveWorkbenchAPI } = await import("./tui/live-api.js");
  const { CliContext } = await import("./context.js");
  const context = CliContext.fromEnvironment();
  const operatorUrl = resolveOperatorBaseUrl(context, opts.operatorUrl);
  const appOptions = {
    gatewayUrl: opts.url,
    token: opts.token,
    refreshIntervalSeconds: opts.refreshInterval,
    once: opts.once,
    liveApi: new LiveWorkbenchAPI(context, operatorUrl),
  };
  const app = new TuiApp(
    opts.clientId ? { ...appOptions, clientId: opts.clientId } : appOptions
  );
  await app.run();
}

function registerWorkbenchCommand(program: Command, name: "tui" | "workbench", description: string): void {
  program
    .command(name)
    .description(description)
    .option("--url <url>", "URL base do gateway", process.env["GATEWAY_URL"] ?? "http://localhost:3000")
    .option(
      "--operator-url <url>",
      "URL base da API operador/runtime (default: RLM_TUI_OPERATOR_URL ou PYTHON_BRAIN_BASE_URL)",
    )
    .option("--token <token>", "Token de autenticação", process.env["RLM_API_TOKEN"] ?? "")
    .option("--client-id <id>", "Client id da sessão viva (default: tui:default)")
    .option("--refresh-interval <seconds>", "Intervalo de atualização auxiliar do modo live", parsePositiveFloatOption, 0.75)
    .option("--once", "Renderiza o painel uma vez e encerra", false)
    .action(runTuiCommand);
}

export function createProgram(): Command {
  const program = new Command();

  program
    .name("rlm")
    .description(pkg.description)
    .version(pkg.version, "-v, --version", "Exibir versão");

  program.addCommand(makePromptCommand());
  program.addCommand(makeReplCommand());
  program.addCommand(makeSessionCommand());
  program.addCommand(makeToolsCommand());
  program.addCommand(makeHealthCommand());

  program.addCommand(makeVersionCommand());
  program.addCommand(makeDoctorCommand());
  program.addCommand(makeChannelCommand());
  program.addCommand(makeSkillCommand());
  program.addCommand(makeOpsCommand());
  program.addCommand(makeTokenCommand());
  program.addCommand(makeSetupCommand());
  program.addCommand(makePeerCommand());
  program.addCommand(makeClientCommand());

  registerOperationalCommands(program);
  registerWorkbenchCommand(program, "tui", "Painel TUI ao vivo com eventos, mensagens e canais em tempo real");
  registerWorkbenchCommand(program, "workbench", "Alias do painel TUI/live workbench do operador");

  program.exitOverride((err: CommanderError) => {
    if (err.code !== "commander.helpDisplayed" && err.code !== "commander.version") {
      console.error(`Erro: ${err.message}`);
      process.exit(err.exitCode ?? 1);
    }
  });

  return program;
}

export async function runCli(argv = process.argv): Promise<void> {
  await createProgram().parseAsync(argv);
}

function isExecutedDirectly(): boolean {
  const entry = process.argv[1];
  if (!entry) {
    return false;
  }
  return import.meta.url === pathToFileURL(entry).href;
}

if (isExecutedDirectly()) {
  runCli().catch((err: unknown) => {
    if (err instanceof Error) {
      console.error(`Erro fatal: ${err.message}`);
    } else {
      console.error("Erro fatal desconhecido:", err);
    }
    process.exit(1);
  });
}
