# RLM no Celular — iPhone, Android e Caminhos Reais

Documento de referência para manter aberto o caminho mobile do RLM sem cair em
promessa tecnicamente fraca.

Última atualização: 2026-03-12

---

## Resposta curta

Sim, existe caminho real para o RLM chegar a iPhone e Android.

Mas existem **três caminhos diferentes**, e misturá-los leva a decisões ruins:

1. **Mobile como cliente do RLM remoto**
   Melhor caminho de curto prazo. Muito viável.
2. **Mobile como app nativo companion**
   Melhor caminho de médio prazo. Viável e com UX superior.
3. **RLM rodando inteiro on-device**
   Possível apenas com re-arquitetura séria. Não é port direto do backend atual.

O erro seria assumir que “ter app mobile” implica “rodar o mesmo backend Python
no telefone”. Não implica.

---

## O que o RLM já tem hoje que habilita mobile

O backend atual já expõe quase tudo que um cliente mobile precisa:

```text
POST /webhook/{client_id}
GET  /sessions
GET  /sessions/{id}
GET  /sessions/{id}/events
POST /v1/chat/completions
GET  /webchat
WebSocket de observabilidade
```

Isso significa que **o motor conversacional não precisa ser refeito** para ter
presença mobile. O que falta é a casca cliente, autenticação adequada para
mobile e UX de produto.

Base interna do repositório:

- README já documenta WebChat, OpenAI-compatible API, sessões e WebSocket.
- O WebChat atual já usa SSE e sessão persistente.
- A arquitetura multi-dispositivo já foi pensada em documentos internos.

---

## Caminho A — PWA / Web app instalável

### Veredito

**É o caminho mais barato e mais racional para abrir mobile agora.**

### Por que é viável

- PWAs podem ser instaláveis e se comportar como app em múltiplas plataformas.
- Android oferece integração forte com web apps, incluindo Trusted Web Activity.
- iPhone já suporta web push para web apps e páginas Safari modernas.

### O que já ajuda no RLM

- O RLM já tem WebChat.
- O servidor já tem SSE.
- O frontend atual já é uma single-page servida pelo próprio backend.

### O que faltaria para um PWA sério

| Item | Estado atual | Necessário para mobile sério |
|---|---|---|
| Responsividade real | fraca / básica | layout mobile-first |
| Manifest | ausente | nome, ícones, display standalone |
| Service Worker | ausente | cache, reconexão, offline básico |
| Push | ausente | web push com VAPID + service worker |
| Badge/app shortcuts | ausente | refinamento de UX |

### Limites do PWA

- Não substitui completamente um app nativo para áudio, push avançado, share
  extension, integração profunda com sistema e tarefas longas.
- iOS impõe comportamento mais restritivo que Android para background e UX web.

### Referências externas

- MDN: PWA pode ser instalada, operar offline e integrar com o sistema:
  https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps
- Apple: web push funciona em Safari e web apps no iOS 16.4+:
  https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers
- Android: Android recomenda WebView/Custom Tabs/TWA para cenários web no app:
  https://developer.android.com/guide/webapps
- Android TWA:
  https://developer.android.com/develop/ui/views/layout/webapps/guide-trusted-web-activities-version2

### Conclusão do caminho A

Se a meta é "ter RLM no celular" sem entrar agora em Swift/Kotlin, o caminho
correto é **evoluir o WebChat para PWA**.

---

## Caminho B — App nativo companion

### Veredito

**É o melhor caminho de produto.**

Não precisa portar o engine inteiro. O app pode ser um cliente fino, com:

- chat
- push
- voz
- histórico local
- ações rápidas
- abertura profunda em sessões/eventos
- integração com câmera, share sheet e notificações

### Arquitetura recomendada

```text
iPhone / Android app
    -> HTTPS para API RLM
    -> WebSocket para eventos
    -> push para retomada e notificações
    -> cache local para histórico leve

RLM backend continua no servidor/VPS/desktop
```

### Por que esse caminho é forte

- preserva o engine atual
- evita tentar empacotar REPL/sandbox completo no telefone
- permite UX muito melhor que Telegram
- não força reescrita do núcleo agora

### Capacidades nativas relevantes

#### iPhone

- APNs e UserNotifications suportam notificações locais e remotas.
- Background Tasks permitem atualização e manutenção em background dentro do
  modelo permitido pela plataforma.
- Web push também existe para web app, mas app nativo tem mais controle.

Referências:

- UserNotifications:
  https://developer.apple.com/documentation/usernotifications
- Background Tasks:
  https://developer.apple.com/documentation/backgroundtasks

#### Android

- Notifications têm actions, badges, heads-up e lock screen.
- Foreground services cobrem tarefas perceptíveis ao usuário.
- WebView/Custom Tabs/TWA permitem híbrido se necessário.

Referências:

- Notifications:
  https://developer.android.com/develop/ui/views/notifications
- Foreground services:
  https://developer.android.com/guide/components/foreground-services
- Web apps in Android:
  https://developer.android.com/guide/webapps

### O que um app companion do RLM deveria expor

| Função | iPhone | Android | Backend RLM atual já ajuda? |
|---|---|---|---|
| Chat principal | ✅ | ✅ | sim |
| Streaming de resposta | ✅ | ✅ | sim, via SSE/WS/API |
| Push de alertas | ✅ APNs | ✅ FCM/notification stack | parcialmente |
| Voz | ✅ | ✅ | backend já tem STT/TTS; cliente precisa UX |
| Abertura de sessão específica | ✅ | ✅ | sim, via session/event endpoints |
| Quick actions / shortcuts | ✅ | ✅ | precisa camada cliente |
| Compartilhar para o RLM | ✅ | ✅ | precisa camada cliente |

### Conclusão do caminho B

Se a ambição é ultrapassar Telegram e virar produto real no bolso, este é o
caminho principal.

---

## Caminho C — RLM inteiro on-device

### Veredito

**Não é port direto do backend atual.**

É um projeto novo em cima dos conceitos do RLM, não uma simples embalagem do
repositório Python atual.

### Por que o port direto atual é ruim

O backend atual depende de características que não combinam bem com mobile:

- REPL Python arbitrário
- execução de código
- múltiplos backends de sandbox
- processos de longa duração
- acoplamento com servidor local e fluxos desktop/server

### iPhone: restrição estrutural mais forte

O ponto crítico é oficial: a App Store diz que apps não podem **baixar,
instalar ou executar código** que introduza ou altere funcionalidades do app
(guideline 2.5.2), salvo exceções bem específicas.

Referência:

- App Store Review Guidelines 2.5.2:
  https://developer.apple.com/app-store/review/guidelines/

Além disso, background em iOS é controlado e deve usar modos permitidos pela
plataforma; não existe liberdade irrestrita de daemon pessoal rodando como no
desktop.

Referência:

- Background Tasks:
  https://developer.apple.com/documentation/backgroundtasks

### Consequência prática para iPhone

Um “RLM on-device” no iPhone teria que ser outra arquitetura:

- app em Swift/SwiftUI
- tool calling controlado
- sem REPL Python arbitrário baixando comportamento novo
- modelo local via framework nativo quando fizer sentido
- políticas explícitas de permissão

### Android: mais permissivo, mas ainda exige re-arquitetura

Android abre mais espaço para:

- foreground services
- runtimes locais
- empacotamento híbrido
- AI on-device com Gemini Nano, LiteRT e MediaPipe

Referências:

- Android AI:
  https://developer.android.com/ai
- Google AI Edge / LiteRT / MediaPipe:
  https://ai.google.dev/edge

Mas isso **não** transforma o backend atual em app mobile automaticamente.
Continua sendo necessário separar:

- orquestração
- ferramentas permitidas
- persistência
- runtime de inferência
- política de background

### iPhone on-device futuro

Hoje existe um caminho oficial interessante com Apple Foundation Models:

- modelo on-device
- sessões
- geração estruturada
- tool calling

Referência:

- Apple Foundation Models:
  https://developer.apple.com/documentation/foundationmodels

Isso é relevante porque aproxima parte da filosofia do RLM do ecossistema nativo
da Apple. Mas continua sendo uma **reimplementação nativa da ideia**, não o
backend Python atual rodando intacto.

### Conclusão do caminho C

RLM totalmente local no telefone é uma possibilidade futura, mas só depois de
separar o projeto em camadas portáveis. No iPhone, isso precisa nascer dentro
das regras da plataforma; no Android, precisa nascer dentro do modelo móvel, não
como um daemon desktop espremido no aparelho.

---

## Decisão recomendada

### Curto prazo

**PWA mobile-first sobre o WebChat atual.**

Razão:

- reaproveita backend existente
- abre iPhone e Android com menor custo
- já prepara push web e instalação

### Médio prazo

**App companion nativo.**

Razão:

- push melhor
- voz melhor
- share/inbox/actions
- UX muito superior a Telegram

### Longo prazo

**RLM on-device reimaginado.**

Razão:

- privacidade
- latência
- resiliência offline
- diferenciação de produto

Mas isso exigirá uma linha nova de arquitetura.

---

## O que precisaria mudar no RLM para manter o caminho on-device aberto

Estas decisões ajudam agora mesmo, sem implementar mobile ainda:

1. Separar mais claramente `engine` de `client shell`.
2. Reduzir dependência de REPL arbitrário para fluxos de ferramenta mais
   declarativos onde fizer sentido.
3. Tornar eventos, sessões e artifacts mais estáveis e consumíveis por cliente.
4. Tratar permissões e capacidades por dispositivo/cliente.
5. Evitar acoplamento do frontend com detalhes internos do daemon.

Esses passos melhoram desktop, web e mobile ao mesmo tempo.

---

## Riscos e ilusões a evitar

| Ilusão | Realidade |
|---|---|
| “Telegram prova que mobile já está resolvido” | Telegram resolve transporte, não produto mobile |
| “Basta empacotar Python no celular” | Isso não resolve UX, background, review policy, push nem integração nativa |
| “iPhone e Android são equivalentes” | iPhone é muito mais restritivo para execução dinâmica e comportamento em background |
| “Se houver app mobile, o backend precisa morar no aparelho” | Não. O companion remoto é o caminho mais racional primeiro |

---

## Roadmap sugerido

```text
Fase 1
  - tornar WebChat responsivo e instalável como PWA
  - adicionar manifest + service worker + push web

Fase 2
  - estabilizar contrato de eventos/sessões para cliente mobile
  - adicionar autenticação por dispositivo

Fase 3
  - criar app companion nativo
  - push, voz, share sheet, quick actions

Fase 4
  - estudar variante on-device
  - Apple Foundation Models no iPhone
  - Gemini Nano / LiteRT / MediaPipe no Android
```

---

## Conclusão final

Sim, o RLM pode chegar direto ao celular.

Mas a estratégia correta é:

- **primeiro**: web app instalável
- **depois**: app companion nativo
- **só então**: variante on-device reescrita para mobile

Se tentar pular direto para “rodar o backend Python inteiro no iPhone”, a chance
de desperdício é alta. Se construir a presença mobile em camadas, o caminho fica
aberto sem comprometer o motor atual.