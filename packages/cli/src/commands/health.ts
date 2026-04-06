/**
 * Comando `rlm health` — verifica estado do servidor RLM.
 *
 * Uso:
 *   rlm health
 *   rlm health --brain
 */

import { Command } from "commander";
import { RlmClient, RlmApiError } from "../client.js";
import { c, fmtTimestamp, printError } from "../format.js";

interface GatewayHealth {
  status: string;
  channels: Record<string, { status: string; inbound_count: number }>;
  bridge: { connected: boolean };
  timestamp: number;
}

interface BrainHealth {
  status: string;
  tool_count: number;
  timestamp: number;
}

export function makeHealthCommand(): Command {
  return new Command("health")
    .description("Verifica status do servidor RLM (gateway + brain)")
    .option("--gateway", "Apenas health do gateway", false)
    .option("--brain", "Apenas health do brain", false)
    .option("--json", "Saída em JSON bruto", false)
    .action(async (opts: { gateway: boolean; brain: boolean; json: boolean }) => {
      const client = new RlmClient();
      const checkGateway = !opts.brain || opts.gateway;
      const checkBrain = !opts.gateway || opts.brain;

      let gatewayData: GatewayHealth | null = null;
      let brainData: BrainHealth | null = null;
      let hasError = false;

      if (checkGateway) {
        try {
          gatewayData = await client.get<GatewayHealth>("/health");
        } catch (err) {
          if (err instanceof RlmApiError && err.statusCode >= 500) {
            hasError = true;
          } else {
            hasError = true;
          }
        }
      }

      if (checkBrain) {
        try {
          brainData = await client.get<BrainHealth>("/brain/health");
        } catch {
          hasError = true;
        }
      }

      if (opts.json) {
        console.log(JSON.stringify({ gateway: gatewayData, brain: brainData }, null, 2));
        if (hasError) process.exit(1);
        return;
      }

      console.log();

      if (gatewayData !== null) {
        const st = gatewayData.status;
        const icon =
          st === "healthy" ? c.success("●") : st === "degraded" ? c.warn("●") : c.error("●");
        console.log(`${icon} Gateway: ${c.bold(st)}`);

        const bridge = gatewayData.bridge?.connected ? c.success("conectado") : c.error("desconectado");
        console.log(`   Bridge:  ${bridge}`);

        const channels = Object.entries(gatewayData.channels ?? {});
        if (channels.length > 0) {
          for (const [name, info] of channels) {
            const cs =
              info.status === "active" ? c.success("●") : c.warn("●");
            console.log(`   ${cs} ${name}: ${info.inbound_count} msgs recebidas`);
          }
        }

        if (gatewayData.timestamp) {
          console.log(c.dim(`   última atualização: ${fmtTimestamp(gatewayData.timestamp)}`));
        }
        console.log();
      } else if (checkGateway) {
        console.log(c.error("● Gateway: inacessível"));
      }

      if (brainData !== null) {
        const icon = brainData.status === "healthy" ? c.success("●") : c.error("●");
        console.log(`${icon} Brain: ${c.bold(brainData.status)}`);
        console.log(`   ferramentas registradas: ${brainData.tool_count}`);
        if (brainData.timestamp) {
          console.log(c.dim(`   última atualização: ${fmtTimestamp(brainData.timestamp)}`));
        }
        console.log();
      } else if (checkBrain) {
        console.log(c.error("● Brain: inacessível"));
      }

      if (hasError) process.exit(1);
    });
}
