/**
 * Steps do wizard — coleta de configurações por seção.
 *
 * Migrado de rlm/cli/wizard/steps.py
 */

import * as crypto from "node:crypto";
import type { WizardPrompter } from "./prompter.js";
import {
  CHANNEL_SPECS,
  CHANNEL_TEST_FNS,
} from "./channels.js";
import {
  LLM_SECTION_KEYS,
  MODEL_ROLE_SPECS,
  buildRoleModelDefaults,
  formatRoleModelSummary,
  getModelOptions,
  promptModelName,
  testOpenAiKey,
} from "./env-utils.js";

// ──────────────────────────────────────────────────────────────────────────
// stepLlmCredentials
// ──────────────────────────────────────────────────────────────────────────

export async function stepLlmCredentials(
  p: WizardPrompter,
  existing: Record<string, string>,
  flow: string
): Promise<Record<string, string>> {
  const config: Record<string, string> = {};

  const provider = await p.select(
    "Provedor LLM",
    [
      { value: "openai", label: "OpenAI", hint: "GPT-5.4, mini, nano" },
      { value: "anthropic", label: "Anthropic", hint: "Claude 3.5, Claude 4" },
      { value: "google", label: "Google AI", hint: "Gemini Pro, Gemini Flash" },
      { value: "custom", label: "Outro / OpenAI-compatível", hint: "LM Studio, Ollama, etc." },
      { value: "skip", label: "Pular", hint: "configurar depois" },
    ],
    "openai"
  );

  if (provider === "skip") {
    for (const k of LLM_SECTION_KEYS) {
      if (existing[k]) config[k] = existing[k];
    }
    return config;
  }

  // Seleção do modelo
  const models = getModelOptions(provider);
  const existingModel = existing["RLM_MODEL"] ?? "";

  let selectedModel: string;
  if (flow === "quickstart" && provider !== "custom") {
    selectedModel = existingModel || models[0]!.value;
    p.note(`Modelo selecionado automaticamente: ${selectedModel}`, "QuickStart");
  } else {
    selectedModel = await promptModelName(p, provider, "Modelo padrão", existingModel, models);
  }

  config["RLM_MODEL"] = selectedModel;

  const hasRoleModels = MODEL_ROLE_SPECS.some(([envName]) => !!existing[envName]);
  let routeModeDefault = flow === "quickstart" ? "single" : "recommended";
  if (hasRoleModels) routeModeDefault = "manual";

  const routeMode = await p.select(
    "Estratégia de modelos",
    [
      { value: "single", label: "Um único modelo", hint: "usa apenas RLM_MODEL e limpa overrides antigos" },
      { value: "recommended", label: "Split recomendado", hint: "preenche planner, worker, fast e minirepl automaticamente" },
      { value: "manual", label: "Escolher por papel", hint: "configurar planner, worker, evaluator, fast e minirepl" },
    ],
    routeModeDefault
  );

  const roleDefaults = buildRoleModelDefaults(existing, provider, selectedModel);

  if (routeMode === "recommended") {
    Object.assign(config, roleDefaults);
    p.note(formatRoleModelSummary(roleDefaults), "Modelos por papel");
  } else if (routeMode === "manual") {
    for (const [envName, label, description] of MODEL_ROLE_SPECS) {
      config[envName] = await promptModelName(
        p,
        provider,
        `Modelo para ${label} (${description})`,
        roleDefaults[envName] ?? selectedModel,
        models
      );
    }
    p.note(formatRoleModelSummary(config), "Modelos por papel");
  } else {
    p.note(
      `  • Todos os papéis usarão ${selectedModel}\n` +
      "  • Overrides RLM_MODEL_* antigos serão removidos ao salvar",
      "Modelos"
    );
  }

  // API Key
  const keyMap: Record<string, string> = {
    openai: "OPENAI_API_KEY",
    anthropic: "ANTHROPIC_API_KEY",
    google: "GOOGLE_API_KEY",
    custom: "OPENAI_API_KEY",
  };
  const keyName = keyMap[provider]!;
  const existingKey = existing[keyName] ?? "";
  const masked = existingKey.length > 10 ? `…${existingKey.slice(-6)}` : "";

  if (masked && flow === "quickstart") {
    config[keyName] = existingKey;
    p.note(`Usando API key existente (${masked})`, "QuickStart");
  } else {
    let promptMsg = `API Key (${keyName})`;
    if (masked) promptMsg += `  atual: ${masked}`;

    const apiKey = await p.text(promptMsg, {
      default: existingKey,
      password: !masked,
    });
    if (apiKey) {
      config[keyName] = apiKey;
    }

    if (provider === "openai" && apiKey && apiKey.startsWith("sk-")) {
      const shouldTest = await p.confirm("Testar chave OpenAI agora?", false);
      if (shouldTest) {
        const spinner = p.progress("Verificando…");
        const ok = await testOpenAiKey(apiKey);
        if (ok) {
          spinner.stop("✓ Chave válida — conexão OK");
        } else {
          spinner.stop("⚠  Não validada — verifique depois");
        }
      }
    }
  }

  return config;
}

// ──────────────────────────────────────────────────────────────────────────
// stepServerConfig
// ──────────────────────────────────────────────────────────────────────────

export async function stepServerConfig(
  p: WizardPrompter,
  existing: Record<string, string>,
  flow: string
): Promise<Record<string, string>> {
  const config: Record<string, string> = {};

  const defaults = {
    RLM_API_HOST: existing["RLM_API_HOST"] ?? "127.0.0.1",
    RLM_API_PORT: existing["RLM_API_PORT"] ?? "5000",
    RLM_WS_HOST: existing["RLM_WS_HOST"] ?? "127.0.0.1",
    RLM_WS_PORT: existing["RLM_WS_PORT"] ?? "8765",
  };

  if (flow === "quickstart") {
    Object.assign(config, defaults);
    p.note(
      `  • API REST:  ${defaults.RLM_API_HOST}:${defaults.RLM_API_PORT}\n` +
      `  • WebSocket: ${defaults.RLM_WS_HOST}:${defaults.RLM_WS_PORT}`,
      "Servidor (defaults)"
    );
    return config;
  }

  const validatePort = (v: string): string | undefined => {
    const n = parseInt(v, 10);
    if (isNaN(n) || n < 1 || n > 65535) return "Porta deve estar entre 1 e 65535";
    return undefined;
  };

  const bindChoice = await p.select(
    "Bind do servidor (quem pode acessar?)",
    [
      { value: "loopback", label: "Loopback (127.0.0.1)", hint: "apenas esta máquina" },
      { value: "lan", label: "LAN (0.0.0.0)", hint: "acessível na rede local" },
      { value: "custom", label: "IP customizado", hint: "definir manualmente" },
    ],
    "loopback"
  );

  let host: string;
  if (bindChoice === "loopback") {
    host = "127.0.0.1";
  } else if (bindChoice === "lan") {
    host = "0.0.0.0";
  } else {
    host = await p.text("Endereço IP para bind", {
      default: defaults.RLM_API_HOST,
      validate: (v) => v ? undefined : "IP não pode ser vazio",
    });
  }

  config["RLM_API_HOST"] = host;
  config["RLM_WS_HOST"] = host;
  config["RLM_API_PORT"] = await p.text("Porta da API REST", {
    default: defaults.RLM_API_PORT,
    validate: validatePort,
  });
  config["RLM_WS_PORT"] = await p.text("Porta do WebSocket", {
    default: defaults.RLM_WS_PORT,
    validate: validatePort,
  });

  return config;
}

// ──────────────────────────────────────────────────────────────────────────
// stepChannels
// ──────────────────────────────────────────────────────────────────────────

export async function stepChannels(
  p: WizardPrompter,
  existing: Record<string, string>,
  flow: string
): Promise<Record<string, string>> {
  const config: Record<string, string> = {};

  const preConfigured = CHANNEL_SPECS
    .filter((spec) => spec.vars.some((v) => !!existing[v.key]))
    .map((spec) => spec.name);

  if (flow === "quickstart") {
    if (preConfigured.length > 0) {
      p.note(`Canais já configurados: ${preConfigured.join(", ")}`, "Canais (QuickStart)");
      for (const spec of CHANNEL_SPECS) {
        for (const v of spec.vars) {
          if (existing[v.key]) config[v.key] = existing[v.key];
        }
      }
    }
    const addChannels = await p.confirm(
      "Configurar canais de comunicação (Telegram, Discord, etc.)?",
      preConfigured.length === 0
    );
    if (!addChannels) return config;
  } else {
    p.note(
      "Canais permitem que o Arkhe receba e envie mensagens\n" +
      "por Telegram, Discord, WhatsApp e Slack.\n" +
      "WebChat está sempre ativo e não requer configuração.",
      "Canais de comunicação"
    );
  }

  for (const spec of CHANNEL_SPECS) {
    const hasExisting = spec.vars.some((v) => !!existing[v.key]);

    const label = hasExisting ? `(${spec.name} já tem tokens)` : "";
    const enable = await p.confirm(
      `Configurar ${spec.name}? ${label}`.trimEnd(),
      hasExisting
    );

    if (!enable) {
      for (const v of spec.vars) {
        if (existing[v.key]) config[v.key] = existing[v.key];
      }
      continue;
    }

    p.note(spec.hint, `${spec.name} — Setup`);

    let testTokenValue = "";
    for (const varSpec of spec.vars) {
      const existingVal = existing[varSpec.key] ?? "";
      const masked = existingVal.length > 8 ? `…${existingVal.slice(-6)}` : "";

      let promptMsg = varSpec.key;
      if (masked) promptMsg += `  atual: ${masked}`;
      if (!varSpec.required) promptMsg += "  (opcional)";

      const val = await p.text(promptMsg, {
        default: existingVal,
        password: !masked && varSpec.key.toUpperCase().includes("TOKEN"),
      });

      if (val) {
        config[varSpec.key] = val;
        if (varSpec.key.endsWith("_BOT_TOKEN")) testTokenValue = val;
      } else if (existingVal) {
        config[varSpec.key] = existingVal;
      }
    }

    // Teste de conectividade
    const testFnName = spec.testFn;
    const testFn = testFnName ? CHANNEL_TEST_FNS[testFnName] : undefined;

    if (testFn && testTokenValue) {
      const shouldTest = await p.confirm(`Testar token do ${spec.name} agora?`, true);
      if (shouldTest) {
        const spinner = p.progress(`Conectando ao ${spec.name}…`);
        const [ok, msg] = await testFn(testTokenValue);
        if (ok) {
          spinner.stop(`✓ ${spec.name} OK — ${msg}`);
        } else {
          spinner.stop(`⚠  ${spec.name} falhou: ${msg}`);
        }
      }
    }
  }

  p.note("WebChat está sempre ativo (sem tokens necessários).", "WebChat");
  return config;
}

// ──────────────────────────────────────────────────────────────────────────
// stepSecurityTokens
// ──────────────────────────────────────────────────────────────────────────

const TOKEN_SPECS: Array<[string, string]> = [
  ["RLM_WS_TOKEN", "WebSocket / observabilidade"],
  ["RLM_INTERNAL_TOKEN", "API interna /webhook/{client_id}"],
  ["RLM_ADMIN_TOKEN", "rotas administrativas e health"],
  ["RLM_HOOK_TOKEN", "webhooks externos /api/hooks"],
  ["RLM_API_TOKEN", "API OpenAI-compatible /v1"],
];

function generateToken(): string {
  return crypto.randomBytes(32).toString("hex");
}

export async function stepSecurityTokens(
  p: WizardPrompter,
  existing: Record<string, string>,
  flow: string
): Promise<Record<string, string>> {
  const config: Record<string, string> = {};

  if (flow === "quickstart") {
    let generated = 0;
    let kept = 0;
    for (const [envName] of TOKEN_SPECS) {
      if (existing[envName]) {
        config[envName] = existing[envName];
        kept++;
      } else {
        config[envName] = generateToken();
        generated++;
      }
    }
    const parts: string[] = [];
    if (generated > 0) parts.push(`${generated} gerados`);
    if (kept > 0) parts.push(`${kept} mantidos`);
    p.note(`Tokens de segurança: ${parts.join(", ")}`, "Segurança (auto)");
    return config;
  }

  // Advanced: perguntar por cada token existente
  for (const [envName, label] of TOKEN_SPECS) {
    const existingValue = existing[envName] ?? "";
    if (existingValue) {
      const regenerate = await p.confirm(
        `${envName} (${label}) já existe (…${existingValue.slice(-6)}). Regenerar?`,
        false
      );
      config[envName] = regenerate ? generateToken() : existingValue;
    } else {
      config[envName] = generateToken();
    }
  }

  p.note(`${TOKEN_SPECS.length} tokens de segurança configurados.`, "✓ Segurança");
  return config;
}
