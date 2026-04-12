# ADR-002: Pipeline de Envelope em 3 Estágios

**Status**: Aceito  
**Data**: 2025-07-11  
**Contexto**: Coexistem três tipos de "envelope" no RLM.

## Decisão

O pipeline de mensagens segue três estágios, cada um com sua estrutura:

| Estágio | Módulo | Tipo | Responsabilidade |
|---|---|---|---|
| 1. Normalização | `rlm/gateway/message_envelope.py` | `InboundMessage` | Gateways normalizam mensagens nativas (WhatsApp, Telegram, Slack) para um formato canônico imutável. |
| 2. Validação/Transfer | `rlm/gateway/envelope.py` | `Envelope` | Envelope schema-validated (`schemas/envelope.v1.json`) para transferência TS↔Python via WebSocket. Inclui adapter `inbound_message_to_envelope()`. |
| 3. Roteamento/Entrega | `rlm/core/comms/envelope.py` | `Envelope` | Envelope do MessageBus com campos de retry, prioridade, direção, correlação. Consumido pelo pipeline de routing e delivery. |

## Fluxo

```
Gateway nativo → InboundMessage (normalização)
                    ↓
             gateway.Envelope (validação schema v1, WebSocket transfer)
                    ↓
             MessageBus.ingest() → comms.Envelope (routing/delivery)
                    ↓
             RoutingPolicy → comms.Envelope(direction=OUTBOUND)
                    ↓
             DeliveryWorker → ChannelRegistry → Gateway nativo
```

## Regras

1. **Nenhum gateway deve produzir `comms.Envelope` diretamente** — sempre passa por `InboundMessage` primeiro.
2. **`InboundMessage` é imutável (frozen)** — nenhum middleware pode mutá-lo.
3. **`comms.Envelope` não herda de `InboundMessage`** — são tipos distintos por design.
4. **Ambiguidade de nome**: `Envelope` existe em dois módulos. Sempre use o import qualificado ou alias (`from rlm.gateway.envelope import Envelope as GatewayEnvelope`).

## Consequências

- Três arquivos permanecem. Não fundir.
- Qualquer novo campo de roteamento vai em `comms.Envelope`.
- Qualquer novo campo de normalização/canal vai em `InboundMessage`.
- Qualquer alteração de schema TS↔Python vai em `gateway.Envelope`.
