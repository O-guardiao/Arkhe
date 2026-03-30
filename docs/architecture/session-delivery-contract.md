# Contrato de Sessao e Entrega do RLM

## Objetivo

Separar identidade conversacional, origem do request e rota persistida de entrega.
O RLM nao deve inferir entrega assíncrona a partir de um campo mutavel de origem.

## Campos canonicos

- `user_id`
  - Chave unificada da conversa e da memoria de longo prazo.
  - E resolvida a partir do `client_id` conforme `RLM_SESSION_SCOPE`.

- `session_id`
  - Identidade da sessao viva do servidor.
  - Agrupa estado REPL, `memory.db`, runtime workbench e logs.

- `originating_channel`
  - Canal do request corrente.
  - No storage atual, permanece compatível como alias de `client_id`.
  - Serve para reply sincrono no mesmo request quando o canal e replyable.

- `delivery_context`
  - Rota persistida para entregas assíncronas, retomadas e callbacks posteriores.
  - E armazenado separado do `originating_channel`.
  - So deve ser atualizado automaticamente por canais replyable reais.
  - TUI e webchat nao devem roubar essa rota por terem sido a ultima origem observada.

- `session_status`
  - Estado operacional persistido da sessao.
  - Valores correntes do runtime: `idle`, `running`, `error`, `completed`.
  - O resultado do turno continua vindo do Supervisor (`completed`, `timeout`, `aborted`, `error`, `error_loop`), mas a sessao viva volta para `idle` quando pode ser reutilizada.

- `operation_log`
  - Trilha append-only de operacoes estruturadas por sessao.
  - Implementada sobre `event_log` com `event_type = session_operation`.
  - Serve para inspecao operacional e correlacao, nao para UI especifica.

## Regras de roteamento

1. `SessionManager.get_or_create(client_id)` resolve primeiro o `user_id`.
2. Todo request atualiza o `originating_channel` da sessao viva.
3. `delivery_context` so e atualizado automaticamente quando o canal de entrada e replyable via `ChannelRegistry`.
4. Resposta sincronica usa o `originating_channel` capturado no request, nunca o valor mutavel da sessao depois.
5. Entrega assíncrona ou queued deve usar rota explicita ou `delivery_context` persistido.
6. `client_id` continua existindo por compatibilidade, mas nao e mais tratado como verdade unica de entrega.

## Hooks de ciclo de vida

- `session.created`
- `session.closed`
- `session.status_changed`
- `session.origin.updated`
- `session.delivery.updated`
- `session.operation`

## Storage

Tabela `sessions`:

- `client_id`: alias legado de `originating_channel`
- `user_id`: identidade unificada
- `status`: `session_status`
- `delivery_context`: JSON persistido

Tabela `event_log`:

- eventos historicos livres continuam existindo
- entradas estruturadas de operacao usam `event_type = session_operation`

## Consequencia pratica

O canal mais recente e apenas origem observada. A rota padrao de entrega passa a ser um dado persistido e deliberado. Isso elimina a sobrecarga dupla de `client_id` e prepara o RLM para canais mistos sem corromper a entrega assíncrona.