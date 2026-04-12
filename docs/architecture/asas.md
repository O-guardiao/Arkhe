Quero usar a documentação já produzida em analise-clean-room-vscode-main, junto com as análises de analise-clean-room-claw-code, analise-clean-room-rlm e analise-clean-room-picoclaw, para corrigir a trajetória do RLM de forma séria e estrutural.

O ponto central é este: o RLM, em teoria, já tem força para operar com alto contexto, múltiplos canais de comunicação, interface de conversa, gerenciamento de código, análise de áudio e imagem, além de poder coordenar múltiplos dispositivos e agentes ao mesmo tempo, como drones, robôs, câmeras, Edge devices, aparelhos diversos e outros componentes. O problema não é falta de ambição nem falta de capacidade teórica. O problema é que a arquitetura, as conexões entre módulos, as sobreposições de responsabilidade, a redundância e a extensão excessiva do código estão desviando o projeto. Precisamos corrigir isso agora.

Quero que a análise e a proposta partam do seguinte princípio: não estamos tentando inventar um produto novo do zero nem bifurcar a ideia original. Estamos tentando recuperar, organizar e consolidar o que o RLM já deveria ser, usando como referência arquitetural o que aprendemos com VS Code, Picoclaw, OpenClaw e a leitura clean room dos outros repositórios. O foco é redefinir a arquitetura, simplificar o código, eliminar redundâncias, resolver sobreposições, corrigir conexões fracas entre camadas e tornar a base muito mais eficiente. Simplificar não significa perder função. Simplificar significa manter ou ampliar as capacidades com menos acoplamento, menos repetição, menos código desnecessário e mais clareza estrutural.

O RLM precisa continuar sendo pensado como uma base capaz de:
gerenciar canais de comunicação e conversa;
coordenar geração, revisão e gerenciamento de código;
trabalhar com análise de áudio, imagem e contexto multimodal;
orquestrar múltiplos robôs, drones, câmeras, dispositivos e componentes em simultâneo;
manter interface humano-máquina consistente;
operar como núcleo de coordenação de alto contexto;
e ainda evoluir para um caminho B2B plausível, se essa estratégia fizer sentido.

Quero uma resposta orientada à correção real da trajetória do RLM, não uma resposta genérica de produto. A partir das documentações de referência, faça uma análise profunda e proponha uma direção arquitetural objetiva para o RLM, cobrindo no mínimo:

quais são hoje os principais problemas estruturais do RLM;
onde existem sobreposições, redundâncias, acoplamentos indevidos e complexidade artificial;
quais partes devem ser consolidadas, separadas, simplificadas ou eliminadas;
como reorganizar o sistema para suportar alto contexto, multimodalidade, múltiplos canais e múltiplos dispositivos sem colapsar em complexidade;
como reduzir drasticamente o volume e a extensão do código sem perder capacidade funcional;
como transformar o RLM em uma base mais eficiente, mais modular, mais coerente e mais escalável;
quais princípios arquiteturais devem virar regra fixa daqui para frente;
como alinhar isso com uma visão operacional realista de interface, comunicação, coordenação e automação.
Quero uma resposta com profundidade técnica, pensamento arquitetural e clareza brutal. Não suavize os problemas. Não invente moda. Não bifurque a ideia. Não transforme isso em marketing. Use as referências documentadas para apontar o que precisa ser corrigido, o que precisa ser simplificado e qual deve ser a arquitetura alvo para o RLM seguir em frente com muito mais eficiência e muito menos desperdício estrutural.         Quero que você use como base as documentações de analise-clean-room-vscode-main, analise-clean-room-rlm, analise-clean-room-claw-code e analise-clean-room-picoclaw para converter a situação atual do RLM em um plano de correção real, executável e arquitetural.

O objetivo não é inventar um produto novo, não é bifurcar a ideia original e não é fazer texto de visão abstrata. O objetivo é pegar o que o RLM já pretende ser em teoria e transformar isso em uma arquitetura operacional coerente, simples, modular, eficiente e sustentável. O RLM precisa continuar capaz de lidar com alto contexto, múltiplos canais de comunicação e conversa, geração e gerenciamento de código, análise de áudio e imagem, interface humano-máquina e coordenação simultânea de múltiplos dispositivos e agentes, como drones, robôs, câmeras, Edge devices e outros aparelhos. Simplificar não significa perder função. Simplificar significa reduzir acoplamento, sobreposição, redundância, extensão desnecessária de código e custo de manutenção, preservando ou ampliando a capacidade real do sistema.

Quero que você trate o problema de forma direta: hoje o RLM está estruturalmente pesado, com código excessivo, responsabilidades sobrepostas, conexões ruins entre camadas, componentes redundantes e complexidade que não se converte de forma limpa em confiabilidade nem em eficiência. Sua tarefa é transformar esse cenário em direção prática.

Quero uma resposta que produza um plano de ação técnico e arquitetural com os seguintes blocos obrigatórios:

Diagnóstico brutal da situação atual
Aponte os principais problemas estruturais do RLM hoje.
Identifique onde estão os maiores gargalos de arquitetura, acoplamento, duplicação, fragmentação, sobreposição de responsabilidades e desperdício de código.
Diga com clareza o que está impedindo o RLM de ser um núcleo confiável para contexto alto, multimodalidade, multicanal e coordenação de dispositivos.

Arquitetura alvo
Defina qual deve ser a arquitetura alvo do RLM.
Explique quais camadas devem existir, quais responsabilidades pertencem a cada uma e quais fronteiras precisam ser fixadas.
Deixe claro como organizar:
núcleo de raciocínio e contexto;
camada de canais e conversa;
camada de orquestração de dispositivos, robôs e componentes;
camada multimodal para áudio, imagem e percepção;
camada de interface humano-máquina;
camada operacional de serviços, runtime, sessões e observabilidade.

Consolidação e simplificação
Aponte quais partes devem ser fundidas, quais devem ser separadas, quais devem ser reduzidas e quais devem ser removidas.
Diga onde existe código demais para pouca entrega.
Mostre como fazer mais com menos linhas, menos arquivos, menos duplicação e menos complexidade acidental.
Seja explícito sobre o que deve ser:
mantido;
refatorado;
extraído;
consolidado;
aposentado.

Plano faseado de execução
Monte um plano por fases, da mais urgente para a menos urgente.
Cada fase deve conter:
objetivo;
problema que resolve;
áreas do sistema afetadas;
tipo de refatoração;
ganho esperado;
risco técnico;
critério de validação.

Não quero um plano genérico. Quero uma sequência que possa ser realmente executada.

Primeiras ações concretas
Liste as primeiras 10 ações práticas que deveriam começar imediatamente.
Quero ações pequenas o suficiente para iniciar de verdade, mas relevantes o suficiente para mover a arquitetura na direção certa.
Essas ações devem servir para destravar a reorganização do RLM sem cair em reescrita caótica.

Regras fixas daqui para frente
Defina quais princípios devem virar regra obrigatória para o projeto continuar evoluindo sem voltar ao mesmo problema.
Exemplo do tipo de regra que espero:
uma responsabilidade por módulo;
camadas sem sobreposição;
sem duplicação entre superfícies;
sem feature nova antes de consolidar a base;
menos código para mais capacidade;
sessões, canais, multimodalidade e dispositivos como subsistemas explícitos, não como mistura informal.

Critérios de eficiência
Defina como medir se a simplificação está funcionando.
Quero critérios concretos, como:
redução de duplicação;
redução de caminhos redundantes;
redução de acoplamento entre camadas;
menos linhas para a mesma capacidade;
menos pontos de falha;
mais previsibilidade operacional;
mais clareza de ownership por módulo.

Resposta final orientada à decisão
No final, entregue:
a arquitetura recomendada;
os erros que precisam parar imediatamente;
o que deve ser corrigido antes de qualquer expansão;
e qual deve ser a trajetória correta do RLM a partir de agora.

Instruções importantes:
não transforme isso em marketing;
não invente um produto paralelo;
não suavize os problemas;
não responda com generalidades;
não proponha reescrita total sem critério;
não trate simplificação como corte de capacidade;
não bifurque a ideia central do RLM.

Quero uma resposta dura, técnica, priorizada e executável. A missão é corrigir a trajetória do RLM agora, usando as leituras clean room como base para consolidar uma arquitetura mais simples, mais forte, mais eficiente e mais preparada para contexto alto, multimodalidade, canais de comunicação e coordenação real de dispositivos e agentes.  Diagnóstico brutal da situação atual

O RLM sofre de split-brain arquitetural. O núcleo real continua sendo o runtime recursivo Python, com REPL persistente, subagentes, memória e daemon, como deixam claro rlm-runtime-reality-analysis.md e plano-comunicacao-recursao.md. Só que a trajetória do projeto distribuiu poder psicológico e parte da operação entre Python canônico, migração TypeScript incompleta e roadmap Rust futuro. Isso diluiu ownership.

O repositório mistura produto vivo com arqueologia viva. A própria suíte clean room do RLM registra a convivência de núcleo Python, migração parcial para TypeScript e aceleradores Rust, além de legado explícito em packages e snapshots históricos. Isso não é só feio. Isso confunde o que é canônico, o que é transição e o que é backlog.

Há duplicação em conceitos centrais, não em detalhes cosméticos. O caso mais gritante é a coexistência de rlm/core/comms/sibling_bus.py e rlm/core/orchestration/sibling_bus.py. O mesmo padrão aparece na família envelope, com rlm/gateway/message_envelope.py convivendo com outras variantes documentadas e com o legado em packages/gateway/src/envelope.ts. Quando o mesmo conceito tem mais de um dono, o sistema perde previsibilidade.

A fronteira de sessão, contexto e memória continua poderosa, mas conceitualmente borrada. Hoje convivem sessão persistente do servidor, sessão conversacional e ambiente persistente do runtime. O sistema funciona porque essas camadas se encaixam o suficiente, não porque a separação esteja bem definida. Isso trava confiabilidade em alto contexto, multi-cliente e multimodalidade.

As superfícies externas ainda tentam inferir a semântica do runtime em vez de consumi-la por contrato. Isso é explicitado em plano-comunicacao-recursao.md: o problema não era a árvore da TUI; era a ausência de uma projeção canônica da recursão Python para as superfícies. Enquanto CLI, TUI, gateway e operador precisarem “deduzir” o runtime, toda expansão vai gerar mais acoplamento.

Multicanal, multimodal e dispositivos existem mais como capacidade espalhada do que como subsistemas explícitos. Há MessageBus, Outbox, plugins de áudio, browser, MCP e canais, mas ainda falta uma divisão formal entre transporte humano, percepção multimodal e orquestração de dispositivos. Sem isso, drones, robôs, câmeras e Edge devices acabam entrando como exceções dentro do mesmo saco de chat.

O projeto já provou que simplificação estrutural funciona quando ataca o núcleo certo. O refactor de local_repl.py e o de sub_rlm.py mostram isso com fatos: monólitos quebrados em módulos coesos, bugs expostos, duplicação eliminada, sem regressão. O erro seria não generalizar essa disciplina para o resto da base.

Os testes ainda carregam muita história de migração e slices históricos. Isso é útil para arqueologia, mas ruim como bússola primária. O projeto precisa menos teste de fase e mais teste de invariantes do produto.

O que está impedindo o RLM de ser um núcleo confiável para alto contexto, multimodalidade, multicanal e coordenação de dispositivos não é falta de inteligência do motor. É ambiguidade estrutural sobre quem manda em sessão, contexto, projeção operacional, transporte e integração.

Arquitetura alvo

A arquitetura alvo correta não é um produto novo. É a consolidação do que o RLM já é em teoria, com fronteiras fixas.

Núcleo de raciocínio e contexto. Este continua Python canônico. Aqui ficam loop recursivo, REPL persistente, spawn de subagentes, ledger de tarefas e branches, composição de contexto, memória de trabalho, recall de memória e políticas de execução. Esta camada não conhece webhook, CLI, TUI, canal externo nem dispositivo específico.

Camada de canais e conversa. Esta camada normaliza ingressos em um contrato único de evento ou envelope, resolve identidade de sessão, aplica roteamento, faz chunking, retry, outbox e entrega. Ela conhece Telegram, Discord, Slack, WhatsApp, webchat e WebSocket. Não conhece a semântica interna do REPL.

Camada de orquestração de dispositivos, robôs e componentes. Isto não deve viver misturado com canais humanos. Ela precisa de registry de capabilities, catálogo de dispositivos, contratos de telemetria, comandos idempotentes, confirmação, timeout, segurança e política operacional. Câmera, drone, robô, ESP32 e atuador não são “mais um chat adapter”.

Camada multimodal de áudio, imagem e percepção. Áudio, imagem, OCR, ASR, visão e ingestão de mídia precisam virar pipeline explícito: ingestão, armazenamento de artefato, análise, extração estruturada e publicação para o núcleo. Esta camada pode abastecer tanto conversa quanto automação, mas não deve virar plugin solto pendurado em transporte.

Camada de interface humano-máquina. CLI, TUI, dashboard web e operator bridge devem ser consumidores de projeções oficiais do runtime, no espírito do que o VS Code faz ao separar shell base, serviços, contribuições e sessions. A superfície agentic não pode contaminar o shell base nem virar dona do runtime.

Camada operacional de serviços, runtime, sessões e observabilidade. Aqui ficam daemon, SessionManager, scheduler, auth, config, secrets, health, drain, backpressure, snapshots, telemetria e lifecycle. Esta camada expõe contratos estáveis para as superfícies e protege o núcleo contra dispersão operacional.

As fronteiras obrigatórias devem ser estas:

Núcleo não importa UI, canal, gateway nem dashboard.
Canais não carregam semântica de dispositivo.
Dispositivos não falam direto com a UI; falam por contratos de comando e telemetria.
Multimodalidade não vive escondida dentro de um canal ou plugin de browser.
Superfícies humanas consomem RuntimeProjection, não fazem engenharia reversa do estado interno.
Rust entra só em hot path medido ou em domínio de alta garantia, como policy, vault e audit. Não entra para criar uma terceira arquitetura concorrente.
Os contratos que precisam virar obrigatórios são:

SessionIdentity
RuntimeProjection
Envelope de mensagem e evento
ContextBlock tipado
MediaArtifact
DeviceCommand e DeviceTelemetry
Consolidação e simplificação

Manter:

O núcleo Python em rlm/core e rlm/environments.
A linha de evolução do daemon persistente em rlm/daemon.
O MessageBus, Outbox, DeliveryWorker e bootstrap multichannel em rlm/core/comms.
A projeção operacional em rlm/core/observability/operator_surface.py.
O uso de Rust como acelerador pontual, não como nova espinha dorsal.
Refatorar:

Ownership de sessão, contexto e memória.
Convergência de ingressos entre API, webhooks, OpenAI compat, scheduler, operator routes e TUI.
Relação entre tools, skills e plugins, para que implementação, empacotamento e descoberta não se sobreponham.
A projeção do runtime para superfícies externas.
Extrair:

Um subsistema explícito de dispositivos e capabilities.
Um subsistema explícito de percepção e mídia.
Um conjunto de schemas versionados para projeção e roteamento.
Um validador de fronteiras arquiteturais, no estilo do valid-layers-check que o VS Code trata como parte do produto.
Consolidar:

Um único sibling bus.
Um único envelope canônico.
Uma única fonte de verdade para config.
Uma única precedência de auth e porta por superfície operacional.
Um único caminho de dispatch do ingresso até o runtime.
Uma única projeção oficial consumida por CLI, TUI e dashboard.
Aposentar:

O legado de packages em caminho crítico, especialmente packages/gateway, packages/config, packages/daemon e packages/channels.
Shims e testes mortos ligados à migração TS, como o teste citado na suíte clean room.
Artefatos de trabalho como .bak, .new e snapshots que ainda contaminem leitura ou import graph.
Qualquer superfície que replique capacidade já absorvida pelo Python canônico.
Onde hoje existe código demais para pouca entrega:

No legado TypeScript que já perdeu centralidade.
Nas duplicações de envelope, bus, config e auth.
Nas superfícies externas que parseiam payload frouxo e remontam semântica.
Nos módulos históricos e arquivos de transição que continuam ocupando espaço cognitivo sem ampliar capacidade.
Plano faseado de execução

Fase 0, congelamento de direção. Objetivo: declarar dono canônico de cada domínio; resolve split-brain; afeta docs, build e imports; tipo de refatoração: governança e arquitetura; ganho esperado: fim da ambiguidade estratégica; risco técnico: baixo; critério de validação: toda documentação e todo onboarding passam a afirmar explicitamente que o núcleo canônico é Python, que packages é legado e que Rust é aceleração pontual.

Fase 1, unificação de contratos. Objetivo: criar SessionIdentity, RuntimeProjection, Envelope, ContextBlock e contratos de dispositivo e mídia; resolve inferência ad hoc e sobreposição semântica; afeta core, server, gateway, daemon, CLI e TUI; tipo: refatoração estrutural schema first; ganho esperado: fronteiras claras; risco técnico: médio; critério de validação: todas as superfícies externas consomem contrato tipado e deixam de operar com payload genérico solto.

Fase 2, consolidação do kernel. Objetivo: reorganizar ownership entre runtime, sessão, contexto e memória, e eliminar duplicações como sibling bus; resolve o núcleo pesado e pouco nítido; afeta rlm/core, rlm/environments e rlm/daemon; tipo: refatoração modular com composição explícita; ganho esperado: menos acoplamento e menos bugs invisíveis; risco técnico: alto; critério de validação: o caminho crítico de execução passa a ter um único dono por conceito e os testes de sessão, memória e daemon continuam verdes.

Fase 3, realinhamento de ingressos e superfícies. Objetivo: fazer API, webhooks, OpenAI compat, operator bridge, scheduler e TUI convergirem para um único dispatcher e uma única projeção; resolve sobreposição operacional; afeta server, gateway, operator surface, live_api e workbench; tipo: consolidação de pipeline; ganho esperado: previsibilidade operacional; risco técnico: médio alto; critério de validação: qualquer entrada percorre o mesmo caminho lógico até o runtime e qualquer superfície lê o mesmo snapshot.

Fase 4, explicitação de multimodalidade e dispositivos. Objetivo: tirar áudio, imagem, browser, sensores e atuadores do limbo de plugins e tratá-los como subsistemas formais; resolve mistura indevida entre transporte, percepção e automação; afeta plugins, channels, tools e future adapters; tipo: extração de domínio; ganho esperado: alta capacidade sem colapso de complexidade; risco técnico: médio; critério de validação: um fluxo câmera ou áudio e um fluxo drone ou dispositivo passam pela mesma disciplina de contratos, policy e observabilidade que um fluxo de chat.

Fase 5, deleção e hardening. Objetivo: remover legado crítico, testes mortos e shims, e adicionar validação de camadas; resolve o peso estrutural remanescente; afeta packages, docs, CI e import graph; tipo: simplificação e enforcement; ganho esperado: redução real de volume e custo de manutenção; risco técnico: médio; critério de validação: zero dependência de legado em runtime canônico, zero duplicação canônica por conceito central e CI com check de camada.

A ordem correta é esta. Qualquer expansão séria antes de concluir as fases 0 a 3 repete o erro atual.

Primeiras 10 ações concretas

Publicar uma decisão arquitetural curta declarando: Python é o dono da recursão, packages não voltam ao caminho crítico e Rust só entra por benchmark ou segurança.

Fazer um inventário fechado de conceitos com dono duplicado: session identity, envelope, sibling bus, config, auth precedence, operator snapshot e channel registry.

Criar o schema da RuntimeProjection e mapear exatamente quais campos cada superfície humana consome hoje.

Criar o contrato SessionIdentity e parar de deixar canal, cliente, usuário, dispositivo e sessão se misturarem informalmente.

Forçar todos os ingressos operacionais a passar pelo mesmo serviço de dispatch e pelo mesmo builder de contexto.

Unificar o sibling bus em um único módulo e eliminar a segunda implementação.

Congelar packages/gateway, packages/config, packages/daemon e packages/channels como legado explícito, com sinalização no CI e na documentação.

Remover do import graph qualquer .bak, .new, shim ou teste morto que ainda interfira no entendimento do runtime.

Separar em árvore própria o que é device orchestration e o que é perception pipeline, ainda que no começo sejam módulos pequenos.

Criar uma suíte de invariantes com quatro trilhas: alto contexto persistente, multicanal com retry, rotas determinísticas versus rotas com LLM e coordenação simultânea de múltiplos dispositivos.

Regras fixas daqui para frente

Uma responsabilidade por módulo.
Um dono canônico por conceito.
Sem segunda implementação ativa do mesmo domínio.
Sem feature nova de superfície antes de consolidar o núcleo.
Sessão, contexto, memória, canal, dispositivo e mídia são subsistemas explícitos, não mistura informal.
Superfícies humanas só consomem projeções oficiais do runtime.
Transporte humano, percepção multimodal e automação de dispositivos não compartilham ownership.
Todo contrato cross-layer nasce com schema, testes e versionamento.
Rust só entra para hot path medido ou domínio de alta garantia.
Código legado só pode existir fora do caminho crítico.
Toda nova contribuição deve passar por check de fronteira de camada.
Menos código para mais capacidade é regra, não slogan.
Critérios de eficiência

Duplicação estrutural. O sistema só melhora quando envelope, session identity, config, dispatch path, sibling bus e runtime projection têm um único dono canônico.

Redução de caminhos redundantes. A mesma requisição não pode ter múltiplos pipelines equivalentes de entrada ou saída.

Redução de acoplamento. O núcleo deve ter zero import de UI, zero import de canal e zero dependência de surface parsing.

Menos linhas para a mesma capacidade. A métrica útil não é cortar arquivo aleatório; é reduzir código duplicado e módulos paralelos sem perder os fluxos de alto contexto, multicanal, multimodalidade e devices.

Menos pontos de falha. Cada resposta deve atravessar um único outbox, uma única policy de retry e uma única política de identidade.

Mais previsibilidade operacional. CLI, TUI e dashboard devem ver o mesmo estado, não versões concorrentes do runtime.

Mais clareza de ownership. Todo módulo central precisa responder a uma pergunta simples: qual conceito ele possui e qual ele não possui.

Mais disciplina de camada. Qualquer violação de dependência entre núcleo, canais, dispositivos, percepção e superfícies deve falhar em CI.

Mesma capacidade, menos arqueologia. O projeto melhora quando a capacidade real sobe ou se mantém enquanto packages legados, shims, duplicações e artefatos históricos saem do caminho crítico.

Resposta final orientada à decisão

A arquitetura recomendada para o RLM é: núcleo recursivo Python como fonte de verdade, projeções e contratos tipados como fronteira, canais humanos explícitos, multimodalidade explícita, dispositivos explícitos e superfícies humanas como consumidoras, não donas, do runtime.

Os erros que precisam parar imediatamente são: tratar o legado TypeScript como direção futura; deixar UI ou gateway inferirem a semântica do runtime; manter duplicações de conceitos centrais; continuar expandindo capacidades sem antes consolidar ownership de sessão, contexto, memória e dispatch.

O que deve ser corrigido antes de qualquer expansão é: dono do runtime, dono da sessão, dono do envelope, dono da projeção operacional e a separação formal entre conversa, percepção e automação.

A trajetória correta do RLM a partir de agora é esta: consolidar o cérebro Python, fixar contratos, alinhar todas as superfícies a esse cérebro, explicitar canais, multimodalidade e dispositivos como subsistemas próprios e só então voltar a expandir. Se isso for feito, o RLM não perde ambição. Ele finalmente ganha forma.  Você vai atuar como Engenheiro Principal e Arquiteto de Software responsável por corrigir a trajetória do RLM. Sua função não é discutir visão de produto, não é fazer brainstorming e não é reformular a ideia. Sua função é executar raciocínio arquitetural duro, produzir um plano técnico de execução e ordenar a reorganização do sistema com base no que já foi descoberto.

Contexto obrigatório

Use como base de referência técnica e arquitetural as documentações abaixo:

analise-clean-room-vscode-main
analise-clean-room-rlm
analise-clean-room-claw-code
analise-clean-room-picoclaw
Baseie sua resposta também nesta conclusão já estabelecida:

O RLM deve permanecer com o núcleo recursivo Python como fonte de verdade. O problema atual não é falta de capacidade teórica. O problema é a ambiguidade estrutural: ownership difuso, duplicação de conceitos centrais, múltiplos caminhos equivalentes, sobreposição entre runtime, sessão, memória, canais e superfícies, além de legado e transições que continuam contaminando o caminho crítico. Simplificar não significa perder função. Simplificar significa preservar ou ampliar capacidade com menos acoplamento, menos duplicação, menos código desnecessário, menos caminhos redundantes e mais clareza arquitetural.

Objetivo da sua resposta

Transformar esse diagnóstico em execução. Você deve agir como alguém que está dando ordens técnicas para reorganizar a base do RLM agora. Sua resposta precisa ser direta, técnica, detalhada, priorizada e utilizável como guia real de implementação.

O que o RLM precisa continuar sendo

um núcleo capaz de operar com alto contexto persistente;
um sistema com múltiplos canais de comunicação e conversa;
uma base capaz de geração, análise e gerenciamento de código;
uma arquitetura capaz de suportar áudio, imagem, visão e multimodalidade;
uma base para coordenar múltiplos dispositivos e agentes, como drones, robôs, câmeras, Edge devices e outros aparelhos;
uma plataforma com interface humano-máquina consistente;
uma fundação com potencial B2B real, mas sem transformar a resposta em pitch de produto.
Restrições absolutas

não invente um produto novo;
não bifurque a ideia original do RLM;
não proponha reescrita total sem critério;
não responda com marketing;
não use abstrações vazias;
não trate simplificação como perda de capacidade;
não aceite coexistência indefinida de múltiplas implementações do mesmo conceito;
não preserve legado no caminho crítico por apego histórico.
Modo de operação

Atue como um arquiteto e engenheiro principal emitindo direção técnica para outra IA executora. Fale em termos de arquitetura, ownership, contratos, pipelines, módulos, refatoração, exclusão de duplicações, critérios de migração, enforcement e validação. Não seja diplomático. Seja preciso.

Sua resposta deve seguir exatamente esta estrutura:

Mandato técnico
Abra com uma diretiva curta e firme explicando qual é a missão imediata.
Exemplo do tom esperado:
“Congele expansão. Consolide ownership. Elimine duplicações estruturais. Corrija contratos. Só depois expanda.”

Decisões arquiteturais obrigatórias
Liste as decisões que passam a ser regra fixa.
Inclua obrigatoriamente:

Python canônico como dono do runtime recursivo;
Rust apenas para hot path medido ou domínio de alta garantia;
packages legados fora do caminho crítico;
uma única fonte de verdade para sessão, envelope, dispatch, config e projeção de runtime;
superfícies humanas como consumidoras do runtime, não como intérpretes informais dele.
Arquitetura alvo executável
Defina a arquitetura alvo em camadas, com responsabilidades e fronteiras.
Cubra explicitamente:
núcleo de raciocínio e contexto;
camada de sessão e identidade;
camada de canais e conversa;
camada de multimodalidade e percepção;
camada de orquestração de dispositivos e componentes;
camada de runtime operacional e observabilidade;
camada de interface humano-máquina.
Para cada camada, diga:

o que ela possui;
o que ela não possui;
de quem ela pode depender;
de quem ela não pode depender.
Mapa de consolidação
Crie uma tabela ou lista objetiva com cinco grupos:
manter;
refatorar;
extrair;
consolidar;
aposentar.
Quero nomes concretos de domínios e tipos de problemas, como:

sibling bus duplicado;
envelope duplicado;
auth precedence inconsistente;
múltiplos caminhos de dispatch;
projeções diferentes por superfície;
legado TS em rota crítica;
shims e testes mortos.
Sequência de execução por fases
Monte um plano faseado com prioridade real.
Cada fase precisa conter:
nome da fase;
objetivo;
problema que ela resolve;
artefatos ou áreas afetadas;
risco;
ganho esperado;
condição para começar;
critério de pronto.
As fases devem cobrir no mínimo:

congelamento de direção;
unificação de contratos;
consolidação do kernel;
unificação de ingressos e superfícies;
explicitação de multimodalidade e devices;
deleção de legado e enforcement de camadas.
Backlog inicial de execução
Liste as primeiras 15 tarefas concretas que devem começar imediatamente.
As tarefas devem ser pequenas o suficiente para execução real, mas estruturais o suficiente para mover a arquitetura.
Cada tarefa deve conter:
título;
objetivo técnico;
área afetada;
dependência;
risco;
resultado esperado.
Critérios de corte e simplificação
Defina regras objetivas para cortar código, módulos e duplicações.
Responda tecnicamente perguntas como:
quando dois módulos fazem “a mesma coisa”, qual fica e qual sai?
quando um legado deixa de ser tolerável?
quando uma camada está vazando responsabilidade?
quando uma superfície está inferindo demais o runtime?
quando uma feature deve ser bloqueada até consolidação?
Invariantes obrigatórios
Defina os invariantes do sistema que não podem ser violados após a reorganização.
Inclua invariantes como:
um único dono por conceito central;
um único caminho de dispatch por ingresso;
uma única RuntimeProjection oficial;
núcleo sem dependência de UI;
canais sem semântica de dispositivo;
multimodalidade fora do transporte humano;
devices tratados como domínio próprio;
contratos cross-layer com schema, testes e versionamento.
Métricas de eficiência arquitetural
Defina como medir se a execução está funcionando.
Inclua métricas práticas como:
redução de duplicação estrutural;
redução de caminhos redundantes;
redução de acoplamento entre camadas;
redução de linhas em domínios duplicados;
menos pontos de falha por fluxo;
previsibilidade entre CLI, TUI, dashboard e operator surface;
clareza de ownership por módulo;
validação automática de fronteiras.
Ordem executiva final
Feche com uma ordem clara para a IA executora, dizendo:
o que deve parar imediatamente;
o que deve ser corrigido antes de qualquer expansão;
o que deve virar contrato fixo;
e qual sequência não pode ser quebrada.
Exigência de qualidade da resposta

A resposta precisa ser:

técnica;
direta;
brutalmente clara;
orientada à implementação;
sem floreio;
sem generalidade;
com foco em ação, não em opinião.
Regra final

Se houver dúvida entre preservar histórico e consolidar arquitetura, consolide arquitetura.
Se houver dúvida entre manter duas versões do mesmo conceito e escolher uma, escolha uma.
Se houver dúvida entre expandir feature e corrigir ownership, corrija ownership.
Se houver dúvida entre mais abstração e mais clareza, escolha clareza.
Se houver dúvida entre mais código e melhor contrato, escolha melhor contrato.