# Sistema de Logging

Este projeto tem dois mecanismos de logging com responsabilidades diferentes. A falta de clareza entre eles é a principal fonte de confusão. Este documento fecha essa lacuna.

## Visão Geral

Existem duas camadas:

1. Logging de trajetória do agente
   Arquivo: rlm/logger/rlm_logger.py
  API pública: from rlm.logger import RLMLogger
   Finalidade: persistir metadados e iterações completas do RLM em JSONL.

2. Logging estruturado de runtime
   Arquivo: rlm/core/structured_log.py
  API pública principal: RuntimeLogger, get_runtime_logger(...), get_logger(...), session_log, supervisor_log, repl_log, plugin_log, scheduler_log, gateway_log
   Finalidade: registrar eventos operacionais curtos por subsistema, com texto humano ou JSON compacto.

3. Verbose humano de terminal
  Arquivo: rlm/logger/verbose.py
  API pública: from rlm.logger import VerbosePrinter
  Finalidade: observação local da execução no terminal; não substitui persistência nem log operacional.

## Fachada recomendada

Existe agora uma fachada explícita para reduzir import errado e ambiguidade:

Arquivo: rlm/logging.py

Use:

- `from rlm.logging import TrajectoryLogger`
- `from rlm.logging import RuntimeLogger, get_runtime_logger`
- `from rlm.logging import VerbosePrinter`

Essa fachada não remove as APIs antigas. Ela apenas torna explícita a responsabilidade certa de cada tipo de log.

Regra prática:

- Se você quer analisar a execução completa do agente, use o logger de trajetória.
- Se você quer observar o comportamento do processo, do gateway ou do scheduler, use o logger estruturado.
- Se você quer apenas acompanhar a execução local no terminal, use o verbose.

## 1. Logger de Trajetória

O logger de trajetória grava um arquivo JSONL por execução lógica.

### O que ele registra

- Uma entrada inicial de metadata com configuração do RLM.
- Uma entrada por iteração com:
  - prompt
  - resposta do modelo
  - blocos de código executados
  - stdout/stderr do REPL
  - subchamadas registradas no resultado do bloco
  - resposta final quando existir
  - tempo da iteração

### Exemplo de uso

```python
from rlm import RLM
from rlm.logger import RLMLogger

logger = RLMLogger(log_dir="./logs", file_name="session")

rlm = RLM(
    backend="openai",
    backend_kwargs={"model_name": "gpt-4o-mini"},
    logger=logger,
    verbose=True,
)

result = rlm.completion("Analise este arquivo")
```

### Formato de saída

Cada linha é um JSON independente.

Exemplo simplificado:

```json
{"type": "metadata", "root_model": "gpt-4o-mini", "max_iterations": 30}
{"type": "iteration", "iteration": 1, "response": "...", "code_blocks": []}
{"type": "iteration", "iteration": 2, "response": "...", "final_answer": "..."}
```

### Garantias e limites

- UTF-8 explícito.
- Lock intra-processo para threads.
- Serialização defensiva com fallback para repr quando necessário.
- Não resolve concorrência entre múltiplos processos escrevendo no mesmo arquivo.

Se você precisar logging cross-process consistente, a solução correta é um agregador por fila/socket, não abrir o mesmo arquivo de vários processos.

## 2. VerbosePrinter

Arquivo: rlm/logger/verbose.py

O VerbosePrinter é a camada de observação humana no terminal. Ele não substitui o logger de trajetória; ele complementa.

### Quando rich está disponível

Ele usa Rich para mostrar:

- cabeçalho da execução
- início de iteração
- resposta do LLM
- execução de código
- subchamadas
- resposta final
- resumo com tempo e tokens

### Quando rich não está disponível

Ele faz fallback para texto puro em stderr.

Esse comportamento é intencional. O sistema não deve falhar no import apenas porque uma dependência opcional de visualização não está instalada.

## 3. Logger Estruturado de Runtime

Arquivo: rlm/core/structured_log.py

Esse módulo serve para diagnóstico operacional do runtime.

### O que ele oferece

- níveis: debug, info, warn, error
- namespace por subsistema
- saída em texto humano ou JSON compacto
- redação automática de segredos
- child logger hierárquico
- log opcional em arquivo

### Exemplo de uso

```python
from rlm.core.structured_log import get_runtime_logger

log = get_runtime_logger("gateway", json_format=False)
log.info("Webhook recebido", client_id="abc", channel="telegram")

child = log.child("telegram")
child.warn("Mensagem recusada", chat_id="123")
```

### Nome recomendado para código novo

Para reduzir a ambiguidade com `rlm.logger.RLMLogger`, código novo deve preferir:

- `RuntimeLogger`
- `get_runtime_logger(...)`
- idealmente via `rlm.logging`

Compatibilidade preservada:

- `rlm.core.structured_log.RLMLogger` continua existindo
- `get_logger(...)` continua existindo

Ou seja: a mudança é semântica e de clareza de API, não de comportamento.

### Recomendações objetivas por responsabilidade

- `TrajectoryLogger`: ciclo completo do agente, iterações, replay conceitual, visualização posterior.
- `RuntimeLogger`: eventos operacionais curtos de servidor, gateway, scheduler, plugins, supervisor e infraestrutura.
- `VerbosePrinter`: UX local de desenvolvimento, leitura humana imediata, sem persistência.

### Loggers pré-configurados

- session_log
- supervisor_log
- repl_log
- plugin_log
- scheduler_log
- gateway_log

Esses objetos reduzem boilerplate nos pontos mais frequentes do runtime.

## 4. Redação de Segredos

O módulo structured_log tenta impedir vazamento acidental de credenciais em logs.

### Fontes de redação

- valores exatos lidos de variáveis de ambiente conhecidas
- padrões como:
  - sk-...
  - key-...
  - Bearer ...
  - strings alfanuméricas longas típicas de token

### Importante

Isso reduz risco, mas não é prova formal de ausência de vazamento. Se você concatenar payloads arbitrários em mensagens, ainda pode gerar logs ruins. O uso correto continua sendo registrar contexto pequeno e específico.

## 5. Variáveis de Ambiente

### RLM_LOG_LEVEL

Controla o corte do logger estruturado.

Valores aceitos na prática:

- debug
- info
- warn
- error

Exemplo:

```bash
RLM_LOG_LEVEL=debug rlm start --foreground
```

### Observação

O logger de trajetória não depende de RLM_LOG_LEVEL. Ele grava iterações quando está conectado ao objeto RLM via parâmetro logger.

## 6. Quando usar cada coisa

### Quero investigar por que o gateway rejeitou requisições

Use o logger estruturado de runtime.

### Quero ver o que o agente executou em cada iteração

Use o logger de trajetória.

### Quero ver a execução acontecendo no terminal

Use verbose=True no RLM, que ativa o VerbosePrinter.

### Quero integrar com coletor externo ou grep automatizado

Use structured_log com json_format=True.

## 7. Armadilhas conhecidas

### Duas classes com o mesmo nome lógico

Existe um RLMLogger em rlm.logger e outro em rlm.core.structured_log.

Isso é confuso, mas hoje eles representam problemas diferentes:

- rlm.logger.RLMLogger: trajetória
- rlm.core.structured_log.RLMLogger: eventos operacionais
- rlm.core.structured_log.RuntimeLogger: alias explícito recomendado para eventos operacionais em código novo

Ao manter ou refatorar essa área, preserve essa distinção ou unifique explicitamente os papéis.

### Abrir o mesmo arquivo em múltiplos processos

Não faça isso. Em Windows isso costuma falhar cedo; em POSIX pode falhar de forma silenciosa e intermitente. Se precisar de multi-processo, use fila, listener ou agregador central.

### Tratar verbose como persistência

VerbosePrinter é observação em terminal. Ele não substitui logging persistente.

## 8. Estratégia recomendada de operação

Para desenvolvimento local:

- verbose=True para leitura humana imediata
- RLMLogger de trajetória para auditoria pós-execução

Para gateway e serviços:

- structured_log com nível controlado por ambiente
- formato JSON quando houver coleta automatizada
- logger de trajetória apenas quando a carga justificar o custo de persistência detalhada

O scheduler em `rlm/server/scheduler.py` agora segue essa regra e usa o logger operacional estruturado.
Os módulos centrais e gateways remanescentes também foram alinhados para `RuntimeLogger`:

- `rlm/core/lm_handler.py`
- `rlm/core/shutdown.py`
- `rlm/core/disposable.py`
- `rlm/server/event_router.py`
- `rlm/server/telegram_gateway.py`
- `rlm/server/slack_gateway.py`
- `rlm/server/discord_gateway.py`
- `rlm/server/whatsapp_gateway.py`
- `rlm/server/webchat.py`

Com isso, a regra de responsabilidade fica assim:

- trajetória do agente: `TrajectoryLogger`
- operação de runtime, servidores e infraestrutura: `RuntimeLogger`
- observação humana local: `VerbosePrinter`

## 9. Estado atual do projeto

Após a última atualização:

- o logger de trajetória está com escrita JSONL mais robusta
- o VerbosePrinter não quebra mais quando rich não está instalado
- a documentação agora explicita a fronteira entre logging operacional e logging de trajetória

O próximo refactor natural, se for desejado, é reduzir a ambiguidade entre os dois sistemas e expor uma fachada unificada sem perder a separação de responsabilidades.

Parte dessa ambiguidade já foi reduzida com a introdução de aliases explícitos para o logger operacional, sem quebrar compatibilidade do código existente.