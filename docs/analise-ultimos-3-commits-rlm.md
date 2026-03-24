# Análise dos 3 Últimos Commits do RLM

## Objetivo

Este documento consolida o que foi alterado nos 3 commits mais recentes relevantes para o incidente atual do RLM, com foco em:

1. registrar exatamente o que mudou;
2. separar mudanças estruturais de mudanças comportamentais;
3. identificar onde o runtime recursivo provavelmente degradou;
4. deixar uma base objetiva para uma correção limpa posterior.

## Janela analisada

### Commit 1

- Hash curta: 01c2cf7
- Mensagem: feat: skill discoverability + entry-point validation tests
- Data: 2026-03-24 10:02:17 -0300

### Commit 2

- Hash curta: cd683c3
- Mensagem: fix: 8 bugs — rlm.py audit (5) + MCTS early termination + SIF partial compose + test api→runtime_pipeline
- Data: 2026-03-24 11:06:37 -0300

### Commit 3

- Hash curta: 3b686b3
- Mensagem: Revert "fix: 8 bugs — rlm.py audit (5) + MCTS early termination + SIF partial compose + test api→runtime_pipeline"
- Data: 2026-03-24 11:32:36 -0300

## Estado de branch no momento desta documentação

- HEAD local: 3b686b3
- origin/main: cd683c3
- Consequência: o repositório local está revertido, mas o remoto ainda aponta para o commit que introduziu o pacote de mudanças depois revertido localmente.

## Leitura executiva

Dos 3 commits, apenas 1 é forte candidato a ter piorado o comportamento central do RLM: cd683c3.

O commit 01c2cf7 é periférico ao núcleo recursivo.

O commit 3b686b3 não introduz comportamento novo; ele reverte cd683c3 por inteiro. O problema dele não é degradar o runtime, mas remover junto correções legítimas que também estavam no pacote revertido.

Em termos de risco real para o produto, o ponto mais provável de quebra não está em testes, nem em skill_loader, nem em mcts. Está nas alterações comportamentais dentro de rlm/core/rlm.py feitas em cd683c3.

## Commit 01c2cf7

## Resumo funcional

Esse commit adiciona observabilidade leve sobre skills no TUI, melhora a inspeção de skill_doc e cria testes de entry point da CLI.

## Arquivos alterados

### rlm/cli/tui.py

- Adiciona no cabeçalho da interface uma linha mostrando quantidade de skills carregadas e o diretório de origem.
- Natureza: interface e observabilidade.
- Risco para o núcleo recursivo: baixo.

### rlm/core/skill_loader.py

- skill_doc passa a incluir source_path no texto retornado.
- Natureza: documentação e descobribilidade.
- Risco para o núcleo recursivo: baixo.

### tests/conftest.py

- Cria resolve_arkhe_cli para encontrar o executável instalado ou cair no modo python -m rlm.cli.main.
- Natureza: infraestrutura de teste.
- Risco para o núcleo recursivo: nulo.

### tests/test_cli.py

- Adiciona 2 testes subprocess para validar help e version do entry point arkhe.
- Natureza: teste de CLI.
- Risco para o núcleo recursivo: nulo.

## Diagnóstico do commit 01c2cf7

Não há indício técnico de que esse commit possa ter quebrado o comportamento recursivo do RLM. Ele mexe em TUI, metadados de skill e testes da CLI. Se houve regressão de runtime após esse ponto, a causa não está aqui.

## Commit cd683c3

## Resumo funcional

Esse commit mistura dois tipos de mudança dentro do mesmo pacote:

1. correções objetivas e de baixo risco;
2. mudanças comportamentais no loop central do RLM.

Essa mistura é o principal erro de engenharia do lote. Ela dificultou separar o que realmente corrigia falhas do que alterava a semântica do produto.

## Arquivos alterados

### rlm/core/mcts.py

- Mudança:
  - limiar de early termination: de total_score >= 1.0 para total_score >= self.max_depth * 4.0
- Justificativa declarada:
  - default_score_fn já gera algo próximo de 3.5 por passo, então 1.0 encerrava branches cedo demais.
- Natureza:
  - correção lógica objetiva.
- Risco para o núcleo recursivo:
  - baixo.
- Efeito esperado:
  - impedir cancelamento prematuro de branches no MCTS.

### rlm/core/skill_loader.py

- Mudança:
  - estimate_tokens passa a usar allow_partial_compose=True em SIFTableBuilder.build.
- Natureza:
  - correção objetiva de medição e composição parcial.
- Risco para o núcleo recursivo:
  - baixo.
- Efeito esperado:
  - evitar falhas ao estimar contexto em subconjuntos reais de skills.

### tests/test_critical_phase8.py

- Mudança:
  - o teste deixa de olhar apenas api.py e passa a ler api.py + runtime_pipeline.py.
- Natureza:
  - correção de teste alinhada a refatoração anterior do servidor.
- Risco para o núcleo recursivo:
  - nulo.

### tests/test_critical_gateway.py

- Mudança:
  - mesma correção estrutural: leitura combinada de api.py + runtime_pipeline.py.
- Natureza:
  - correção de teste.
- Risco para o núcleo recursivo:
  - nulo.

### tests/test_critical_skills.py

- Mudança:
  - mesma correção estrutural: leitura combinada de api.py + runtime_pipeline.py.
- Natureza:
  - correção de teste.
- Risco para o núcleo recursivo:
  - nulo.

### rlm/core/rlm.py

Aqui está a parte crítica. O commit não apenas conserta bugs; ele altera a política de decisão do runtime.

#### Mudanças de baixo risco dentro de rlm.py

1. if self.verbose / getattr(self, 'verbose', None) para self.verbose.enabled
   - Objetivo: padronizar checagem de verbose.
   - Risco: baixo, assumindo que self.verbose sempre existe e segue essa interface.

2. Correção do print do loop detector de \n literal para quebra de linha real
   - Objetivo: corrigir log.
   - Risco: nulo.

3. _last_message_history preenchido antes dos retornos de RLMChatCompletion
   - Objetivo: preservar histórico final.
   - Risco: baixo.

#### Mudanças de médio risco dentro de rlm.py

4. _loop_detector_critical
   - Introduz flag para abortar mais rigidamente quando o loop detector marca nível crítico.
   - Risco: médio.
   - Motivo do risco:
     - se houver falso positivo do detector, o runtime pode abortar cedo demais.
   - Ainda assim, essa mudança é coerente com segurança operacional do loop e não é a principal suspeita da degradação relatada.

5. _default_answer muda role de assistant para user
   - Mudança semanticamente relevante.
   - Risco: médio.
   - Motivo do risco:
     - altera o framing da última solicitação ao modelo.
     - pode mudar como o backend interpreta o estado conversacional.
   - Não é o principal suspeito, mas também não é uma mudança trivial.

#### Mudanças de alto risco dentro de rlm.py

6. Relaxamento da recovery nudge

Antes:

- a mensagem dizia que o modelo precisava escrever código executável em bloco repl para progredir.

Depois:

- a mensagem passou a dizer que, se a resposta já estiver clara, o modelo deve finalizar imediatamente com FINAL(your answer).

Impacto arquitetural:

- isso desloca a política do sistema de runtime recursivo para resolução textual imediata;
- reduz a pressão do loop para explorar contexto, executar código, inspecionar estado e chamar ferramentas;
- aproxima o produto de um chatbot clássico, o que colide diretamente com a premissa central declarada do Arkhe.

Risco:

- alto.

7. Auto-finalização por text-only stall na completion principal

- Foram adicionadas as variáveis _text_only_stall_count e _TEXT_ONLY_STALL_LIMIT = 2.
- Se o modelo produzir 2 iterações consecutivas sem código e sem FINAL, o sistema trata a última resposta textual como resposta final.

Impacto arquitetural:

- encerra o loop sem execução de código;
- reduz chance de subagentes, busca paralela e exploração iterativa;
- transforma ausência temporária de código em condição de término, em vez de tratá-la como parte do processo de raciocínio recursivo.

Risco:

- muito alto.

8. Auto-finalização por text-only stall também em _run_inner_loop

- A mesma lógica foi duplicada no loop interno compartilhado por completion_stream e sentinel_completion.

Impacto arquitetural:

- a degradação não fica restrita ao caminho principal;
- passa a contaminar também fluxos persistentes e modos alternativos de execução.

Risco:

- muito alto.

## Onde cd683c3 provavelmente quebrou o RLM

### Suspeito número 1

- Auto-finalização por text-only stall nas duas rotas de execução.

Por que é o principal suspeito:

- corta o runtime após apenas 2 respostas textuais sem código;
- essa condição é curta demais para um sistema recursivo com ferramentas, inspeção e possível coordenação entre agentes;
- explica diretamente sintomas como:
  - respostas genéricas;
  - pouca profundidade;
  - menos exploração;
  - desaparecimento de subagentes;
  - 0 parallel tasks em trace paralela.

### Suspeito número 2

- Recovery nudge relaxada para permitir FINAL imediato.

Por que é forte suspeito:

- muda o incentivo fundamental do sistema;
- em vez de insistir em execução e progresso operacional, autoriza a saída precoce por texto.

### Suspeito número 3

- _default_answer com role user.

Por que entra como suspeito secundário:

- altera framing de finalização;
- pode reforçar a tendência de responder como conversa em vez de consolidar estado processado.

### Suspeito número 4

- _loop_detector_critical.

Por que é suspeito secundário:

- se o detector estiver sensível demais, o abort rígido pode encurtar loops legítimos.
- porém, isoladamente, ele não explica tão bem o padrão observado de respostas genéricas e ausência de paralelismo quanto a auto-finalização textual.

## O que em cd683c3 provavelmente não quebrou o RLM

As seguintes mudanças são praticamente inocentes em relação ao problema central relatado:

- correção do limiar de MCTS em rlm/core/mcts.py;
- allow_partial_compose em rlm/core/skill_loader.py;
- ajustes dos testes para api.py + runtime_pipeline.py;
- correção do log do loop detector;
- persistência de _last_message_history.

Essas mudanças podem ter efeitos laterais locais, mas não explicam degradação sistêmica do comportamento recursivo.

## Commit 3b686b3

## Resumo funcional

Esse commit reverte cd683c3 por inteiro.

## Efeito prático

### O que ele removeu de ruim

- removeu a auto-finalização por text-only stall;
- removeu a recovery nudge relaxada;
- removeu a propagação dessas heurísticas para o loop interno;
- removeu a mudança de role em _default_answer;
- removeu o abort rígido via _loop_detector_critical.

### O que ele removeu de bom junto

- removeu a correção do limiar de MCTS;
- removeu allow_partial_compose na estimativa de tokens;
- removeu os ajustes corretos dos testes de servidor;
- removeu também correções utilitárias como _last_message_history e padronização de verbose.enabled.

## Diagnóstico do commit 3b686b3

Esse commit não é a origem da degradação do RLM. Ele é uma reversão global. O problema dele é outro: por ser um revert em bloco, ele reabre bugs legítimos ao mesmo tempo em que remove mudanças ruins.

Em outras palavras:

- como contenção emergencial do runtime, faz sentido;
- como estado estável de engenharia, não faz.

## Tabela de risco por mudança

| Mudança | Arquivo | Tipo | Risco de quebrar o runtime recursivo | Observação |
| --- | --- | --- | --- | --- |
| Mostrar skills no TUI | rlm/cli/tui.py | UI | Baixo | Não afeta o loop central |
| Expor source_path em skill_doc | rlm/core/skill_loader.py | UX/inspeção | Baixo | Sem impacto no runtime |
| Testes de entry point | tests/test_cli.py | Teste | Nulo | Fora do runtime |
| Threshold do MCTS | rlm/core/mcts.py | Correção lógica | Baixo | Tende a melhorar, não piorar |
| allow_partial_compose | rlm/core/skill_loader.py | Correção lógica | Baixo | Mede melhor subconjuntos |
| Testes api + runtime_pipeline | tests/*.py | Teste | Nulo | Apenas alinhamento com refactor |
| verbose.enabled | rlm/core/rlm.py | Infra interna | Baixo | Mudança local |
| _last_message_history | rlm/core/rlm.py | Estado interno | Baixo | Persistência do histórico |
| _loop_detector_critical | rlm/core/rlm.py | Controle de abort | Médio | Pode abortar cedo em falso positivo |
| _default_answer role user | rlm/core/rlm.py | Semântica de prompt | Médio | Pode alterar framing final |
| Recovery nudge permitindo FINAL imediato | rlm/core/rlm.py | Política comportamental | Alto | Incentiva encurtamento do loop |
| Text-only stall auto-finalize | rlm/core/rlm.py | Política comportamental | Muito alto | Candidato principal à regressão |

## Sequência causal mais provável

### Cenário mais provável

1. cd683c3 entrou com correções válidas misturadas com heurísticas comportamentais agressivas.
2. O runtime passou a aceitar saída textual cedo demais.
3. O modelo deixou de insistir em repl, ferramentas, exploração e subagentes.
4. Os traces ficaram mais curtos e genéricos.
5. O usuário percebeu piora sistêmica apesar dos testes verdes.
6. 3b686b3 foi criado para conter o problema, mas levou embora as correções boas junto.

### Leitura honesta

Os testes que ficaram verdes em cd683c3 não validavam a propriedade mais importante do produto: permanência da recursão como centro operacional.

O pacote passou na suíte, mas falhou no comportamento de produto.

## Conclusões operacionais

### Conclusão 1

01c2cf7 não é o culpado.

### Conclusão 2

cd683c3 contém correções boas e mudanças ruins no mesmo lote.

### Conclusão 3

O ponto mais provável de quebra do RLM está em rlm/core/rlm.py, especificamente nas heurísticas de encerramento precoce por texto e na mudança de incentivo da recovery nudge.

### Conclusão 4

3b686b3 foi útil para parar a regressão comportamental, mas não serve como solução final porque também removeu correções legítimas.

## Direção recomendada para a próxima análise

Separar o pacote cd683c3 em duas classes:

### Manter ou reaplicar

- correção do limiar de MCTS;
- allow_partial_compose em estimate_tokens;
- correção dos testes que precisam ler api.py + runtime_pipeline.py;
- correções utilitárias seguras de rlm.py, se confirmadas isoladamente:
  - verbose.enabled;
  - _last_message_history;
  - correção do print do loop detector.

### Não reaplicar sem nova evidência forte

- text-only stall auto-finalize;
- recovery nudge permitindo finalização imediata;
- mudança de role em _default_answer;
- qualquer abort rígido adicional no loop sem validação comportamental específica.

## Perguntas que a próxima rodada deve responder

1. O runtime volta a usar subagentes e paralelismo quando só as correções seguras são reaplicadas?
2. O loop detector crítico aborta demais em cenários legítimos?
3. O framing de _default_answer como user melhora algo real ou só muda estilo?
4. É possível criar testes de comportamento que detectem morte precoce da recursão?

## Resumo final

Se for necessário apontar um único local onde o RLM provavelmente quebrou, o melhor palpite técnico é este:

- arquivo: rlm/core/rlm.py
- commit: cd683c3
- mecanismo: heurística de auto-finalização textual somada ao relaxamento da recovery nudge

Se for necessário apontar o erro de processo que permitiu isso, é este:

- misturar correções estruturais legítimas com mudanças de política comportamental no mesmo commit.