/**
 * Comando `rlm doctor` — diagnóstico completo do ambiente RLM.
 *
 * Verifica:
 *   1. Gateway acessível
 *   2. Brain conectado
 *   3. WS bridge conectada
 *   4. Canais configurados e saudáveis
 *   5. Variáveis de ambiente críticas
 *
 * Exit 0 = tudo ok, Exit 1 = algum problema detectado.
 *
 * Uso:
 *   rlm doctor
 *   rlm doctor --json
 */

import { Command } from "commander";
import { RlmClient, RlmApiError } from "../client.js";
import { c, printError } from "../format.js";

type CheckStatus = "ok" | "warn" | "fail";

interface Check {
  name: string;
  status: CheckStatus;
  message: string;
}

function icon(status: CheckStatus): string {
  if (status === "ok") return c.success("✓");
  if (status === "warn") return c.warn("⚠");
  return c.error("✗");
}

export function makeDoctorCommand(): Command {
  return new Command("doctor")
    .description("Diagnóstico completo do ambiente RLM")
    .option("--json", "Saída em JSON bruto", false)
    .option("--fail-fast", "Para na primeira falha", false)
    .action(async (opts: { json: boolean; failFast: boolean }) => {
      const client = new RlmClient();
      const checks: Check[] = [];
      let hasFailure = false;

      async function check(name: string, fn: () => Promise<Check>): Promise<void> {
        if (opts.failFast && hasFailure) return;
        try {
          const result = await fn();
          checks.push(result);
          if (result.status === "fail") hasFailure = true;
        } catch (err) {
          const result: Check = { name, status: "fail", message: String(err) };
          checks.push(result);
          hasFailure = true;
        }
      }

      // 1. Gateway acessível
      await check("gateway", async () => {
        const start = Date.now();
        const health = await client.get<{ status: string }>("/health");
        const latency = Date.now() - start;
        if (health.status === "ok" || health.status === "healthy") {
          return { name: "gateway", status: "ok", message: `acessível (${latency}ms)` };
        }
        return { name: "gateway", status: "warn", message: `status=${health.status}` };
      });

      // 2. Brain conectado
      await check("brain", async () => {
        try {
          const health = await client.get<{ status: string }>("/brain/health");
          if (health.status === "ok" || health.status === "healthy") {
            return { name: "brain", status: "ok", message: "Brain Python conectado" };
          }
          return { name: "brain", status: "warn", message: `status=${health.status}` };
        } catch (err) {
          if (err instanceof RlmApiError && err.statusCode === 503) {
            return { name: "brain", status: "fail", message: "Brain desconectado (ws-bridge offline)" };
          }
          throw err;
        }
      });

      // 3. WS bridge
      await check("ws-bridge", async () => {
        const data = await client.get<{ bridge?: { connected: boolean } }>("/health");
        const connected = data.bridge?.connected ?? false;
        return {
          name: "ws-bridge",
          status: connected ? "ok" : "fail",
          message: connected ? "WebSocket bridge conectada" : "Bridge desconectada",
        };
      });

      // 4. Canais
      await check("channels", async () => {
        const data = await client.get<{ channels?: Record<string, { status: string }> }>("/health");
        const channels = data.channels ?? {};
        const total = Object.keys(channels).length;
        if (total === 0) {
          return { name: "channels", status: "warn", message: "Nenhum canal configurado" };
        }
        const unhealthy = Object.entries(channels)
          .filter(([, v]) => v.status !== "healthy" && v.status !== "ok")
          .map(([k]) => k);
        if (unhealthy.length > 0) {
          return { name: "channels", status: "warn", message: `${total} canal(is), degradados: ${unhealthy.join(", ")}` };
        }
        return { name: "channels", status: "ok", message: `${total} canal(is) saudáveis` };
      });

      // 5. Variáveis críticas
      await check("env", async () => {
        const missing: string[] = [];
        const optional: string[] = [];
        const required = ["RLM_TOKEN"];
        for (const key of required) {
          if (!process.env[key]) missing.push(key);
        }
        const optionalKeys = ["RLM_HOST", "RLM_PORT"];
        for (const key of optionalKeys) {
          if (!process.env[key]) optional.push(key);
        }
        if (missing.length > 0) {
          return { name: "env", status: "fail", message: `Vars obrigatórias ausentes: ${missing.join(", ")}` };
        }
        if (optional.length > 0) {
          return { name: "env", status: "warn", message: `Vars opcionais não definidas: ${optional.join(", ")} (usando defaults)` };
        }
        return { name: "env", status: "ok", message: "Variáveis de ambiente configuradas" };
      });

      // Output
      if (opts.json) {
        process.stdout.write(JSON.stringify({ checks, ok: !hasFailure }, null, 2) + "\n");
        process.exit(hasFailure ? 1 : 0);
      }

      process.stdout.write("\n");
      for (const chk of checks) {
        process.stdout.write(`  ${icon(chk.status)}  ${c.bold(chk.name.padEnd(12))} ${chk.message}\n`);
      }
      process.stdout.write("\n");

      if (hasFailure) {
        printError("Foram encontrados problemas. Verifique os itens marcados com ✗.");
        process.exit(1);
      } else {
        process.stdout.write(`${c.success("Tudo ok!")} Sistema RLM operacional.\n\n`);
      }
    });
}
