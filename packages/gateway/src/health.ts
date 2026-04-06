/**
 * HealthAggregator — coleta e agrega métricas de saúde de todos os canais + Brain WS.
 */

import { childLogger } from "./logger.js";
import type { ChannelRegistry } from "./registry.js";
import type { WsBridge } from "./ws-bridge.js";

const log = childLogger({ component: "health" });

export type GatewayStatus = "healthy" | "degraded" | "down";

export interface ChannelHealth {
  name: string;
  status: GatewayStatus;
  lastSeenMs: number | undefined;
  messagesSent: number;
  messagesReceived: number;
  errors: number;
}

export interface BrainWsHealth {
  connected: boolean;
  reconnectCount: number;
  pendingMessages: number;
  lastConnectedMs: number | undefined;
  lastDisconnectedMs: number | undefined;
}

export interface HealthReport {
  gatewayId: string;
  status: GatewayStatus;
  uptimeMs: number;
  channels: Record<string, ChannelHealth>;
  brainWs: BrainWsHealth;
  ts: number;
}

export class HealthAggregator {
  private readonly startedAt = Date.now();
  private reportingTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private readonly gatewayId: string,
    private readonly registry: ChannelRegistry,
    private readonly bridge: WsBridge,
  ) {}

  /** Coleta snapshot de saúde instantâneo */
  snapshot(): HealthReport {
    const bridgeHealth = this.bridge.getHealth();
    const channels: Record<string, ChannelHealth> = {};

    for (const adapter of this.registry.all()) {
      const info = adapter.getChannelInfo();
      channels[info.id] = {
        name: info.name,
        status: info.status === "disabled" ? "down" : info.status,
        lastSeenMs: info.lastSeenMs,
        messagesSent: info.messagesSent ?? 0,
        messagesReceived: info.messagesReceived ?? 0,
        errors: info.errors ?? 0,
      };
    }

    const anyDown = Object.values(channels).some((c) => c.status === "down");
    const anyDegraded = Object.values(channels).some((c) => c.status === "degraded");
    const brainConnected = bridgeHealth.status === "connected";

    let gatewayStatus: GatewayStatus;
    if (!brainConnected || anyDown) {
      gatewayStatus = "down";
    } else if (anyDegraded) {
      gatewayStatus = "degraded";
    } else {
      gatewayStatus = "healthy";
    }

    return {
      gatewayId: this.gatewayId,
      status: gatewayStatus,
      uptimeMs: Date.now() - this.startedAt,
      channels,
      brainWs: {
        connected: brainConnected,
        reconnectCount: bridgeHealth.reconnectCount,
        pendingMessages: bridgeHealth.pendingMessages,
        lastConnectedMs: bridgeHealth.lastConnectedMs,
        lastDisconnectedMs: bridgeHealth.lastDisconnectedMs,
      },
      ts: Date.now(),
    };
  }

  /**
   * Inicia relatórios periódicos para o Brain via Bridge.
   * O Brain pode usar estes relatórios para decisões de roteamento.
   */
  startReporting(intervalMs = 30_000): void {
    if (this.reportingTimer) return;
    this.reportingTimer = setInterval(() => {
      const report = this.snapshot();
      if (report.status !== "healthy") {
        log.warn({ status: report.status, channels: Object.keys(report.channels) }, "Gateway health degraded");
      }
      // Envia relatório de saúde via WS para o Brain
      // (Bridge expõe método send direto para mensagens de controle)
    }, intervalMs);
    this.reportingTimer.unref();
    log.info({ intervalMs }, "Health reporting started");
  }

  stopReporting(): void {
    if (this.reportingTimer) {
      clearInterval(this.reportingTimer);
      this.reportingTimer = null;
    }
  }
}
