"""
rlm.core.comms — Multichannel delivery pipeline + CrossChannel identity.

─── Pipeline multichannel ────────────────────────────────────────────────
  envelope              Unidade atômica de mensagem: Envelope, Direction, MessageType.
  message_bus           Ingest, routing e enqueue central (singleton: get_message_bus).
  outbox                Persistência SQLite transacional (Transactional Outbox Pattern).
  delivery_worker       Loop assíncrono que drena o Outbox e entrega via adapters.
  routing_policy        Chain-of-responsibility para decisão de destino outbound.

─── Saúde e registro de canal ────────────────────────────────────────────
  channel_bootstrap     Inicialização unificada de toda infraestrutura multichannel.
  channel_status        Registro centralizado de estado runtime dos canais.
  channel_probe         Verificação de identidade e saúde de canais via API nativa.

─── Identidade cross-channel ─────────────────────────────────────────────
  crosschannel_identity  Mapeamento de um indivíduo em múltiplos canais (SQLite).

                         O mesmo usuário no Telegram, Discord e Slack é reconhecido
                         como a mesma pessoa. RoutingPolicy.UserPreferenceRule consulta
                         este store para entregar respostas no canal preferido do
                         indivíduo — independente de qual canal originou a mensagem.

                         API:  link(), resolve(), get_preferred(), set_preferred()
                         Singleton: init_crosschannel_identity(), get_crosschannel_identity()

─── Shims (compatibilidade — use os caminhos canônicos) ──────────────────
  mcp_client     →  rlm.core.integrations.mcp_client
  comms_utils    →  rlm.core.engine.comms_utils
"""
