/**
 * Orquestração principal do onboarding — runWizard + fluxo de 8 etapas.
 *
 * Migrado de rlm/cli/wizard/onboarding.py
 */

import * as os from "node:os";
import * as path from "node:path";
import {
  EnvDetector,
  loadExistingEnv,
  probeServer,
  resolveEnvPath,
  summarizeExistingConfig,
  writeEnv,
} from "./env-utils.js";
import { WizardCancelledError, defaultPrompter } from "./prompter.js";
import type { WizardPrompter } from "./prompter.js";
import {
  stepChannels,
  stepLlmCredentials,
  stepSecurityTokens,
  stepServerConfig,
} from "./steps.js";

// ──────────────────────────────────────────────────────────────────────────
// runWizard — ponto de entrada público
// ──────────────────────────────────────────────────────────────────────────

/**
 * Executa o wizard interativo.
 *
 * @param flow - "quickstart", "advanced", ou undefined (pergunta ao usuário)
 * @returns exit code (0 = sucesso)
 */
export async function runWizard(flow?: string): Promise<number> {
  const p = defaultPrompter;
  const envDetector = EnvDetector.detect();
  const projectRoot = process.cwd();
  const envPath = resolveEnvPath(projectRoot);
  const existing = loadExistingEnv(envPath);

  try {
    return await runOnboarding(p, envDetector, projectRoot, envPath, existing, flow);
  } catch (err) {
    if (err instanceof WizardCancelledError) {
      p.outro("Setup cancelado pelo usuário.");
      return 1;
    }
    throw err;
  }
}

// ──────────────────────────────────────────────────────────────────────────
// runOnboarding — orquestrador central de 8 etapas
// ──────────────────────────────────────────────────────────────────────────

async function runOnboarding(
  p: WizardPrompter,
  env: EnvDetector,
  projectRoot: string,
  envPath: string,
  existing: Record<string, string>,
  flow: string | undefined
): Promise<number> {
  // ═══ Etapa 1: Intro + Banner ═══════════════════════════════════════════
  const osLabel = env.system + (env.isWsl ? " (WSL)" : "");
  const uvLabel = env.uvPath ? `✓ ${env.uvPath}` : "✗ ausente";
  const nodeVer = process.version;

  p.intro(
    `Arkhe Setup Wizard\n` +
    `Configuração interativa do Recursive Language Model\n\n` +
    `Sistema: ${osLabel}  •  Node.js: ${nodeVer}  •  uv: ${uvLabel}`
  );

  // ═══ Etapa 2: Aviso de segurança ═══════════════════════════════════════
  p.note(
    "O Arkhe executa modelos de linguagem que podem gerar\n" +
    "código e comandos de sistema. Tokens de segurança serão\n" +
    "gerados automaticamente para proteger WebSocket, API\n" +
    "REST e rotas administrativas.\n\n" +
    "É sua responsabilidade:\n" +
    "  • Não expor as portas sem autenticação\n" +
    "  • Manter a chave API LLM confidencial\n" +
    "  • Revisar comandos antes de executá-los",
    "⚠ Aviso de segurança"
  );

  const understands = await p.confirm("Entendo os riscos. Continuar?", true);
  if (!understands) {
    throw new WizardCancelledError("riscos não aceitos");
  }

  // ═══ Etapa 3: Config existente ══════════════════════════════════════════
  let configAction = "fresh";
  let workingExisting = { ...existing };

  if (Object.keys(existing).length > 0) {
    p.note(summarizeExistingConfig(existing), "Configuração existente detectada");

    configAction = await p.select(
      "Como deseja proceder com a configuração existente?",
      [
        { value: "keep", label: "Manter valores atuais", hint: "prosseguir sem alterar" },
        { value: "modify", label: "Modificar valores", hint: "editar variáveis uma a uma" },
        { value: "reset", label: "Resetar tudo", hint: "começar do zero" },
      ],
      "keep"
    );

    if (configAction === "reset") {
      const scope = await p.select(
        "O que resetar?",
        [
          { value: "config", label: "Apenas configuração (.env)", hint: "mantém dados" },
          { value: "full", label: "Reset completo", hint: "remove .env e regenera tudo" },
        ],
        "config"
      );
      if (scope === "full") {
        const fs = await import("node:fs");
        if (fs.existsSync(envPath)) fs.unlinkSync(envPath);
        p.note("Configuração removida. Começando do zero.", "Reset");
      }
      workingExisting = {};
      configAction = "fresh";
    }
  }

  // ═══ Etapa 4: Escolher Flow ══════════════════════════════════════════════
  let chosenFlow: string;
  if (flow === "quickstart" || flow === "advanced") {
    chosenFlow = flow;
  } else {
    chosenFlow = await p.select(
      "Modo de configuração",
      [
        { value: "quickstart", label: "⚡ QuickStart", hint: "API key + defaults automáticos → pronto em 30s" },
        { value: "advanced", label: "🔧 Avançado", hint: "configurar porta, bind, modelo, tokens individualmente" },
      ],
      "quickstart"
    );
  }

  // ═══ Coleta de configuração ══════════════════════════════════════════════
  let config: Record<string, string>;

  if (configAction === "keep") {
    config = { ...workingExisting };
    p.note("Usando configuração existente sem alterações.", "Config mantida");
  } else {
    config = await collectConfig(p, workingExisting, chosenFlow, configAction);
  }

  // ═══ Etapa 5: Probe ao servidor ══════════════════════════════════════════
  const apiHost = config["RLM_API_HOST"] ?? "127.0.0.1";
  const apiPort = config["RLM_API_PORT"] ?? "5000";
  const wsHost = config["RLM_WS_HOST"] ?? "127.0.0.1";
  const wsPort = config["RLM_WS_PORT"] ?? "8765";

  const spinner = p.progress("Verificando se o servidor já está rodando…");
  const [apiAlive, wsAlive] = await Promise.all([
    probeServer(apiHost, apiPort),
    probeServer(wsHost, wsPort),
  ]);

  if (apiAlive || wsAlive) {
    spinner.stop(
      `⚠  Servidor detectado (API=${apiAlive ? "✓" : "✗"} WS=${wsAlive ? "✓" : "✗"})`
    );
    p.note(
      "O servidor Arkhe já está em execução.\n" +
      "As novas configurações serão aplicadas após reinício.\n" +
      "Use 'arkhe stop && arkhe start' após o setup.",
      "Servidor ativo"
    );
  } else {
    spinner.stop("Nenhum servidor ativo detectado.");
  }

  // ═══ Etapa 6: Salvar .env ═══════════════════════════════════════════════
  writeEnv(envPath, config);
  p.note(`Arquivo salvo em: ${envPath}`, "✓ .env gravado");

  // ═══ Etapa 7: Daemon ═════════════════════════════════════════════════════
  await setupDaemon(p, env, projectRoot, envPath, chosenFlow);

  // ═══ Etapa 8: Resumo + Finalização ═══════════════════════════════════════
  showSummary(p, config);

  const nextAction = await p.select(
    "O que fazer agora?",
    [
      { value: "start", label: "🚀 Iniciar servidor agora", hint: "arkhe start" },
      { value: "status", label: "📊 Ver status", hint: "arkhe status" },
      { value: "later", label: "⏰ Fazer isso depois", hint: "sair do wizard" },
    ],
    "start"
  );

  if (nextAction === "start") {
    p.note("Iniciando servidor…", "Start");
    const { startServices } = await import("../service.js");
    await startServices({ foreground: false, apiOnly: false, wsOnly: false });

    const startSpinner = p.progress("Aguardando servidor ficar pronto…");
    let started = false;
    for (let i = 0; i < 15; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      if (await probeServer(apiHost, apiPort)) {
        started = true;
        break;
      }
    }
    if (started) {
      startSpinner.stop("✓ Servidor Arkhe ativo e respondendo!");
    } else {
      startSpinner.stop("⚠  Servidor não respondeu em 15s. Verifique com 'arkhe status'");
    }
  } else if (nextAction === "status") {
    const { showStatus } = await import("../service.js");
    await showStatus();
  }

  p.outro("✓ Onboarding completo!  Execute 'arkhe status' a qualquer momento.");
  return 0;
}

// ──────────────────────────────────────────────────────────────────────────
// collectConfig — coleta ramificada por flow
// ──────────────────────────────────────────────────────────────────────────

async function collectConfig(
  p: WizardPrompter,
  existing: Record<string, string>,
  flow: string,
  _configAction: string
): Promise<Record<string, string>> {
  const config: Record<string, string> = {};

  Object.assign(config, await stepLlmCredentials(p, existing, flow));
  Object.assign(config, await stepServerConfig(p, existing, flow));
  Object.assign(config, await stepChannels(p, existing, flow));
  Object.assign(config, await stepSecurityTokens(p, existing, flow));

  return config;
}

// ──────────────────────────────────────────────────────────────────────────
// setupDaemon — instala serviço systemd/launchd
// ──────────────────────────────────────────────────────────────────────────

async function setupDaemon(
  p: WizardPrompter,
  env: EnvDetector,
  projectRoot: string,
  envPath: string,
  flow: string
): Promise<void> {
  if (!env.hasSystemd && !env.hasLaunchd) {
    p.note(
      "Nenhum gerenciador de serviços detectado (systemd/launchd).\n" +
      "Use 'arkhe start' para iniciar manualmente.",
      "Daemon"
    );
    return;
  }

  let install: boolean;
  if (flow === "quickstart") {
    install = true;
    p.note("Serviço será instalado automaticamente.", "Daemon (QuickStart)");
  } else {
    const daemonType = env.hasSystemd ? "systemd" : "launchd";
    install = await p.confirm(`Instalar serviço ${daemonType} para iniciar no boot?`, true);
  }

  if (!install) return;

  const spinner = p.progress("Instalando serviço…");
  try {
    const { installDaemon } = await import("../service.js");
    const rc = await installDaemon({ projectRoot, envPath });
    if (rc === 0) {
      spinner.stop("✓ Serviço instalado com sucesso");
    } else {
      spinner.stop("⚠  Serviço não pôde ser instalado (use arkhe start manualmente)");
    }
  } catch {
    spinner.stop("⚠  Erro ao instalar serviço");
  }
}

// ──────────────────────────────────────────────────────────────────────────
// showSummary — exibe resumo da configuração gerada
// ──────────────────────────────────────────────────────────────────────────

function showSummary(p: WizardPrompter, config: Record<string, string>): void {
  const lines: string[] = [];
  for (const [k, v] of Object.entries(config)) {
    let display: string;
    if (k.includes("KEY") || k.includes("TOKEN")) {
      display = v.length > 8
        ? `${"*".repeat(Math.min(v.length - 6, 20))}…${v.slice(-6)}`
        : "***";
    } else {
      display = v;
    }
    lines.push(`  ${k} = ${display}`);
  }
  p.note(lines.join("\n"), "Resumo da configuração");
}
