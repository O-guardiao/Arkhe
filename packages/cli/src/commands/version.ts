/**
 * Comando `rlm version` — exibe versões do gateway e do brain.
 *
 * Uso:
 *   rlm version
 *   rlm version --json
 */

import { Command } from "commander";
import { RlmClient, RlmApiError } from "../client.js";
import { c, printError } from "../format.js";

interface VersionInfo {
  version: string;
  commit?: string;
  build_date?: string;
  env?: string;
}

export function makeVersionCommand(): Command {
  return new Command("version")
    .aliases(["ver"])
    .description("Exibe versões do gateway e do brain")
    .option("--json", "Saída em JSON bruto", false)
    .action(async (opts: { json: boolean }) => {
      const client = new RlmClient();
      let gateway: VersionInfo | null = null;
      let brain: VersionInfo | null = null;

      await Promise.allSettled([
        (async () => {
          try {
            const h = await client.get<{ version?: string; commit?: string; env?: string }>("/health");
            gateway = { version: h.version ?? "unknown", commit: h.commit, env: h.env };
          } catch (err) {
            if (!(err instanceof RlmApiError)) throw err;
          }
        })(),
        (async () => {
          try {
            brain = await client.get<VersionInfo>("/brain/version");
          } catch {
            // brain opcional
          }
        })(),
      ]);

      if (opts.json) {
        process.stdout.write(JSON.stringify({ gateway, brain }, null, 2) + "\n");
        return;
      }

      if (!gateway && !brain) {
        printError("Servidor RLM não encontrado. Verifique RLM_HOST.");
        process.exit(1);
      }

      if (gateway) {
        const v = gateway as VersionInfo;
        process.stdout.write(
          `${c.bold("Gateway")}  ${c.success(v.version)}${v.commit ? `  commit:${v.commit.slice(0, 8)}` : ""}${v.env ? `  [${v.env}]` : ""}\n`,
        );
      }
      if (brain) {
        const v = brain as VersionInfo;
        process.stdout.write(
          `${c.bold("Brain")}     ${c.success(v.version)}${v.commit ? `  commit:${v.commit.slice(0, 8)}` : ""}\n`,
        );
      }
    });
}
