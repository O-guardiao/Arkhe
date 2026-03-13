# Análise de Velocidade, Concorrência e Multiprocess no rlm-main

## Objetivo

Este documento consolida a análise técnica do projeto `rlm-main` com foco em um problema específico:

- reduzir latência total;
- permitir múltiplas execuções em simultâneo;
- evitar que uma interação bloqueie a próxima;
- tornar o RLM viável para uso prático com carga concorrente.

O ponto central é simples: o projeto já possui algum paralelismo interno para subtarefas, mas a arquitetura raiz ainda é serial e acoplada ao ciclo de vida do processo Python atual.

## Resumo Executivo

O `rlm-main` não foi desenhado, no caminho principal, para rodar múltiplas interações raiz de forma realmente independente dentro do mesmo processo.

Hoje, o comportamento dominante é:

1. cada `completion()` sobe contexto de execução;
2. o loop principal do agente roda em série;
3. as iterações dependem causalmente umas das outras;
4. o REPL local executa código no mesmo processo Python;
5. paralelismo existe apenas em ferramentas auxiliares, não no agente raiz.

Consequência prática:

- usar mais threads não resolve o problema principal;
- o ganho real exige isolamento por processo;
- a unidade de paralelismo correta para tornar o RLM viável é a interação raiz, não a iteração interna.

## Arquitetura Atual

### 1. Entrada principal

O ponto de entrada relevante está em [rlm/core/rlm.py](../rlm/core/rlm.py).

Em [rlm/core/rlm.py](../rlm/core/rlm.py#L319), `RLM.completion()` controla o ciclo completo da interação.

Fluxo simplificado:

1. cria `LMHandler` e ambiente;
2. injeta ferramentas no REPL;
3. monta prompt inicial;
4. roda um loop até `max_iterations`;
5. em cada iteração, chama o modelo e executa código;
6. procura `FINAL_VAR`;
7. retorna a resposta final.

### 2. Contexto de execução por completion

Em [rlm/core/rlm.py](../rlm/core/rlm.py#L167), `_spawn_completion_context()` faz isto por chamada:

1. cria cliente do backend;
2. cria `LMHandler`;
3. registra clientes auxiliares;
4. sobe o handler;
5. cria ou reutiliza ambiente;
6. ao final, para o handler e limpa ambiente se necessário.

Isso significa que existe custo fixo por `completion()`, mesmo antes da parte inteligente começar.

### 3. Loop principal do agente

Em [rlm/core/rlm.py](../rlm/core/rlm.py#L430), o loop principal é serial.

Cada iteração depende da anterior porque o histórico é atualizado a partir do resultado da iteração anterior.

Essa dependência impede paralelização ingênua entre iterações de um mesmo agente.

### 4. Turno individual da iteração

Em [rlm/core/rlm.py](../rlm/core/rlm.py#L589), `_completion_turn()` faz:

1. `lm_handler.completion(prompt)`;
2. extrai blocos de código;
3. executa cada bloco com `environment.execute_code()`;
4. registra o resultado e volta para o loop.

Em outras palavras: LLM e REPL são executados em sequência, não sobrepostos.

### 5. REPL local

O ambiente local está em [rlm/environments/local_repl.py](../rlm/environments/local_repl.py).

Em [rlm/environments/local_repl.py](../rlm/environments/local_repl.py#L630), `execute_code()` executa código no namespace persistente do próprio processo Python.

Características relevantes:

- estado local persistente entre iterações;
- `stdout` e `stderr` capturados no mesmo processo;
- lock para captura de saída em [rlm/environments/local_repl.py](../rlm/environments/local_repl.py#L587);
- execução via `exec()` com sandbox parcial.

Isso é eficiente para um único fluxo, mas ruim para isolamento e encerramento limpo sob concorrência intensa.

## O que já é concorrente hoje

O projeto já possui concorrência parcial. O problema não é ausência total de paralelismo, e sim a camada em que ele foi colocado.

### 1. `llm_query_batched`

Em [rlm/environments/local_repl.py](../rlm/environments/local_repl.py#L418), `llm_query_batched()` envia múltiplos prompts concorrentes para o handler.

Esse é o melhor caminho atual para subtarefas simples e independentes.

### 2. `LMHandler` com batch assíncrono

Em [rlm/core/lm_handler.py](../rlm/core/lm_handler.py#L98), `_handle_batched()` usa `asyncio.gather()` sobre `client.acompletion(...)`.

Isso mostra que o backend já tem capacidade de fan-out assíncrono quando a operação é tratada como lote.

### 3. `sub_rlm_parallel`

Em [rlm/core/sub_rlm.py](../rlm/core/sub_rlm.py#L381), `sub_rlm_parallel()` roda filhos em paralelo com `ThreadPoolExecutor`.

Esse mecanismo é útil para subagentes independentes, mas ainda opera dentro do mesmo processo raiz.

### 4. Connection pooling

Em [rlm/core/fast.py](../rlm/core/fast.py#L180), existe pool de conexão TCP para o `LMHandler`.

Isso reduz overhead de handshake local, mas não resolve o custo estrutural do ciclo completo do agente.

## Gargalos Reais

### Gargalo 1: `completion()` raiz continua serial

Mesmo com `llm_query_batched` e `sub_rlm_parallel`, a chamada raiz em [rlm/core/rlm.py](../rlm/core/rlm.py#L430) é serial.

Implicação:

- uma interação raiz não avança enquanto a etapa atual não terminar;
- não existe pipeline entre iteração `n` e iteração `n + 1`;
- não existe sobreposição entre chamada LLM e execução REPL da mesma interação.

### Gargalo 2: custo fixo por `completion()`

Em [rlm/core/rlm.py](../rlm/core/rlm.py#L167), o projeto cria `LMHandler` e ambiente por chamada.

Implicação:

- cada execução carrega custo inicial e custo de teardown;
- multiprocess mal desenhado pode apenas multiplicar esse custo;
- sem worker persistente, a escalabilidade fica ruim.

### Gargalo 3: REPL local no mesmo processo

Em [rlm/environments/local_repl.py](../rlm/environments/local_repl.py#L630), o código roda no mesmo processo Python.

Implicação:

- threads e recursos pendentes afetam o processo inteiro;
- encerramento pode atrasar novas interações;
- um fluxo ruim impacta a vida do processo pai.

### Gargalo 4: concorrência por thread em subtarefas complexas

Em [rlm/core/sub_rlm.py](../rlm/core/sub_rlm.py#L461), o paralelismo atual de subagentes usa threads.

Implicação:

- bom para fan-out pequeno;
- ruim para isolamento duro;
- ruim para recuperação sob travamento;
- inadequado como estratégia principal de escalabilidade.

### Gargalo 5: nível errado de paralelismo

O projeto paraleliza subtarefas, mas o maior ganho operacional viria de paralelizar interações raiz independentes.

Em termos de arquitetura, o sistema investe no lugar menos valioso primeiro.

## O que não é bug, é limite estrutural

É importante separar bug de desenho.

### 1. Iterações não podem ser paralelizadas livremente

Não faz sentido tentar paralelizar o loop interno padrão de [rlm/core/rlm.py](../rlm/core/rlm.py#L430), porque a próxima iteração depende do histórico gerado pela anterior.

### 2. Mais threads não equivalem a mais throughput real

Se o problema é isolamento, cleanup e contenção do REPL, aumentar `ThreadPoolExecutor` só expande a superfície do problema.

### 3. Async sozinho não resolve

Mesmo que tudo virasse `async`, o REPL local ainda seria estado mutável no mesmo processo. O problema principal continuaria: múltiplas interações competindo pelo mesmo processo e pelo mesmo ciclo de vida.

## O que o código já sugere implicitamente

O próprio projeto já deixa pistas de qual é o caminho correto.

### 1. O prompt já admite que `sub_rlm` é caro

Em [rlm/utils/prompts.py](../rlm/utils/prompts.py#L29), o sistema instrui o modelo a preferir `llm_query` e `llm_query_batched` para tarefas simples, porque `sub_rlm` é muito mais lento.

Isso confirma que o custo do agente recursivo completo é alto demais para uso indiscriminado.

### 2. O backend OpenAI já suporta async

Em [rlm/clients/openai.py](../rlm/clients/openai.py#L43), o cliente já cria `OpenAI` e `AsyncOpenAI`.

Portanto, a limitação não está no SDK do backend. Está no desenho do ciclo de execução acima dele.

### 3. O `LMHandler` já possui infraestrutura de servidor concorrente

Em [rlm/core/lm_handler.py](../rlm/core/lm_handler.py#L130), o servidor já usa `ThreadingTCPServer`, event loop persistente e pool de workers.

O componente de transporte está razoavelmente preparado. O gargalo maior é o acoplamento entre agente raiz, REPL local e ciclo de vida da completion.

## Diagnóstico Prático

Se a pergunta for: "por que uma interação nova não começa logo enquanto outra ainda está finalizando?", a resposta é esta:

1. o agente raiz não é uma unidade isolada por processo;
2. o REPL local vive no mesmo processo do fluxo principal;
3. subtarefas paralelas deixam custo residual no processo pai;
4. a arquitetura assume um fluxo dominante por `completion()`.

Se a pergunta for: "como tornar isso viável para múltiplos jobs ao mesmo tempo?", a resposta é outra:

- mover a unidade de isolamento para o nível de processo.

## Arquitetura Recomendada

### Meta

Transformar a interação raiz em uma unidade executável em um worker isolado.

### Desenho recomendado

#### Camada 1: supervisor

Um processo supervisor recebe jobs e distribui trabalho.

Responsabilidades:

- fila de entrada;
- roteamento para workers livres;
- timeout por job;
- retry controlado;
- kill de worker travado.

#### Camada 2: pool de workers persistentes

Cada worker é um processo Python independente.

Cada worker mantém vivos:

- cliente LLM;
- `LMHandler`;
- ambiente local persistente, se necessário;
- métricas e estado do job atual.

Benefícios:

- um travamento não contamina os demais;
- uma interação nova pode começar em outro worker imediatamente;
- o custo de startup é amortizado;
- throughput cresce com número de workers.

#### Camada 3: execução por mensagem

O supervisor envia para o worker:

- prompt;
- configuração;
- timeout;
- metadata opcional.

O worker responde com:

- `RLMChatCompletion`;
- logs;
- métricas;
- artefatos, se necessário.

### Unidade de paralelismo correta

A unidade correta para paralelismo de produção é:

- uma interação raiz por processo worker.

Não é:

- uma iteração do loop interno;
- um bloco REPL isolado;
- uma thread extra dentro do mesmo agente raiz.

## Estratégia Operacional Recomendada

### Para tarefas simples e independentes

Usar `llm_query_batched`.

Exemplos:

- classificação;
- extração;
- resumo independente por item;
- transformação de pequenas unidades de texto;
- geração simples de funções ou respostas.

### Para tarefas complexas que exigem raciocínio e REPL

Usar `sub_rlm_parallel` apenas quando cada subtarefa realmente precisa de um subagente com iterações próprias.

### Para múltiplas conversas ou múltiplos jobs de produção

Não usar threads como estratégia principal.

Usar pool de processos persistentes.

## Proposta de Evolução do Projeto

### Fase 1: worker process persistente

Criar uma abstração do tipo `RLMWorkerProcess`.

Capacidades mínimas:

- inicialização única do backend;
- inicialização única do `LMHandler`;
- ambiente persistente opcional;
- loop de recebimento de jobs;
- retorno estruturado;
- encerramento explícito.

### Fase 2: worker pool

Criar um `RLMWorkerPool` com:

- tamanho configurável;
- fila de jobs;
- timeout por worker;
- restart automático de worker defeituoso;
- coleta de métricas de throughput e latência.

### Fase 3: API de alto nível

Adicionar algo como:

```python
completion_many(prompts: list[str], max_workers: int = 4) -> list[RLMChatCompletion]
```

Esse método deve distribuir interações independentes entre workers persistentes.

### Fase 4: especialização de caminho rápido

Criar uma política explícita:

- simples e independente: `llm_query_batched`;
- complexa e recursiva: `sub_rlm_parallel`;
- múltiplas interações raiz: `worker pool`.

Hoje essas decisões ficam implícitas demais, e o sistema paga custo excessivo por isso.

## Mudanças de Maior Impacto

Em ordem de retorno esperado:

1. manter `LMHandler` vivo por worker, em vez de recriar por `completion()`;
2. mover a isolation boundary para processo, não thread;
3. criar pool persistente de workers;
4. usar `llm_query_batched` como caminho padrão para fan-out simples;
5. restringir `sub_rlm` para casos realmente recursivos;
6. medir latência separando startup, LLM, REPL e teardown.

## O que não vale o esforço principal

### 1. Paralelizar iterações do mesmo agente

Baixo valor e alto risco de quebrar a lógica.

### 2. Colocar mais `ThreadPoolExecutor` no caminho principal

Não corrige a fronteira de isolamento.

### 3. Tentar resolver tudo com mais async no agente raiz

Ajuda em I/O, mas não corrige o problema de processo e REPL compartilhado.

## Recomendação Final

Para o `rlm-main` ser viável sob múltiplos jobs simultâneos, a arquitetura precisa tratar cada interação raiz como unidade isolada de execução.

Em termos práticos:

- o RLM deve parar de ser pensado como um único loop Python central com subtarefas concorrentes;
- deve passar a ser pensado como um conjunto de workers persistentes, cada um capaz de executar um agente completo de forma isolada.

Resumo direto:

1. o paralelismo atual existe, mas está no nível errado;
2. o gargalo principal está no agente raiz serial e no REPL no mesmo processo;
3. a solução viável é multiprocess persistente por worker;
4. threads devem ser ferramenta auxiliar, não a base da escalabilidade.

## Padrão: Subagente Interativo como Camada de Desacoplamento

### O Problema Central da Interação

O modelo atual pressupõe uma única "conversa de cada vez":

1. usuário envia prompt;
2. Python executa o ciclo completo do agente;
3. usuário fica esperando;
4. resposta final chega.

Qualquer avanço do ponto de vista do usuário é invisível durante a execução. Enquanto o REPL roda código e o LLM processa iterações, não há canal de comunicação de volta para o usuário.

Esse é o problema de interatividade: a resposta é estruturalmente presa ao ciclo Python.

### A Inversão Proposta

A solução correta não é tentar tornar o ciclo Python mais rápido para o usuário não perceber a espera. A solução é **desacoplar a camada de interação da camada de computação**.

O subagente filho é a unidade viável para essa separação:

- o **filho** executa as tarefas pesadas (RLM com REPL, código, iterações);
- o **pai conversacional** mantém o canal aberto com o usuário enquanto o filho trabalha.

Isso inverte o relacionamento entre processo longo e usuário:

```
[Usuário] <----chat----> [Subagente Pai Conversacional]
                               |
                 .-------------+--------------.
                 |             |              |
           [Filho RLM 1] [Filho RLM 2] [Filho RLM N]
           Python + REPL  Python + REPL  Python + REPL
           rodando async   aguardando    concluido
```

O pai conversacional nunca bloqueia. Ele pode:

- entregar mensagens de status vindas dos filhos;
- responder perguntas independentes do usuário;
- lançar mais filhos se o usuário pedir algo novo;
- reportar quando um filho termina.

### Por que o Código Atual Já Suporta Isso Parcialmente

O projeto já contém as peças fundamentais:

**1. `sub_rlm` com thread daemon**

Em [rlm/core/sub_rlm.py](../rlm/core/sub_rlm.py#L280), cada filho roda em `threading.Thread(daemon=True)`. Isso significa que o processo pai não precisa esperar o filho terminar se decidir seguir adiante.

**2. `_sibling_bus`**

A assinatura de `sub_rlm()` em [rlm/core/sub_rlm.py](../rlm/core/sub_rlm.py#L175) já inclui o parâmetro `_sibling_bus`. Esse parâmetro existe exatamente para comunicação entre agentes irmãos durante execução paralela.

Esse canal já é a semente do que o padrão interativo precisa: um barramento onde filhos publicam progresso e o pai consome.

**3. `sub_rlm_parallel` com múltiplos filhos simultâneos**

Em [rlm/core/sub_rlm.py](../rlm/core/sub_rlm.py#L381), `sub_rlm_parallel` já lança N filhos em paralelo e coleta resultados quando todos terminam.

O passo seguinte é: em vez de bloquear esperando todos, o pai conversacional **polling** o estado dos filhos enquanto responde ao usuário.

**4. `SubRLMArtifactResult.as_custom_tools()`**

Em [rlm/core/sub_rlm.py](../rlm/core/sub_rlm.py#L113), um filho pode produzir funções e passá-las para o próximo filho. Isso habilita a recursividade: filho A cria uma primitiva, filho B usa essa primitiva sem recomputar.

### O que Falta Para o Padrão Ser Completo

Existem três lacunas que precisam ser fechadas:

**Lacuna 1: `sub_rlm` fire-and-forget**

Hoje `sub_rlm` bloqueia o chamador até o filho terminar (via `thread.join(timeout=...)`). O padrão interativo precisa de uma variante que retorne imediatamente um handle:

```python
# hoje: bloqueia
resultado = sub_rlm("tarefa pesada", timeout_s=300)

# precisa existir: retorna handle imediatamente
handle = sub_rlm_async("tarefa pesada")
# ... conversa com usuario ...
resultado = handle.result(timeout_s=300)  # bloqueia só aqui, quando necessário
```

A infraestrutura (daemon thread + future) já está presente no código. A mudança é só expor o handle antes de chamar `.join()`.

**Lacuna 2: Canal de progresso filho → pai**

O filho hoje não envia mensagens parciais. O pai recebe apenas o resultado final.

O `_sibling_bus` é o candidato direto para preencher essa lacuna. Precisaria ser expandido de "comunicação entre irmãos" para "comunicação filho → pai":

```python
# filho escreve enquanto roda
_sibling_bus.publish(f"branch {id}: processando item {n} de {total}")

# pai lê sem bloquear
mensagens = _sibling_bus.poll()
```

Qualquer `queue.Queue` ou lista com lock resolve isso. A arquitetura já prevê o ponto de injeção.

**Lacuna 3: Loop conversacional interleaved com polling**

O pai conversacional precisa de um loop que intercala:

1. verificar se há mensagens novas dos filhos;
2. verificar se há mensagem nova do usuário;
3. responder uma das duas.

```python
while filhos_ativos ou mensagem_pendente:
    novas = canal.poll()
    if novas:
        entregar_ao_usuario(novas)
    if usuario_falou:
        if pergunta_nova:
            filho = sub_rlm_async(pergunta_nova)
            filhos_ativos.append(filho)
        elif sobre_progresso:
            responder_com_status(filhos_ativos)
    time.sleep(0.1)  # poll interval leve
```

Esse loop é o componente que está ausente. Não é complexo, mas precisa ser construído explicitamente.

### Recursividade: Filho Abre Mais Filhos

O padrão suporta recursividade natural. Um filho pode, no seu REPL, chamar `sub_rlm()` para abrir sub-filhos:

```
[Usuário] <-> [Pai Conversacional]
                      |
              [Filho A: análise de dados]
                      |
          .--------+---------.
          |                  |
  [Sub-filho A1:       [Sub-filho A2:
   lê arquivo]         calcula KPIs]
```

Isso já funciona hoje via o depth guard em [rlm/core/sub_rlm.py](../rlm/core/sub_rlm.py#L220):

```python
child_depth = parent.depth + 1
if child_depth >= parent.max_depth:
    raise SubRLMDepthError(...)
```

O `max_depth` controla até onde a recursão pode ir. O pai conversacional fica no `depth=0` e permanece sempre disponível para o usuário porque nunca executa trabalho pesado diretamente — ele apenas delega.

### O Papel da Recursividade para Problemas Independentes

O usuário pode fazer uma segunda pergunta enquanto o filho A ainda está rodando. O pai conversacional pode:

1. detectar que é uma pergunta completamente nova;
2. lançar filho B para responder;
3. monitorar filho A e filho B simultaneamente;
4. entregar as respostas conforme cada filho termina.

Isso significa que o "travamento" percebido pelo usuário desaparece. Do ponto de vista do usuário, a conversa continua fluindo mesmo que o trabalho computacional leve 300 segundos.

### Comparação: Modelo Atual vs Modelo Interativo

| Aspecto | Atual | Interativo |
|---|---|---|
| Usuário espera resposta | Sim, bloqueado | Não, conversa continua |
| Progresso visível | Nenhum | Mensagens parciais via bus |
| Segunda pergunta durante execução | Impossível | Novo filho lançado imediatamente |
| Resultado de tarefa longa | Chega no final do `completion()` | Entregue quando filho termina |
| Recuperação de filho travado | Timeout na thread | Timeout + kill do worker |
| Múltiplas tarefas simultâneas | Serial | N filhos em paralelo |

### Implicação Arquitetural Principal

O padrão interativo requer que o agente raiz mude de papel:

- Modelo atual: o agente raiz **é** o executor de trabalho.
- Modelo interativo: o agente raiz **é** o supervisor conversacional e os filhos são os executores.

Essa separação de papéis é a mudança central. Ela não exige refatoração massiva do código existente — exige adicionar três componentes:

1. `sub_rlm_async` (handle não-bloqueante);
2. canal de progresso bidirecional (expansão do `_sibling_bus`);
3. loop de polling do pai conversacional.

O restante da infraestrutura (`sub_rlm`, `sub_rlm_parallel`, `daemon threads`, `depth guard`, `SubRLMArtifactResult`) já está pronto.

## Referências de Código

- [rlm/core/rlm.py](../rlm/core/rlm.py)
- [rlm/core/lm_handler.py](../rlm/core/lm_handler.py)
- [rlm/core/sub_rlm.py](../rlm/core/sub_rlm.py)
- [rlm/core/fast.py](../rlm/core/fast.py)
- [rlm/environments/local_repl.py](../rlm/environments/local_repl.py)
- [rlm/clients/openai.py](../rlm/clients/openai.py)
- [rlm/utils/prompts.py](../rlm/utils/prompts.py)