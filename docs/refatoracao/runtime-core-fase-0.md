# Runtime Core Fase 0

## Objetivo

Esta fase abre a refatoracao seria do Arkhe na direcao definida em [../../../arquitetura-de-linguagens-para-ecossistema-agentico.md](../../../arquitetura-de-linguagens-para-ecossistema-agentico.md): Python continua como orquestrador, mas policy, approval, audit e vault passam a ter fronteiras explicitas para extracao futura em Rust.

## Corte Inicial

O primeiro corte foi concentrado em runtime guard e nao em UI ou canais porque ele atende tres requisitos ao mesmo tempo:

- reduz acoplamento do pipeline central
- prepara substituicao por nucleo nativo sem quebrar API publica
- cria um ponto unico para registrar riscos e regras de seguranca

## Mapa De Contexto

### Arquivos centrais nesta fase

- rlm/server/runtime_pipeline.py
- rlm/server/api.py
- rlm/cli/tui.py
- rlm/core/execution_policy.py
- rlm/core/exec_approval.py
- rlm/core/security.py
- rlm/tools/vault_tools.py

### Nova fronteira criada

- rlm/runtime/contracts.py
- rlm/runtime/python_runtime_guard.py
- rlm/runtime/__init__.py

## Gaps Encontrados Durante A Refatoracao

### Gap 1 - runtime_pipeline acumula responsabilidades demais

Hoje o pipeline mistura policy inference, audit de entrada, injection de ferramentas, memory bridge, handoff e override de modelo. Isso torna a extracao para Rust inviavel sem criar uma camada de compatibilidade antes.

### Gap 2 - approval state e apenas em memoria

ExecApprovalGate mantem pendencias e resolucoes apenas em memoria do processo. Se o servidor reiniciar, a trilha de aprovacao e perdida. Isso conflita com a meta de auditoria forte.

### Gap 3 - vault tools silenciam falhas demais

Vault search, read e corrections usam varios excepts amplos. Isso mascara erro real de integracao, permissao e encoding. O comportamento atual privilegia continuidade, nao confianca operacional.

### Gap 4 - security e policy ainda sao heuristicas locais

Execution policy e input audit ainda dependem de heuristicas e tabelas Python no processo principal. Isso funciona para iteracao, mas nao e uma base forte para trust boundary de longo prazo.

### Gap 5 - cultura de fallback silencioso continua forte

O codebase ainda possui muitos except Exception com continuidade silenciosa. Isso precisa ser reduzido por dominio critico antes da migracao nativa.

## Entrega Desta Fase

- fronteira de runtime guard introduzida em rlm/runtime/
- API e TUI passam a construir o runtime por essa fronteira
- runtime pipeline passa a consumir policy, security, approval e vault por porta compativel
- compatibilidade preservada com implementacao Python atual
- primeiro crate Rust criado em native/arkhe-policy-core com contratos e heuristica inicial de policy
- bridge Python -> Rust de policy entregue via subprocess JSON com fallback imediato para Python
- contrato serializavel de policy publicado no lado Python para sustentar a migracao multi-linguagem

## Modo Nativo De Policy

Esta fase adiciona a primeira ponte real Python -> Rust sem mover a recursao para fora do orquestrador Python.

- ativacao: RLM_NATIVE_POLICY_MODE=native ou auto
- binario explicito: RLM_NATIVE_POLICY_BIN
- timeout da chamada: RLM_NATIVE_POLICY_TIMEOUT_MS
- binario descoberto automaticamente se existir em native/arkhe-policy-core/target/debug/ ou release/
- para ativar de verdade sem apontar path manual, compile o crate com cargo build dentro de native/arkhe-policy-core

Com isso, recursao, seguranca operacional e coerencia do runtime continuam no Python, enquanto a decisao heuristica de policy pode ser executada no binario Rust quando disponivel.

## Gap Adicional Encontrado Na Validacao

### Gap 6 - compatibilidade futura com Python 3.14

Durante a validacao, tests/test_security.py expuseram warning deprecado em rlm/core/security.py por uso de ast.Str. Isso nao quebra a fase atual, mas precisa ser trocado por ast.Constant antes do alvo Python 3.14.

## Proxima Fase Recomendada

1. Extrair approval records, policy inputs e audit events para contratos serializaveis estaveis.
2. Persistir approval lineage e audit trail fora da memoria do processo.
3. Medir latencia e taxa de fallback da bridge subprocess antes de considerar FFI ou pyo3.
4. Extrair approval core e audit trail persistente para o proximo modulo nativo.
5. Reduzir excepts amplos nos dominios criticos antes da troca efetiva de backend.
