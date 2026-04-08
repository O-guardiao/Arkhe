# rlm.gateway

Pacote canônico do subsistema de gateway Python.

## O que pertence aqui

- adapters e routers de canal (`telegram_gateway`, `discord_gateway`, `slack_gateway`, `whatsapp_gateway`, `webchat`, `operator_bridge`)
- protocolos de entrada/saída de gateway (`envelope`, `message_envelope`)
- montagem de routers e transporte (`transport_router`, `ws_gateway_endpoint`, `webhook_dispatch`)
- utilitários específicos do subsistema (`auth_helpers`, `backoff`, `heartbeat`, `chunker`, `gateway_state`)

## O que não pertence aqui

- ciclo principal do servidor FastAPI e lifespan
- runtime pipeline do brain
- monitoramento, drain, ws server e infraestrutura genérica do processo

Esses componentes continuam em `rlm.server`.

## Regra de fronteira

- `rlm.gateway` recebe, autentica, normaliza e monta tráfego de canais.
- `rlm.server` orquestra o processo HTTP principal e entrega para o brain.
- `rlm.core` continua sendo a fonte de verdade para runtime, recursão, message bus e observabilidade.

## Compatibilidade

Os caminhos antigos em `rlm.server.*` permanecem como shims de compatibilidade.
Código novo deve importar diretamente de `rlm.gateway.*`.
