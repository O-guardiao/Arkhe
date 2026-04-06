/**
 * ChannelRegistry — roteamento de Envelopes para os adaptadores corretos.
 *
 * Registra adaptadores por nome de canal e:
 *  - Recebe respostas do Brain e as despacha via sendViaEnvelope()
 *  - Fornece lista de canais ativos para inspeção de saúde
 */

import { childLogger } from "./logger.js";
import type { ChannelAdapter } from "./adapters/interface.js";
import type { Envelope } from "./envelope.js";
import type { WsBridge } from "./ws-bridge.js";

const log = childLogger({ component: "registry" });

export class ChannelRegistry {
  private readonly adapters = new Map<string, ChannelAdapter>();
  private unsubscribeBridge: (() => void) | null = null;

  constructor(private readonly bridge: WsBridge) {}

  /** Registra um adaptador de canal */
  register(adapter: ChannelAdapter): void {
    if (this.adapters.has(adapter.channelName)) {
      throw new Error(`Adapter already registered: ${adapter.channelName}`);
    }
    this.adapters.set(adapter.channelName, adapter);
    log.info({ channel: adapter.channelName }, "Channel adapter registered");
  }

  /** Conecta ao Brain para receber respostas */
  attachBridge(): void {
    if (this.unsubscribeBridge) return;
    this.unsubscribeBridge = this.bridge.onReply((envelope) => {
      void this.handleBrainReply(envelope);
    });
  }

  /** Desconecta do Brain */
  detachBridge(): void {
    if (this.unsubscribeBridge) {
      this.unsubscribeBridge();
      this.unsubscribeBridge = null;
    }
  }

  /** Retorna todos os adaptadores registrados */
  all(): ChannelAdapter[] {
    return [...this.adapters.values()];
  }

  /** Retorna adaptador por nome, ou undefined */
  get(channel: string): ChannelAdapter | undefined {
    return this.adapters.get(channel);
  }

  /** Envia um envelope de entrada ao Brain */
  forwardToBrain(envelope: Envelope): boolean {
    return this.bridge.sendEnvelope(envelope);
  }

  /**
   * Recebe um envelope de saída do Brain e encaminha ao canal correto.
   * O target_id deve ser o chatId / destinatário no canal.
   */
  private async handleBrainReply(envelope: Envelope): Promise<void> {
    const adapter = this.adapters.get(envelope.target_channel ?? "");

    if (!adapter) {
      log.warn({ channel: envelope.target_channel, id: envelope.id }, "No adapter for channel in reply envelope");
      return;
    }

    const chatId = envelope.target_id;
    const text = envelope.text;

    if (!chatId) {
      log.warn({ id: envelope.id }, "Reply envelope missing target_id");
      return;
    }

    try {
      const result = await adapter.sendMessage(chatId, text, envelope);
      if (!result.ok) {
        log.error({ channel: envelope.target_channel, chatId, error: result.error }, "sendMessage failed");
      } else {
        log.info({ channel: envelope.target_channel, chatId, messageId: result.messageId }, "Reply sent");
      }
    } catch (err) {
      log.error({ err, channel: envelope.target_channel, chatId }, "Unhandled error in sendMessage");
    }
  }
}
