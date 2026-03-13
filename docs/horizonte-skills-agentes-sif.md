# Horizonte de Skills, Agentes e SIF

Documento de referência para orientar a evolução do RLM a partir do que de fato importa na literatura e na prática recente de agentes com LLM.

Última atualização: 2026-03-11

---

## Objetivo

Este documento detalha uma tese simples:

**o futuro de skills e agentes não está em prompts cada vez maiores, mas em capacidades operacionais curtas, tipadas, observáveis, recuperáveis e avaliáveis.**

Essa tese foi construída a partir de seis blocos de evidência:

- ReAct
- Toolformer
- Voyager
- AutoGen
- MCP
- Guias práticos recentes da Anthropic e da OpenAI

O documento não tenta resumir toda a literatura. O foco aqui é: **o que muda no desenho do RLM e do SIF**.

---

## Resumo Executivo

### Diagnóstico principal

O desenho ingênuo de agente baseado em um grande prompt com muitas instruções e muitas ferramentas misturadas não escala bem por cinco razões:

1. aumenta custo de tokens de forma quase linear com o número de capacidades;
2. piora roteamento, porque o modelo vê ferramentas demais e confunde escopo;
3. dificulta depuração, porque decisão, execução e verificação ficam misturadas;
4. envelhece rápido, porque a biblioteca de skills não aprende com uso;
5. cria um falso senso de “arquitetura”, quando na prática há apenas texto acumulado.

### Direção correta

O padrão que emerge tanto em pesquisa quanto em produção é este:

**intenção em linguagem natural + seleção de capacidades + execução estruturada + feedback do ambiente + avaliação explícita**

Em termos arquiteturais, isso significa:

- skills pequenas, com contrato operacional claro;
- contexto progressivo, não injeção total;
- roteador leve para triagem;
- executor especializado para agir;
- verificador para corrigir ou escalar;
- memória procedural para recuperar o que já funcionou;
- telemetria suficiente para aprender e aposentar decisões ruins.

### Tese aplicada ao RLM

No RLM, isso implica que o SIF deve deixar de ser apenas um formato compacto de descrição de skill e se tornar uma camada de:

- interface de runtime;
- roteamento;
- composição;
- observabilidade;
- memória procedural;
- aprendizado offline.

---

## 1. O que cada referência realmente ensina

## 1.1 ReAct

### Ideia central

ReAct mostrou que LLMs funcionam melhor em tarefas abertas quando **raciocínio e ação são intercalados**.

O ganho não está só em “pensar melhor”. O ganho aparece porque o modelo:

- formula uma hipótese;
- executa uma ação no ambiente;
- recebe observação externa;
- revisa a hipótese com base nessa observação.

### O que isso desmonta

ReAct desmonta a ideia de que reasoning puro resolve tudo. Sem observação externa, o modelo tende a:

- alucinar fatos;
- insistir em plano errado;
- propagar erro por várias etapas.

### Implicação para skills

Uma skill útil não é texto bonito. Ela é uma **ação observável**, com:

- entradas claras;
- efeito definido;
- retorno que pode virar nova evidência.

Isso favorece:

- assinatura curta;
- parâmetros óbvios;
- saída fácil de reaproveitar;
- loops de tentativa e correção.

### Implicação para o RLM

Se uma skill não produz algo que o agente consiga usar no próximo passo, ela é documentação, não capacidade.

Logo, o RLM deve privilegiar skills cujos retornos sejam:

- estruturados quando possível;
- curtos quando suficiente;
- compatíveis com composição;
- avaliáveis por outra etapa.

---

## 1.2 Toolformer

### Ideia central

Toolformer desloca a discussão de “a ferramenta existe?” para “o modelo sabe **quando** chamar a ferramenta, **qual** ferramenta chamar e **com quais argumentos**?”.

Essa é a parte difícil.

### O problema real exposto por Toolformer

As falhas mais relevantes não são só falhas de API. São falhas cognitivas de uso:

- não chamar a ferramenta quando deveria;
- chamar a ferramenta errada;
- chamar a ferramenta certa com argumento ruim;
- ignorar resultado útil;
- superusar ferramenta quando o modelo já tinha resposta suficiente.

### O que isso significa para bibliotecas de skills

Skill selection não pode depender só de inventário textual. É preciso tratar o uso de tools como um problema de decisão.

Isso leva a três exigências arquiteturais:

1. o inventário precisa ser curto o suficiente para ser discriminável;
2. a interface precisa ser clara o suficiente para reduzir erro de chamada;
3. o sistema precisa registrar histórico para aprender padrões de sucesso e falha.

### Implicação para o RLM

Hoje tags ajudam. Amanhã elas viram apenas uma feature entre várias.

O roteamento maduro no RLM deveria combinar:

- match lexical;
- embedding da intenção;
- prioridade da skill;
- histórico de acerto por tipo de tarefa;
- custo esperado;
- risco da operação.

---

## 1.3 Voyager

### Ideia central

Voyager é a evidência mais forte para a noção de **skill library como memória procedural**.

O sistema funcionou porque não tratava skill como simples descrição estática. Ele combinava:

- currículo automático;
- biblioteca crescente de habilidades;
- recuperação por similaridade;
- composição de habilidades;
- refinamento iterativo usando feedback do ambiente.

### O insight decisivo

Uma skill valiosa não é apenas “como chamar uma API”.

Uma skill valiosa é uma **competência reutilizável** que:

- funcionou antes;
- foi armazenada com contexto;
- pode ser recuperada em situação parecida;
- pode ser combinada com outras.

### O que isso muda

Em vez de biblioteca de ferramentas, você passa a ter biblioteca de comportamento.

Isso muda o desenho de memória:

- não basta guardar manifesto;
- é preciso guardar também traços de execução, sucesso, falhas e contexto.

### Implicação para o RLM

O SIF precisa evoluir para permitir pelo menos:

- descrição curta da skill;
- assinatura;
- condições de uso;
- score histórico;
- vizinhos de composição;
- exemplos mínimos de sucesso;
- rastros relevantes de uso passado.

Sem isso, o RLM terá uma coleção de wrappers. Não uma biblioteca de habilidades.

---

## 1.4 AutoGen

### Ideia central

AutoGen consolidou a noção de que aplicações agentic úteis frequentemente emergem de **conversação entre papéis especializados**, não de um único agente gigante.

### O ganho real

O ganho não é “ter vários agentes” por si só. O ganho é separar responsabilidades:

- um papel interpreta;
- outro executa;
- outro verifica;
- um humano entra quando necessário.

### O que isso evita

Essa separação reduz:

- acoplamento entre instruções;
- conflito entre ferramentas semelhantes;
- sobrecarga cognitiva do contexto;
- dificuldade de avaliação.

### Implicação para o RLM

O RLM não deveria convergir para um agente único com todas as tools e todas as regras.

Ele deveria caminhar para três classes mínimas de agente:

- **Micro-agent**: triagem, small talk, captura de intenção, baixo custo;
- **Worker-agent**: execução de tarefa com tools adequadas;
- **Evaluator-agent**: crítica, validação, retry, escalonamento.

Isso está mais alinhado com a prática moderna do que empilhar regras em um único loop.

---

## 1.5 MCP

### Ideia central

MCP é um padrão de interoperabilidade. Ele resolve **transporte**, **descoberta**, **schema** e **integração** entre clientes de IA e sistemas externos.

### O que MCP faz bem

MCP padroniza:

- acesso a tools;
- acesso a dados;
- encapsulamento de workflows;
- integração entre ecossistemas diferentes.

### O que MCP não faz

MCP **não resolve**:

- planejamento de alto nível;
- escolha ótima de skill;
- memória procedural;
- aprendizado com telemetria;
- política de retry;
- avaliação de qualidade.

### Implicação para o RLM

No RLM, MCP deve ser tratado como barramento, não como arquitetura cognitiva.

Erro comum:

“se conectei tudo por MCP, tenho um sistema agentic maduro”.

Isso é falso. Você tem plumbing. Cognição ainda precisa ser construída acima disso.

---

## 1.6 Guias práticos da Anthropic e da OpenAI

### Convergência principal

Os guias recentes convergem em alguns pontos que importam muito:

1. comece com a solução mais simples possível;
2. use workflow antes de usar autonomia plena;
3. ferramentas precisam de interface clara e boa documentação operacional;
4. multiagente só vale quando há separação real de domínio ou complexidade;
5. tracing, eval e guardrails são parte do produto, não extras opcionais.

### Conclusão prática

Não existe suporte sério à ideia de “um superprompt resolve”.

O consenso industrial real é:

- decompor;
- medir;
- restringir;
- avaliar;
- só então ampliar autonomia.

---

## 2. Convergência: o que o campo está dizendo em uma frase

O padrão dominante que emerge é este:

**menos texto bruto, mais interface operacional; menos agente monolítico, mais papéis e verificações; menos manifesto estático, mais memória recuperável.**

---

## 3. O que isso implica para skills no RLM

## 3.1 Skill não pode ser só markdown

Markdown continua útil para tutorial e exemplos, mas não pode ser o núcleo da skill.

No runtime, a skill precisa ser tratada como objeto com propriedades operacionais.

### Campos mínimos recomendados

Uma skill madura no RLM deve ter pelo menos estes campos:

- `name`
- `signature`
- `prompt_hint`
- `tags`
- `preconditions`
- `postconditions`
- `side_effects`
- `estimated_cost`
- `historical_reliability`
- `compose`
- `fallback_policy`
- `examples_min`

### Por que cada campo existe

`name`
: identifica a capacidade sem ambiguidade.

`signature`
: define contrato de chamada.

`prompt_hint`
: orienta o roteamento com baixíssimo custo de tokens.

`tags`
: ajudam no roteamento lexical e na busca híbrida.

`preconditions`
: evitam chamadas inviáveis ou perigosas.

`postconditions`
: ajudam o evaluator a saber o que deveria ter acontecido.

`side_effects`
: indicam risco operacional.

`estimated_cost`
: ajuda a selecionar caminhos baratos primeiro.

`historical_reliability`
: ajuda a priorizar o que já funcionou.

`compose`
: sugere vizinhos frequentes de pipeline.

`fallback_policy`
: evita colapso quando uma tool falha.

`examples_min`
: preserva um mínimo de exemplificação sem inflar prompt.

---

## 3.2 Roteamento não deve depender só de tags

Tags são úteis, mas são frágeis. Elas falham em pelo menos quatro casos:

- intenção expressa com vocabulário diferente;
- tarefa que exige duas skills de domínios distintos;
- query curta demais para casar lexicalmente;
- query longa demais com termos que ativam skills erradas.

### Próximo estágio do roteamento

O roteamento maduro precisa ser híbrido:

- score lexical;
- score semântico por embedding;
- score histórico por tipo de tarefa;
- score de custo;
- score de risco.

Uma forma simples de pensar isso:

$$
score(skill, query) = aL + bE + cH - dC - eR
$$

Onde:

- $L$ = match lexical
- $E$ = similaridade semântica
- $H$ = histórico de sucesso
- $C$ = custo esperado
- $R$ = risco operacional

O objetivo não é encontrar “a skill correta” por magia. É **ordenar bem candidatas**.

---

## 3.3 Telemetria é parte da skill library

Sem telemetria, não existe aprendizado operacional.

`call_count` é insuficiente. O RLM precisa capturar pelo menos:

- query original;
- skill escolhida;
- ranking de candidatas;
- argumentos usados;
- latência;
- sucesso ou falha;
- necessidade de retry;
- skill seguinte na sequência;
- utilidade percebida do resultado;
- tipo de erro.

### Benefícios diretos

Com isso, você passa a poder:

- reordenar skills automaticamente;
- detectar skills pouco úteis;
- detectar compose que quase nunca fecha ciclo útil;
- identificar parâmetros que o modelo erra repetidamente;
- medir custo real por tarefa.

Sem telemetria, a biblioteca envelhece e você nem percebe.

---

## 3.4 Compose precisa virar política, não apenas dica

Hoje `compose = [...]` é um grafo estático e declarativo.

Isso é melhor do que nada, mas ainda é fraco. O próximo estágio útil é um grafo ponderado por evidência.

### Exemplo conceitual

Em vez de:

```toml
compose = ["github", "notion"]
```

Você quer algo conceitualmente próximo de:

```json
{
  "github": {"success_rate": 0.74, "avg_latency_ms": 820, "avg_utility": 0.81},
  "notion": {"success_rate": 0.42, "avg_latency_ms": 610, "avg_utility": 0.36}
}
```

### Resultado

A composição deixa de ser opinião manual. Passa a ser política informada por uso.

---

## 3.5 Skill boa do futuro é competência reutilizável

O sistema não deve guardar só “como chamar o serviço”.

Deve guardar também:

- em qual contexto a skill funcionou;
- que pré-condições estavam presentes;
- qual foi a sequência anterior;
- qual foi a próxima melhor ação;
- qual formato de retorno se mostrou mais útil.

Isso é memória procedural.

---

## 4. O que isso implica para agentes no RLM

## 4.1 Pare de pensar em um agente só

Um agente monolítico parece simples, mas é cognitivamente caro e operacionalmente opaco.

Quando o sistema cresce, um agente único tende a:

- carregar contexto demais;
- chamar tools demais;
- misturar papéis;
- ficar difícil de avaliar.

### Separação mínima recomendada

#### Micro-agent

Função:

- small talk;
- identificação de intenção;
- triagem;
- controle de custo;
- encaminhamento.

Características:

- modelo mais barato;
- contexto mínimo;
- poucas ou nenhuma tool pesada;
- alta frequência de uso.

#### Worker-agent

Função:

- executar tarefa delimitada;
- chamar tools;
- navegar em compose;
- produzir saída operacional.

Características:

- contexto focado;
- tools especializadas;
- custo maior, acionado sob demanda.

#### Evaluator-agent

Função:

- validar saída;
- detectar falha;
- solicitar retry;
- escalar para humano ou outro worker.

Características:

- critérios explícitos;
- preferência por saídas estruturadas;
- uso estratégico, não constante.

---

## 4.2 Workflow primeiro, autonomia depois

Antes de subir autonomia, o sistema deve demonstrar competência em workflows mais restritos.

Sinais de que ainda não vale subir para autonomia plena:

- rota de tool ainda erra com frequência;
- custo explode com poucas tarefas;
- resultados são difíceis de validar;
- não há tracing suficiente;
- não existe política clara de fallback.

Se esses sinais estão presentes, o problema não é “falta de agente”. O problema é falta de engenharia de workflow.

---

## 4.3 Handoff explícito é melhor que improviso implícito

Quando um agente precisa passar controle para outro, isso não deveria parecer improviso textual. Deveria existir evento explícito de handoff com dados mínimos:

- motivo do handoff;
- resumo do estado;
- ferramentas já tentadas;
- falhas observadas;
- objetivo restante.

Isso reduz perda de contexto e torna o tracing compreensível.

---

## 5. Progressive disclosure de contexto

Esta é uma das decisões mais importantes para o RLM porque impacta custo, latência e qualidade.

### Camadas recomendadas

#### Micro

Conteúdo:

- poucas capabilities;
- hints curtos;
- sem corpos extensos.

Uso:

- small talk;
- triagem;
- confirmação curta.

#### SIF

Conteúdo:

- índice operacional compacto;
- assinatura curta;
- hints;
- recipes limitadas.

Uso:

- maioria das tarefas práticas.

#### Focused

Conteúdo:

- poucas skills relevantes com mais contexto;
- alguns exemplos;
- compose expandida.

Uso:

- tarefas com duas ou três capacidades acopladas.

#### Full/Manual

Conteúdo:

- documentação completa;
- exemplos detalhados;
- detalhes operacionais completos.

Uso:

- depuração;
- análise humana;
- consulta sob demanda via `skill_doc()`.

### Por que isso é superior

Porque evita dois extremos ruins:

- cegueira do modelo por falta total de awareness;
- colapso de tokens por excesso de contexto.

---

## 6. O que o SIF precisa virar

## 6.1 O papel do SIF hoje

Na sua evolução recente, o SIF já deixou de ser “codex exposto no prompt” e começou a virar:

- contrato operacional;
- camada de hints;
- tabela compacta de skills;
- superfície de composição;
- fábrica de callables.

Isso já é muito mais correto do que o desenho anterior.

## 6.2 O limite do SIF atual

O SIF atual ainda está centrado demais em descrição declarativa. Falta incorporar, de forma nativa:

- confiabilidade histórica;
- score de custo e risco;
- retrieval semântico;
- traços de execução;
- política de fallback;
- ponderação de compose;
- estados de maturidade da skill.

---

## 6.3 Proposta conceitual de SIF v4

O próximo salto sério do SIF não deve ser mais sintaxe decorativa. Deve ser ampliação do contrato de runtime.

### Campos adicionais recomendados

```toml
[sif]
signature = "shell(cmd: str, timeout: int = 30) -> subprocess.CompletedProcess"
short_sig = "shell(cmd)→CP"
prompt_hint = "Executa comando local ou remoto para diagnóstico, deploy ou automação"
compose = ["github", "filesystem", "notion"]

[runtime]
estimated_cost = 0.35
risk_level = "medium"
side_effects = ["filesystem_write", "process_spawn"]
fallback_policy = "ask_user_or_use_github"

[quality]
historical_reliability = 0.82
success_count = 184
failure_count = 41
last_30d_utility = 0.77

[retrieval]
embedding_text = "terminal deploy logs ssh process server diagnostics"
example_queries = [
  "veja os logs do serviço",
  "faça deploy no servidor",
  "reinicie o processo"
]
```

### O que esses campos habilitam

- roteamento híbrido;
- seleção custo-consciente;
- escalonamento por risco;
- priorização por desempenho real;
- aprendizagem offline.

---

## 7. Memória procedural para skills

## 7.1 O que guardar

Cada execução relevante de skill deveria gerar um trace enxuto persistível:

- intenção;
- skill escolhida;
- argumentos;
- saída resumida;
- sucesso/falha;
- utilidade;
- próxima skill acionada;
- embeddings de recuperação.

## 7.2 Como recuperar

Na hora do uso, o sistema consulta:

- skills candidatas por catálogo;
- traces passados por similaridade;
- compose frequentes da skill escolhida.

O resultado é uma seleção melhor informada do próximo passo.

## 7.3 Por que isso importa

Sem memória procedural, cada tarefa parece quase nova. Com memória procedural, o sistema começa a reaproveitar comportamento útil.

---

## 8. Observabilidade e avaliação

## 8.1 O que medir

Uma biblioteca de skills madura precisa de métricas por camada.

### Por skill

- frequência de uso;
- taxa de sucesso;
- latência média;
- custo médio;
- utilidade percebida;
- taxa de retry.

### Por composição

- taxa de fechamento de tarefa;
- latência composta;
- número médio de transições;
- transições improdutivas.

### Por agente

- custo por tipo de tarefa;
- handoffs por execução;
- taxa de escalonamento;
- taxa de correção após evaluator.

## 8.2 Sem eval, o sistema degrada

Se você não mede:

- não sabe qual skill está envelhecendo;
- não sabe se nova skill melhorou ou piorou o roteamento;
- não sabe se compose está ajudando ou confundindo;
- não sabe se o micro-mode está segurando custo sem cegar o sistema.

---

## 9. Anti-padrões a evitar

## 9.1 Superprompt

Sintoma:

- uma instrução gigante tentando descrever tudo para todos os cenários.

Problema:

- caro;
- frágil;
- difícil de manter;
- ruim de avaliar.

## 9.2 Skill como tutorial longo

Sintoma:

- muito markdown explicativo e pouco contrato operacional.

Problema:

- aumenta token sem melhorar decisivamente a chamada.

## 9.3 Compose ornamental

Sintoma:

- listas de compose escritas uma vez e nunca revisitadas.

Problema:

- grafo fica bonito, mas não representa comportamento real.

## 9.4 MCP como pseudo-cognição

Sintoma:

- assumir que interoperabilidade resolve planejamento.

Problema:

- plumbing não substitui inteligência operacional.

## 9.5 Multiagente prematuro

Sintoma:

- vários agentes sem observabilidade, sem handoff claro e sem critérios de uso.

Problema:

- custo sobe antes de a arquitetura amadurecer.

---

## 10. Roadmap recomendado para o RLM

## Fase 1 — Curto prazo

Objetivo: consolidar base operacional.

- completar hints e contratos mínimos das skills;
- estabilizar micro, sif e focused;
- registrar telemetria por execução;
- medir custo e utilidade por skill.

## Fase 2 — Médio prazo

Objetivo: aprender a escolher melhor.

- adicionar ranking híbrido;
- armazenar score histórico;
- persistir traces úteis;
- transformar `compose` em grafo ponderado.

## Fase 3 — Médio prazo avançado

Objetivo: separar papéis do sistema.

- micro-agent para triagem;
- worker-agent para execução;
- evaluator-agent para crítica;
- handoffs e tracing explícitos.

## Fase 4 — Longo prazo

Objetivo: skill library como memória procedural.

- retrieval semântico de skills e traces;
- exemplos recuperados por contexto;
- aposentadoria automática de skills ruins;
- síntese de novas skills a partir de padrões frequentes.

---

## 11. Tradução direta para decisões de arquitetura

Se o objetivo é transformar o RLM em sistema robusto, as decisões corretas são:

1. tratar skill como objeto de runtime, não apenas markdown;
2. tratar contexto como recurso escasso e progressivo;
3. tratar roteamento como problema de ranking, não só keyword match;
4. tratar compose como política aprendida, não só lista estática;
5. tratar memória de skill como memória procedural, não só catálogo;
6. tratar telemetria e eval como parte do produto, não ferramenta auxiliar;
7. tratar MCP como barramento, não mente;
8. tratar multiagente como decomposição de papéis, não moda arquitetural.

---

## 12. Conclusão

O campo está convergindo para uma direção clara:

**o valor não virá de acumular skills, mas de saber selecionar, combinar, verificar, recuperar e aposentar capacidades com base em evidência.**

Para o RLM, isso significa que o próximo salto relevante não é criar mais sintaxe ou mais texto de prompt. É construir uma camada operacional acima do SIF com:

- ranking aprendido;
- memória procedural;
- traces persistidos;
- composição ponderada;
- arquitetura em papéis.

Se isso for bem executado, o SIF deixa de ser só um formato compacto. Ele vira a espinha dorsal do runtime cognitivo do sistema.

---

## Referências base desta leitura

- ReAct: interleaving de reasoning e acting com feedback do ambiente.
- Toolformer: decisão de quando e como usar tools.
- Voyager: skill library crescente com retrieval e composição.
- AutoGen: multiagente por separação de papéis.
- MCP: barramento padronizado para ferramentas e dados.
- Anthropic e OpenAI: workflows simples primeiro, agentes depois; tool interface e eval são centrais.