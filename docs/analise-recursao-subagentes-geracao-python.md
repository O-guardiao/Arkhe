# Análise da Recursão, Subagentes e Geração de Python no RLM

## Escopo

Este documento descreve como o RLM funciona hoje, com foco em quatro pontos:

1. o pipeline real da recursão;
2. o que o projeto chama de subagente e onde isso de fato acontece;
3. como o LLM gera Python atualmente;
4. por que o sistema entra em tentativa e erro e quais melhorias reduzem custo sem quebrar o comportamento atual.

O objetivo aqui não é redesenhar o sistema inteiro. O objetivo é separar mecanismo real de narrativa, identificar gargalos de custo e propor mudanças de baixo risco.

## Resumo Executivo

O RLM atual não é um planejador simbólico com executor estruturado. Ele é um loop recursivo orientado por texto.

O fluxo real é este:

1. o modelo recebe um system prompt que o empurra para trabalhar dentro de um REPL;
2. ele responde em linguagem natural com ou sem blocos ```repl```;
3. o runtime extrai esses blocos por regex;
4. cada bloco é executado no LocalREPL;
5. stdout, stderr e nomes de variáveis voltam para o histórico como novas mensagens;
6. o modelo lê esse feedback e tenta de novo até emitir `FINAL(...)` ou `FINAL_VAR(...)`.

Isso significa que a geração de Python hoje é um loop de síntese textual seguido de validação empírica. Não existe planner intermediário, não existe AST executor guiado por schema, não existe compilação prévia de plano, e não existe classificador forte que decida se a tarefa precisa mesmo de código, de `llm_query` ou de `sub_rlm`.

O sistema funciona porque o REPL fecha o loop. O custo explode porque quase toda correção é pós-falha.

## Arquitetura Real da Completion

### 1. Ponto de entrada

Em [rlm/core/rlm.py](../rlm/core/rlm.py), `RLM.completion()` é o loop principal.

A ordem real é:

1. se `depth >= max_depth`, cai para `_fallback_answer()` e vira uma chamada simples de LM;
2. caso contrário, cria um `LMHandler` e um environment via `_spawn_completion_context()`;
3. monta `message_history` com `_setup_prompt()`;
4. injeta no REPL as ferramentas recursivas: `sub_rlm`, `sub_rlm_parallel`, `sub_rlm_async`, aliases e browser tools;
5. executa até `max_iterations` iterações;
6. em cada iteração, chama `_completion_turn()`;
7. procura `FINAL(...)` ou `FINAL_VAR(...)` na resposta;
8. se não houver final, serializa a iteração e empilha tudo de volta no `message_history`.

Esse desenho é importante porque mostra a fronteira de decisão: o runtime não decide o próximo passo, só reexecuta o que o LLM escreveu.

### 2. O que acontece em uma iteração

Em `_completion_turn()`:

1. `lm_handler.completion(prompt)` pede a próxima resposta textual do modelo;
2. `find_code_blocks(response)` extrai blocos ```repl```;
3. cada bloco é executado com `environment.execute_code(code_block_str)`;
4. o loop detector registra código, output e erro;
5. a resposta textual original do LLM e os resultados do REPL formam um `RLMIteration`.

O ponto central é brutal: o runtime não entende a intenção do código. Ele apenas extrai texto, executa e devolve o feedback textual para a próxima rodada.

### 3. Como a iteração vira contexto da próxima rodada

Em [rlm/utils/parsing.py](../rlm/utils/parsing.py), `format_iteration()` transforma a última resposta em mensagens novas:

1. a resposta do assistente é preservada;
2. cada execução gera uma nova mensagem de usuário contendo o código executado e o output do REPL.

Na prática, erro de Python vira prompt de correção. Esse é o núcleo do comportamento de tentativa e erro.

## Como o Python é Gerado Hoje

### 1. O contrato é puramente textual

O prompt-base em [rlm/utils/prompts.py](../rlm/utils/prompts.py) instrui o modelo a escrever código em blocos ```repl``` e a encerrar com `FINAL(...)` ou `FINAL_VAR(...)`.

Portanto, o pipeline de geração de Python hoje é:

1. o modelo raciocina em texto;
2. o modelo decide se escreve código;
3. o modelo serializa esse código em markdown;
4. o runtime extrai por regex;
5. o runtime executa;
6. a correção vem apenas depois do erro ou do output observado.

Não há:

1. parser estruturado de ações;
2. plano tipado de execução;
3. checagem sintática antes de entrar no loop principal;
4. classificador de modo que barre uso desnecessário de REPL.

### 2. O REPL persiste estado e isso muda o tipo de código gerado

O `LocalREPL` em [rlm/environments/local_repl.py](../rlm/environments/local_repl.py) mantém `globals` e `locals` persistentes ao longo das iterações. Isso empurra o modelo a programar em estilo incremental:

1. cria variável numa rodada;
2. inspeciona ou reutiliza na próxima;
3. corrige trechos localmente em vez de reescrever o programa inteiro.

Isso é uma força real do sistema para análise interativa. Também é uma fonte real de confusão: o modelo pode contaminar o estado, depender de variáveis antigas ou corrigir um detalhe sem perceber que a hipótese anterior inteira estava errada.

### 3. O runtime tenta evitar corrupção de scaffold, mas não resolve erro de estratégia

O `LocalREPL._restore_scaffold()` restaura nomes reservados como `llm_query`, `context` e `FINAL_VAR`. Isso evita que o modelo destrua o ambiente permanentemente.

Mas isso não resolve o problema principal: o modelo ainda escolhe livremente entre caminhos caros e baratos, quase sempre sem um gate determinístico no runtime.

## O que é “Subagente” no RLM Hoje

Hoje existem dois mecanismos diferentes que o usuário tende a chamar de subagente. Eles não são a mesma coisa.

### 1. Subagente recursivo de REPL

Esse é o mecanismo em [rlm/core/sub_rlm.py](../rlm/core/sub_rlm.py).

`sub_rlm()`:

1. sobe `depth + 1`;
2. instancia um novo `RLM` filho;
3. cria novo `LMHandler` e novo environment do filho;
4. roda `child.completion(...)` em thread separada;
5. bloqueia até o resultado ou timeout.

`sub_rlm_parallel()` faz o mesmo para várias tarefas em `ThreadPoolExecutor`.

`sub_rlm_async()` cria filhos fire-and-forget e devolve um `AsyncHandle`.

Na prática, isso é subagente operacional real: um agente completo, com seu próprio loop REPL.

### 2. Handoff de papel no servidor

Esse é o mecanismo em [rlm/core/handoff.py](../rlm/core/role_orchestrator.py) e [rlm/server/api.py](../rlm/server/api.py).

Aqui o fluxo é diferente:

1. o REPL pode registrar `request_handoff(...)`;
2. depois da completion principal, a API chama `orchestrate_roles(...)`;
3. o orchestrator decide executar worker, evaluator ou escalonamento humano;
4. internamente, ele usa `make_sub_rlm_fn(rlm)` para materializar esses papéis.

Ou seja: o sistema de papéis não é um segundo runtime independente. Ele é uma camada de orquestração acima do mesmo mecanismo de `sub_rlm()`.

### 3. Consequência prática

Quando se fala “subagent” no projeto, hoje isso pode significar:

1. filho recursivo explícito chamado do REPL;
2. worker/evaluator disparado por handoff no servidor.

Ambos acabam consumindo o mesmo núcleo: novo `RLM` filho, novo loop, novo custo.

## Onde Nasce o Tentativa e Erro

### 1. O sistema só aprende depois de executar

O fluxo principal não valida o código antes da execução. Ele só descobre erro depois de `exec(...)` dentro do `LocalREPL`.

Isso torna o custo de exploração inevitavelmente empírico:

1. o modelo imagina a solução;
2. escreve Python;
3. executa;
4. lê erro ou output;
5. replaneja.

Esse é literalmente um laço de tentativa e erro.

### 2. O prompt ainda empurra demais para recursão e REPL

O prompt principal diz que o modelo está em um REPL que “can recursively query sub-LLMs” e que isso é “strongly encouraged to use as much as possible”. Mais abaixo, o mesmo prompt diz que `sub_rlm` é um FULL agent e é caro, e que tarefas simples devem preferir `llm_query`.

Isso cria uma instrução internamente conflitante:

1. no topo, empurra exploração e recursão;
2. depois, tenta frear custo.

Em modelo real, instrução inicial e framing importam demais. O resultado previsível é overuse de REPL e de subagente em parte dos casos.

### 3. Erro vira contexto, não política

Quando o REPL falha, o sistema injeta stderr no histórico. Mas isso não significa que exista uma política explícita de reparo.

Hoje o reparo é implícito:

1. o modelo relê o erro;
2. tenta inferir a correção;
3. escreve novo código.

Não existe uma máquina de estados do tipo:

1. erro sintático;
2. erro de import;
3. erro de variável ausente;
4. erro de API;
5. branch de reparo específica para cada categoria.

Sem isso, o modelo paga em iterações o que poderia pagar em roteamento determinístico.

### 4. `FINAL_VAR` falho também custa rodada

Em [rlm/utils/parsing.py](../rlm/utils/parsing.py), se `FINAL_VAR(...)` apontar para variável inexistente, o runtime retorna `None` e deixa o loop continuar.

Isso é correto para robustez, mas aumenta custo porque muitos finais falham por detalhe de namespace e exigem mais uma rodada inteira de LLM.

### 5. O foraging mode é útil, mas formaliza o custo da ignorância

Depois de falhas consecutivas no REPL, o `LocalREPL` entra em foraging mode. Isso é intelectualmente correto: parar de fingir e começar a testar hipóteses.

Mas operacionalmente isso mostra um fato duro: o sistema não evita entrar em terreno desconhecido; ele só reage depois de já ter pago várias falhas.

## Custo Real de `sub_rlm`

`sub_rlm` não é uma chamada barata. Cada uso cria uma pilha nova:

1. nova instância de `RLM`;
2. novo `LMHandler`;
3. novo environment;
4. novo loop iterativo até `max_iterations`;
5. possivelmente novos filhos recursivos.

Então o custo não é só “mais uma chamada de modelo”. O custo é “mais um agente completo”.

Mesmo quando o prompt avisa isso, o runtime não aplica um gate forte. A decisão continua entregue ao LLM.

## Papel do LMHandler no Custo

O [rlm/core/lm_handler.py](../rlm/core/lm_handler.py) é bem melhor do que um stub ingênuo:

1. tem servidor TCP com pool e loop async persistente;
2. aceita batched requests;
3. roteia por `model` e por `depth`.

Mas o benefício desse handler é parcialmente neutralizado por um detalhe estrutural: ele nasce dentro de `_spawn_completion_context()` por completion. Em outras palavras, a infraestrutura de transporte é razoável, mas a unidade de vida ainda é curta demais.

## Diagnóstico Técnico Franco

O RLM funciona hoje como um interpretador de política escrita pelo próprio modelo.

Isso é poderoso porque:

1. dá flexibilidade máxima;
2. permite decomposição emergente;
3. transforma o LLM em programador do próprio processo de busca.

Isso é caro porque:

1. quase não há pruning determinístico antes da execução;
2. o planner e o executor estão colapsados no mesmo texto livre;
3. correção de rumo depende de mensagens de erro, não de tipos de ação;
4. subagente completo é fácil demais de acionar;
5. todo erro simples consome uma rodada completa de modelo.

Se a pergunta for “por que ele gera Python por tentativa e erro?”, a resposta é simples:

1. porque a arquitetura atual foi desenhada para aprender executando;
2. porque não existe camada estruturada entre intenção e execução;
3. porque o runtime só observa sucesso ou falha depois do código pronto.

## Melhorias que Ajudam Sem Quebrar o RLM

As mudanças abaixo preservam o modelo mental atual: loop iterativo, REPL persistente e recursão continuam existindo.

### 1. Criar um roteador determinístico de modo antes do loop

Antes de entrar na iteração principal, classificar a tarefa em um de quatro modos:

1. resposta direta;
2. `llm_query` simples;
3. REPL local;
4. `sub_rlm`.

Essa decisão não deve depender só do LLM principal. Deve existir uma política local baseada em heurística barata:

1. tamanho e tipo do contexto;
2. presença de diretório/codebase;
3. palavras de ação como “implemente”, “resuma”, “classifique”, “analise arquivo”, “execute”;
4. orçamento disponível de iterações.

Impacto esperado:

1. menos REPL para tarefas de resposta simples;
2. menos `sub_rlm` para subtarefas que cabem em uma inferência;
3. latência menor sem mexer no núcleo do loop.

### 2. Endurecer o prompt-base

O prompt principal hoje mistura incentivo à recursão com avisos de custo. Isso é fraco.

O framing deveria ser reescrito assim:

1. padrão é caminho mais barato possível;
2. REPL só quando houver transformação computacional real;
3. `sub_rlm` só quando a subtarefa exigir laço próprio ou ferramentas próprias;
4. responder sem imprimir `context` quando a tarefa já é explícita.

Isso reduz custo sem tocar em APIs.

### 3. Validar sintaxe antes do `exec`

Antes de executar bloco `repl`, compilar com `compile(code, ..., "exec")` e, em caso de `SyntaxError`, devolver erro normalizado curto.

Isso não elimina tentativa e erro, mas corta parte do ruído de falhas triviais.

Melhor ainda: classificar erro em categorias e devolver prefixos padronizados:

1. `SYNTAX_ERROR`;
2. `NAME_ERROR`;
3. `IMPORT_ERROR`;
4. `TYPE_ERROR`;
5. `TOOL_CONTRACT_ERROR`.

O modelo corrige melhor quando o feedback é estável.

### 4. Criar um repair loop especializado para erros banais

Em vez de devolver sempre o erro ao loop geral, usar uma política local:

1. até 2 reparos automáticos para erro sintático ou variável ausente;
2. sem reabrir planejamento completo;
3. com orçamento de tokens pequeno;
4. mantendo a mesma intenção original.

Isso reduz rodadas caras onde o modelo reexplica tudo para corrigir uma linha.

### 5. Colocar orçamento explícito por modo

Hoje `max_iterations` é global por completion, mas não há budget por tipo de ação.

Adicionar budgets locais:

1. no máximo N blocos REPL por iteração;
2. no máximo M chamadas `sub_rlm` por completion raiz;
3. no máximo K retries por categoria de erro.

Sem orçamento por ação, custo tende a vazar para onde o modelo se sente mais “ativo”.

### 6. Tornar `sub_rlm` um privilégio, não um default livre

Melhoria segura:

1. manter `sub_rlm` disponível;
2. adicionar um gate local que bloqueia ou avisa quando a subtarefa é curta demais;
3. registrar telemetry de uso desnecessário.

Exemplo de política:

1. se a subtarefa for uma instrução curta, sem arquivos, sem ferramentas e sem contexto grande, rebaixar para `llm_query`;
2. só permitir `sub_rlm` completo quando houver sinais concretos de multietapas.

### 7. Instrumentar métricas de tentativa e erro

Hoje faltam métricas operacionais específicas para este problema.

Medir por completion:

1. número de iterações;
2. número de blocos REPL executados;
3. taxa de erro por bloco;
4. quantas vezes `FINAL_VAR` falhou;
5. número de `sub_rlm` acionados;
6. quantos `sub_rlm` poderiam ter sido `llm_query`;
7. percentual de tempo em startup de handler, execução REPL e chamadas de modelo.

Sem essas métricas, toda otimização vira opinião.

### 8. Preservar compatibilidade por trás das mesmas interfaces

Se a meta é não quebrar o que já funciona, a regra é simples: melhorar por baixo das interfaces atuais.

Interfaces que não deveriam mudar na primeira fase:

1. `RLM.completion()`;
2. `sub_rlm()`;
3. `sub_rlm_parallel()`;
4. `sub_rlm_async()`;
5. `LocalREPL.execute_code()`.

As melhorias devem entrar como:

1. roteador anterior ao loop;
2. validação antes do exec;
3. repair loop especializado;
4. budgets e telemetry.

## Sequência Recomendada de Implementação

### Fase 1

Instrumentação e diagnóstico real.

1. medir iterações, erros e uso de subagentes;
2. registrar categorias de erro;
3. medir quantas tasks simples entram no REPL sem necessidade.

### Fase 2

Prompt surgery mínima.

1. remover o framing que incentiva recursão “as much as possible”;
2. reforçar caminho mais barato como default;
3. manter recursão como ferramenta, não como identidade principal da tarefa.

### Fase 3

Validador e repair loop baratos.

1. checagem sintática;
2. erros normalizados;
3. reparo local para falhas triviais.

### Fase 4

Roteador de modo.

1. resposta direta;
2. `llm_query`;
3. REPL;
4. `sub_rlm`.

### Fase 5

Governança de custo.

1. budgets por modo;
2. limites por completion;
3. alertas de uso indevido de subagente.

## Conclusão

O RLM já tem uma ideia forte e funcional: usar execução e recursão para expandir capacidade de contexto e decomposição.

O problema atual não é falta de poder. É falta de disciplina estrutural no caminho entre intenção, código e delegação.

Hoje o sistema:

1. decide demais em texto livre;
2. valida tarde demais;
3. delega caro demais;
4. repara erro de forma genérica demais.

Por isso ele produz Python por tentativa e erro.

Se a meta é melhorar sem quebrar, o caminho não é amputar recursão. O caminho é colocar seleção de modo, validação e budgets na frente dela.

Isso preserva o que o RLM tem de raro e reduz o que ele tem de caro.
