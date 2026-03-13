# RLM + VS Code — Documentação de Integração

**Status:** Plano arquitetural completo. Implementação não iniciada.  
**Última atualização:** Março 2026  
**Base:** análise do repositório `vscode-main` (VS Code OSS, MIT License)

---

## 1. Visão Geral

O RLM é o **servidor de inteligência** (Python). O VS Code é o **ambiente de execução** (UI, terminal, filesystem). Uma extensão TypeScript fina é o **conector** entre os dois.

```
┌─────────────────────────────────────────────────────┐
│                    VS Code (instalado)               │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │          rlm-extension (TypeScript ~300L)     │   │
│  │  - Registra RLM como remoteCodingAgent        │   │
│  │  - Exibe SessionRecord's como sessões na UI   │   │
│  │  - Inicia McpGateway → endereço HTTP local    │   │
│  │  - Passa endereço ao api.py via env var        │   │
│  └──────────────┬───────────────────────────────┘   │
│                 │ HTTP / MCP Protocol                │
└─────────────────┼───────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────┐
│                RLM Server (Python)                   │
│                                                     │
│  api.py           → FastAPI, endpoints HTTP          │
│  core/session.py  → SessionManager, SessionRecord    │
│  session.py       → RLMSession (contexto, compação)  │
│  plugins/mcp.py   → MCPServerNamespace (já existe)   │
│  core/mcp_client  → SyncMCPClient (já existe)        │
│                                                     │
│  sub_rlm_async    → paralelismo de subagentes        │
│  SiblingBus       → comunicação P2P entre agentes    │
│  check_cancel()   → cancelamento propagado           │
└─────────────────────────────────────────────────────┘
```

---

## 2. O que o VS Code fornece (sem modificar nada)

### 2.1 Ferramentas Builtin (`builtinTools/`)

Todas registradas em `BuiltinToolsContribution` (`tools.ts`). Implementações reais no VS Code instalado.

| ID da Ferramenta | Arquivo Fonte | Parâmetros | O que faz |
|---|---|---|---|
| `vscode_editFile` | `editFileTool.ts` | `{uri, explanation, code}` | Aplica edição com diff visual animado, usuário pode aceitar/rejeitar |
| `runSubagent` | `runSubagentTool.ts` | `{prompt, description, agentName?}` | Lança subagente com contexto completo do VS Code |
| `manage_todo_list` | `manageTodoListTool.ts` | `{todoList: [{id,title,status}]}` | Todo list persistido, visível na UI do chat |
| `vscode_askQuestions` | `askQuestionsTool.ts` | `{questions:[{id,type,title,...}]}` | Pausa e pede input ao usuário com formulário inline |
| `confirmation` | `confirmationTool.ts` | `{message, buttons}` | Confirmação antes de ação destrutiva |
| `task_complete` | `taskCompleteTool.ts` | `{result}` | Sinaliza conclusão de tarefa ao VS Code |

#### Schema completo do `manage_todo_list` (extraído de `manageTodoListTool.ts`):
```json
{
  "type": "object",
  "properties": {
    "todoList": {
      "type": "array",
      "description": "Complete array of all todo items. Must include ALL items - both existing and new.",
      "items": {
        "type": "object",
        "properties": {
          "id":     { "type": "number", "description": "Unique identifier. Use sequential numbers starting from 1." },
          "title":  { "type": "string", "description": "Concise action-oriented label (3-7 words). Displayed in UI." },
          "status": { "type": "string", "enum": ["not-started", "in-progress", "completed"],
                      "description": "not-started: Not begun | in-progress: Currently working (max 1) | completed: Fully finished" }
        },
        "required": ["id", "title", "status"]
      }
    }
  },
  "required": ["todoList"]
}
```

#### Schema do `runSubagent` (extraído de `runSubagentTool.ts`):
```json
{
  "type": "object",
  "properties": {
    "prompt":       { "type": "string", "description": "Detailed task description for the agent." },
    "description":  { "type": "string", "description": "3-5 word description of the task." },
    "agentName":    { "type": "string", "description": "Optional name of specific agent to invoke." }
  },
  "required": ["prompt", "description"]
}
```

#### Descrição de prompt do `runSubagent` (texto exato, LLM já treinado com isso):
```
Launch a new agent to handle complex, multi-step tasks autonomously. This tool is good
at researching complex questions, searching for code, and executing multi-step tasks.
When you are searching for a keyword or file and are not confident that you will find
the right match in the first few tries, use this agent to perform the search for you.

- Agents do not run async or in the background, you will wait for the agent's result.
- When the agent is done, it will return a single message back to you. The result
  returned by the agent is not visible to the user.
- Each agent invocation is stateless.
- The agent's outputs should generally be trusted.
- Clearly tell the agent whether you expect it to write code or just to do research.
```

**Importância:** GPT-4o e Claude foram treinados com esse texto exato. Reutilizar na descrição de ferramentas MCP do RLM aumenta a taxa de invocação correta sem fine-tuning.

---

### 2.2 `McpGateway` — A Ponte Central

Definido em `vscode.proposed.mcpServerDefinitions.d.ts`:

```typescript
// VS Code expõe este método:
export function startMcpGateway(): Thenable<McpGateway | undefined>;

export interface McpGateway extends Disposable {
    readonly address: Uri;  // ex: "http://localhost:6123"
}
```

**O que faz:** O VS Code abre um servidor HTTP local que expõe **todos os seus MCP servers** via protocolo MCP padrão. Qualquer processo externo que saiba o endereço pode:
- Listar ferramentas: `GET /mcp/tools`
- Invocar ferramenta: `POST /mcp/tool/call`

O Python do RLM acessa isso via `SyncMCPClient` existente — sem nova infraestrutura.

---

### 2.3 `remoteCodingAgents` — Registro no Chat do VS Code

Definido em `remoteCodingAgentsService.ts`:

```typescript
interface IRemoteCodingAgent {
    id: string;           // "rlm-agent"
    command: string;      // "rlm.startSession" (comando VS Code registrado)
    displayName: string;  // "RLM Agent"
    description?: string; // Mostrado no tooltip
    followUpRegex?: string; // Pattern para follow-up automático
    when?: string;        // ContextKey condition
}
```

Registrar o RLM aqui faz ele aparecer no menu do Copilot Chat como opção de agente.

---

### 2.4 `ChatSessionItemController` — Sessões Visíveis na UI

Definido em `vscode.proposed.chatSessionsProvider.d.ts`:

```typescript
enum ChatSessionStatus {
    Failed     = 0,
    Completed  = 1,
    InProgress = 2,
    NeedsInput = 3   // Equivale a RLMSession aguardando input humano
}

// Mapeia diretamente para SessionRecord do RLM:
interface ChatSessionItem {
    id: string;       // SessionRecord.session_id
    label: string;    // SessionRecord.client_id
    status: ChatSessionStatus; // SessionRecord.status → mapeado
}
```

---

### 2.5 `ChatHooks` — Ciclo de Vida Interceptável

Definido em `vscode.proposed.chatHooks.d.ts`:

```typescript
type ChatHookType =
  | 'SessionStart'     // → RLMSession.__init__
  | 'SessionEnd'       // → SessionManager.close_session()
  | 'UserPromptSubmit' // → antes de RLMSession.chat()
  | 'PreToolUse'       // → antes de sub_rlm_async
  | 'PostToolUse'      // → depois de sub_rlm_async
  | 'PreCompact'       // → _compact_background_if_needed()
  | 'SubagentStart'    // → make_sub_rlm_async_fn() invocação
  | 'SubagentStop'     // → AsyncHandle.is_done = True
  | 'Stop'             // → check_cancel() retorna True
  | 'ErrorOccurred';   // → RLMSupervisor detectou error_loop
```

Cada hook executa um **comando de shell** configurável. Permite auditoria, logging externo, ou intervenção humana em qualquer fase da execução do RLM.

---

### 2.6 `agentSessionsWorkspace`

```typescript
// Quando true, a janela inteira é dedicada a gerenciar sessões de agentes
export const isAgentSessionsWorkspace: boolean;
```

Permite abrir uma janela VS Code separada que mostra N sessões RLM simultâneas em paralelo — a visão de "múltiplas aplicações simultâneas com o mesmo contexto".

---

## 3. Infraestrutura RLM que já existe (sem mudar)

### 3.1 `plugins/mcp.py` — Cliente MCP em Python

```python
# Já implementado e funcional:
class SyncMCPClient:
    """Wrapper síncrono sobre o SDK MCP Anthropic.
    Roda asyncio em background thread."""
    def connect(self, timeout=15.0): ...
    def list_tools(self) -> list[dict]: ...
    def call_tool(self, name: str, params: dict) -> Any: ...

class MCPServerNamespace:
    """Expõe ferramentas MCP como métodos Python:
    vscode.vscode_editFile(uri=..., code=..., explanation=...)"""
    def _setup_tools(self): ...  # auto-gera métodos a partir de list_tools()

def load_server(server_name, command, args) -> MCPServerNamespace:
    """Conecta a um MCP server e retorna namespace com métodos."""
```

**Para conectar ao VS Code McpGateway:** basta passar o endereço HTTP em vez de comando stdio:

```python
# Atual (stdio):
sqlite = load_server("sqlite", "npx.cmd", ["-y", "@mcp/server-sqlite"])

# Para VS Code gateway (HTTP — precisa de load_http_server(), ainda não existe):
vscode = load_http_server("vscode", "http://localhost:6123")
vscode.vscode_editFile(uri="file:///main.py", code="...", explanation="...")
```

### 3.2 `core/mcp_client.py` — Implementação Atual (stdio only)

Usa `mcp.client.stdio.stdio_client` — conecta a servidores MCP via stdin/stdout de processo filho. **Não suporta HTTP ainda.** Precisa de extensão para suportar `mcp.client.sse` (HTTP/SSE transport).

### 3.3 `server/api.py` — Endpoints HTTP Existentes

```
POST   /webhook/{client_id}   → processa prompt, retorna ExecutionResult
GET    /sessions              → lista SessionRecord's (para UI do VS Code)
GET    /sessions/{id}         → detalhes de sessão
DELETE /sessions/{id}         → aborta execução (supervisor.abort())
GET    /sessions/{id}/events  → log de eventos
GET    /health                → health check
```

Esses endpoints já existem e são o que a extensão TS chamará.

### 3.4 `core/session.py` — Pool Multicliente

```
SessionManager.get_or_create("vscode:workspace1")  → SessionRecord
SessionRecord.session_id      → ID único
SessionRecord.client_id       → "vscode:workspace1"
SessionRecord.status          → idle|running|completed|aborted|error
SessionRecord.rlm_instance    → RLMSession (contexto conversacional)
```

### 3.5 `session.py` — Sessão Conversacional

```
RLMSession.chat(msg)            → completa com contexto histórico
RLMSession.chat_async(msg)      → SessionAsyncHandle (não-bloqueante)
RLMSession._build_prompt()      → injeta resumo compactado + histórico quente
RLMSession._compact_background  → compactação em daemon thread
```

---

## 4. O que precisa ser construído

### Fase 1 — `load_http_server()` em `plugins/mcp.py`

Estender `SyncMCPClient` para suportar transporte HTTP/SSE (protocolo MCP sobre HTTP):

```python
# Em core/mcp_client.py — adicionar MCPHttpClient:
from mcp.client.sse import sse_client  # já existe no SDK mcp

class SyncMCPHttpClient:
    """Como SyncMCPClient mas conecta via HTTP em vez de stdio."""
    def __init__(self, url: str, headers: dict = None): ...
    def connect(self): ...
    def list_tools(self): ...
    def call_tool(self, name, params): ...
```

```python
# Em plugins/mcp.py — adicionar:
def load_http_server(name: str, url: str, headers: dict = None) -> MCPServerNamespace:
    """Conecta ao McpGateway do VS Code e retorna namespace de ferramentas."""
    client = SyncMCPHttpClient(url, headers)
    client.connect()
    namespace = MCPServerNamespace(client, name)
    _active_clients[name] = namespace
    return namespace
```

### Fase 2 — Endpoint em `api.py` para receber endereço do Gateway

```python
# Novo endpoint em api.py:
@app.post("/vscode/gateway")
async def register_vscode_gateway(body: {"gateway_url": str}):
    """Recebe o endereço do McpGateway do VS Code e conecta as ferramentas."""
    url = body["gateway_url"]
    os.environ["VSCODE_MCP_GATEWAY"] = url
    # Carrega ferramentas do VS Code no REPL global
    vscode_ns = load_http_server("vscode", url)
    app.state.vscode_tools = vscode_ns
    return {"status": "connected", "tools": vscode_ns.list_tools()}
```

### Fase 3 — Extensão TypeScript `rlm-extension`

**Estrutura do projeto:**
```
rlm-extension/
    package.json          ← manifest da extensão
    src/
        extension.ts      ← activate(), inicia gateway, conecta ao api.py
        session-provider.ts ← ChatSessionItemController → chama /sessions
        hooks.ts          ← SubagentStart/Stop → log events
    types/
        chatSessionsProvider.d.ts  ← copiado de vscode-main
        mcpServerDefinitions.d.ts  ← copiado de vscode-main
        remoteCodingAgents.d.ts    ← copiado de vscode-main
```

**`package.json` mínimo:**
```json
{
  "name": "rlm-agent",
  "displayName": "RLM Agent",
  "version": "0.1.0",
  "engines": { "vscode": "^1.112.0" },
  "enabledApiProposals": [
    "remoteCodingAgents",
    "chatSessionsProvider",
    "mcpServerDefinitions",
    "agentSessionsWorkspace"
  ],
  "contributes": {
    "remoteCodingAgents": [{
      "id": "rlm-agent",
      "command": "rlm.startSession",
      "displayName": "RLM Agent",
      "description": "Agente recursivo local com contexto persistente",
      "followUpRegex": "rlm|agente|analyze|analisar"
    }],
    "commands": [{
      "command": "rlm.startSession",
      "title": "Iniciar RLM Session"
    }]
  }
}
```

**`extension.ts` mínimo:**
```typescript
import * as vscode from 'vscode';

const RLM_API = process.env.RLM_API_URL ?? 'http://localhost:8000';

export async function activate(ctx: vscode.ExtensionContext) {
    // 1. Inicia o McpGateway do VS Code
    const gateway = await (vscode.lm as any).startMcpGateway();
    if (gateway) {
        // 2. Registra o endereço no servidor RLM
        await fetch(`${RLM_API}/vscode/gateway`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gateway_url: gateway.address.toString() })
        });
        ctx.subscriptions.push(gateway);
    }

    // 3. Registra o comando para abrir sessão RLM
    ctx.subscriptions.push(
        vscode.commands.registerCommand('rlm.startSession', async () => {
            const res = await fetch(`${RLM_API}/webhook/vscode:default`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: 'start' })
            });
            const data = await res.json();
            vscode.window.showInformationMessage(`RLM Session: ${data.session_id}`);
        })
    );
}

export function deactivate() {}
```

---

## 5. Fluxo completo em produção

### Cenário: RLM corrige um bug no arquivo aberto

```
1. Usuário escreve no chat do VS Code:
   "@rlm-agent corrija o bug no arquivo atual"

2. VS Code invoca comando "rlm.startSession"
   → extension.ts chama POST /webhook/vscode:user123
   → payload inclui: arquivo aberto, seleção, diagnósticos

3. api.py → SessionManager.get_or_create("vscode:user123")
   → RLMSession.chat("corrija o bug no arquivo atual")
   → RLM processa, decide chamar vscode_editFile

4. RLM (via plugins/mcp.py) chama vscode_editFile:
   → SyncMCPHttpClient.call_tool("vscode_editFile", {
         uri: "file:///projeto/main.py",
         explanation: "Corrige NullPointerException na linha 42",
         code: "def process(data):\n    if data is None:\n        return []\n    ..."
     })
   → HTTP para gateway.address (VS Code McpGateway)

5. VS Code aplica o diff:
   → Animação visual na tela
   → Usuário vê botões Aceitar / Rejeitar
   → Retorna {"accepted": true}

6. RLM recebe confirmação, continua execução
   → Chama manage_todo_list para marcar tarefa como completada
   → Retorna resposta final ao usuário no chat
```

### Cenário: Múltiplos subagentes paralelos

```
1. RLM decide tarefa precisa de 3 análises paralelas
   → sub_rlm_async("analisa_performance", ...)  → AsyncHandle1
   → sub_rlm_async("analisa_segurança", ...)    → AsyncHandle2
   → sub_rlm_async("analisa_cobertura", ...)    → AsyncHandle3

2. Cada subagente chama vscode_editFile, runInTerminal via McpGateway
   → VS Code mostra 3 streams paralelos no painel de chat
   → SiblingBus permite subagentes comunicarem entre si

3. Pai coleta resultados via AsyncHandle.result()
   → Compila relatório final, chama manage_todo_list
```

---

## 6. Mapa de equivalências RLM ↔ VS Code

| Conceito RLM | Equivalente VS Code |
|---|---|
| `SessionRecord` | `ChatSessionItem` |
| `SessionRecord.status` | `ChatSessionStatus` |
| `SessionManager.get_or_create()` | `ChatSessionItemController.items` |
| `sub_rlm_async()` | `RunSubagentTool` |
| `AsyncHandle.cancel()` → `check_cancel()` | `ChatHookType.Stop` |
| `_compact_background_if_needed()` | `ChatHookType.PreCompact` |
| `SiblingBus.publish()` | (sem equivalente direto no VS Code) |
| `RLMSupervisor.abort()` | `ChatSessionStatus.Failed` |
| `SessionRecord.status = NeedsInput` | `ChatSessionStatus.NeedsInput` |
| `AskQuestionsTool` do VS Code | (não implementado no RLM ainda) |

---

## 7. Arquivos a extrair do `vscode-main` antes de descartar

Copiar para `rlm-extension/types/`:

```
src/vscode-dts/vscode.proposed.chatSessionsProvider.d.ts
src/vscode-dts/vscode.proposed.chatHooks.d.ts
src/vscode-dts/vscode.proposed.mcpServerDefinitions.d.ts
src/vscode-dts/vscode.proposed.mcpToolDefinitions.d.ts
src/vscode-dts/vscode.proposed.remoteCodingAgents.d.ts
src/vscode-dts/vscode.proposed.agentSessionsWorkspace.d.ts
```

Textos de prompt a preservar (copiar para `docs/`):
```
src/vs/workbench/contrib/chat/common/tools/builtinTools/runSubagentTool.ts
  → linha 47-56: BaseModelDescription (texto exato de treinamento do LLM)

src/vs/workbench/contrib/chat/common/tools/builtinTools/askQuestionsTool.ts
  → linha 32: AUTOPILOT_ASK_USER_RESPONSE

src/vs/workbench/contrib/chat/common/tools/builtinTools/manageTodoListTool.ts
  → linhas 34-63: schema JSON completo do todo list
```

---

## 8. O que NÃO fazer

| Ação | Motivo para não fazer |
|---|---|
| Forkar `vscode-main` e editar | 50k+ linhas, conflicts mensais com updates da MS |
| Compilar o VS Code do zero | 2-4h de build, resultado é um fork sem identidade |
| Reimplementar `editFile`, terminal, diff | VS Code já tem, funciona, tem UX polida |
| Manter remote origin do vscode-main | Updates constantes geram conflitos sem benefício |
| Migrar RLM de Python para TypeScript | Perderia torch, transformers, todo ecossistema ML |

---

## 9. Sequência de implementação

```
[ ] Fase 1: MCPHttpClient em core/mcp_client.py
[ ] Fase 2: load_http_server() em plugins/mcp.py
[ ] Fase 3: POST /vscode/gateway em api.py
[ ] Fase 4: Copiar .d.ts para rlm-extension/types/
[ ] Fase 5: package.json da extensão
[ ] Fase 6: extension.ts (activate + gateway + comando)
[ ] Fase 7: session-provider.ts (ChatSessionItemController)
[ ] Fase 8: Teste manual end-to-end
[ ] Fase 9: Empacotar .vsix e instalar
```
