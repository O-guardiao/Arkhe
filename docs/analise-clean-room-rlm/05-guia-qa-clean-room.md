# 05. Guia de QA e Clean Room

## Objetivo

Este documento traduz a leitura do repositorio em um metodo pratico de revisao para a sua reimplementacao.

Ele existe para impedir dois erros comuns:

- paridade falsa: quando o codigo parece parecido, mas nao preserva o comportamento certo
- copia disfarcada: quando a solucao replica nomes, sequencias e estruturas internas sem necessidade tecnica

## Contratos de comportamento a preservar

### Engine recursivo

- existe um loop iterativo que alterna LLM, execucao e feedback
- o sistema suporta subagentes/subchamadas com limite de profundidade
- contexto pode ser compactado e o runtime precisa sobreviver a conversas longas
- memoria e sessao nao sao anexos; fazem parte da logica principal

### Runtime persistente

- o produto consegue manter ambiente aquecido entre interacoes
- ha snapshots operacionais do runtime, nao apenas logs soltos
- parte dos eventos pode ser tratada deterministicamente, sem executar o pipeline pesado completo

### Multichannel

- canais distintos convergem para um contrato comum de entrega e sessao
- ha auth, backpressure, drain e lifecycle operacional
- mensagens podem ser roteadas, reenviadas e publicadas sem vazar entre sessoes ou canais

### Superficie CLI e operacao

- o produto tem setup, status, diagnostico e operacao de servico reais
- TUI/CLI sao superficies do produto, nao scripts descartaveis
- logging estruturado e trajetorias observaveis fazem parte da experiencia operacional

### Seguranca e politica

- execucao de codigo passa por fences, politicas e possivel aprovacao
- tokens, secrets e credenciais sao tratados como riscos centrais
- integrations como MCP, browser e canais ampliam a superficie de ataque

## Sinais de copia disfarcada

Alertas fortes de violacao de clean room:

- repetir nomes como `RLM`, `RecursionDaemon`, `LMHandler`, `sub_rlm`, `ContextCompactor`
- manter a mesma sequencia exata de etapas internas do loop por imitacao, nao por necessidade
- recriar a mesma topologia `core/engine + mixins + daemon + gateway + server` sem avaliar seu proprio dominio
- preservar arquivos espelho de trabalho historico como `.bak`, `.new`, shims e duplicacoes TS/Python
- usar nomes de papeis, enums, funcoes privadas e helpers com semantica herdada do repositorio em vez de nomes seus

## O que pode e deve ter identidade propria

- nomes de classes, modulos e helpers
- forma de compor o loop principal
- estrategia de sandbox e de runtime persistente
- modelo interno de sessao/memoria desde que preserve comportamento externo
- desenho da CLI, TUI e dashboard
- integracao com provedores, gates e auth

## Checklist de paridade de comportamento

### Sessao e memoria

- sua sessao persiste o suficiente para multi-turn real?
- sua memoria tem criterio explicito de recall, budget e consolidacao?
- contextos antigos sao resumidos ou descartados de forma previsivel?

### Runtime e recursao

- subagentes respeitam limite de profundidade?
- o loop termina com resposta final confiavel ou pode derivar indefinidamente?
- existe diferenca operacional entre rotas leves e rotas pesadas?

### Canais e gateways

- a mesma conversa pode continuar entre canais/transportes sem corromper identidade?
- retries, chunking e reconnect nao duplicam ou perdem mensagens?
- shutdown e restart preservam ou limpam o estado certo?

### CLI e operacao

- setup produz configuracao valida e explicita?
- doctor/status refletem o estado real do runtime?
- TUI e CLI mostram o que o operador precisa para diagnosticar falhas?

## Checklist de edge cases e pontos cegos

### Concorrencia

- duas mensagens simultaneas para a mesma sessao entram em corrida?
- maintenance pode podar sessao ainda ativa?
- outbox/backpressure e drain interagem corretamente sob carga?

### Integridade de estado

- runtime aquecido pode reusar contexto errado?
- um subagente pode escrever memoria com metadata de outro canal?
- reinicio parcial deixa snapshot mentiroso na TUI?

### Performance

- compaction pode se tornar gargalo com historico longo?
- retrieval sem aceleracao Rust degrada de forma aceitavel?
- CLI/TUI dependem de polling excessivo ou payloads grandes demais?

### Robustez

- provider indisponivel leva a erro explicito ou fallback incorreto?
- websocket/webhook entram em modo de erro previsivel?
- arquivos de estado legados ou corrompidos bloqueiam o produto sem necessidade?

## Checklist de seguranca e vulnerabilidades

- existe validacao de token suficiente para API, WS e operator routes?
- secrets vazam para log, transcript, artifact ou output de tool?
- fences de execucao bloqueiam import, subprocess, rede e IO perigosos?
- MCP, browser e plugins podem ampliar privilegios sem gate explicito?
- caminhos de vault/config/state sao normalizados e restringidos?
- canais externos conseguem induzir cross-channel forward malicioso ou loop de mensagens?

## Formato de revisao que eu devo usar nos seus modulos

Para cada modulo que voce enviar, a revisao deve seguir este formato:

### Status da Funcionalidade

- aprovado ou precisa de ajustes
- resumo curto do impacto

### Analise de Paridade e Originalidade

- a funcionalidade atinge ou nao o comportamento esperado
- divergencias de logica de negocio
- sinais de copia disfarcada ou de dependencia indevida do desenho original
- sugestao de abordagem mais idiomatica, quando couber

### Edge Cases e Pontos Cegos

- inputs invalidos
- concorrencia
- ordenacao de eventos
- recuperacao de falha
- gargalos de performance

### Seguranca e Performance

- superficies de ataque
- validacao e sanitizacao
- vazamento de segredo
- custo computacional, IO, memoria e rede

### Casos de Teste Sugeridos

- 3 a 5 testes unitarios ou de integracao essenciais para aquele modulo

## Testes minimos por subsistema

### Engine e recursao

- loop simples sem subagente
- loop com subagente e limite de profundidade
- compaction com historico grande
- provider com falha e recuperacao/fallback
- finalizacao correta de resposta apos multiplas iteracoes

### Testes de runtime persistente

- sessao aquecida entre dois turnos consecutivos
- maintenance sem matar sessao viva
- snapshot consistente antes e depois de restart
- rota deterministica sem queda indevida no pipeline pesado

### Gateway e canais

- webhook com token valido e invalido
- chunking sem perda ou reordenacao incorreta
- reconnect/retry sem duplicidade de entrega
- cross-channel forward sem loop infinito

### Testes de CLI/TUI

- setup gerando estado/config corretos
- doctor detectando falhas reais
- status refletindo daemon parado, vivo e drenando
- TUI reanexando a runtime vivo apos perda temporaria do probe

### Seguranca

- fence bloqueando import/exec/subprocess proibido
- segredo nao aparecendo em log/artifact
- operator route rejeitando token errado
- caminho de vault/config fora da raiz sendo negado

## Regra operacional de clean room

Use o repositorio original para entender:

- comportamento
- requisitos implicitos
- contratos externos
- casos de teste relevantes
- riscos arquiteturais

Nao use o repositorio original para copiar:

- nomes internos
- estrutura de pastas
- ordem historica dos helpers
- workaround de migracao
- duplicacoes acidentais criadas pela evolucao do repo

## Prioridade de revisao quando seu codigo chegar

A ordem mais eficaz de analise e:

1. comportamento observavel
2. seguranca e risco operacional
3. edge cases e concorrencia
4. identidade propria do design
5. estrategia de testes
