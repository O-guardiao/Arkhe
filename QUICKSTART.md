# RLM — Guia de Uso Pessoal (Windows)

Guia passo-a-passo para configurar o RLM como assistente pessoal no Windows.

---

## Pré-requisitos

- **Python 3.11+** instalado
- **Chave de API** de pelo menos um provedor LLM (OpenAI, Anthropic, ou Google)

---

## 1. Instalação

```powershell
# Clone ou navegue até o diretório do projeto
cd C:\caminho\para\rlm-main

# Instale em modo editável
pip install -e .

# Verifique a instalação
rlm version
# ou
python -m rlm version
```

---

## 2. Configuração Inicial

### Opção A: Wizard interativo (recomendado)

```powershell
rlm setup
```

O wizard pergunta:
1. Sua chave de API (OpenAI, Anthropic, etc.)
2. Modelo padrão (gpt-4o-mini é o mais barato)
3. Endereços do servidor (aceite os padrões)
4. Tokens de segurança (gerados automaticamente)

Resultado: arquivo `.env` criado na raiz do projeto.

### Opção B: Configuração manual

```powershell
# Copie o template
copy .env.example .env

# Edite com seu editor preferido
notepad .env
```

Preencha no mínimo:
```env
OPENAI_API_KEY=sk-sua-chave-aqui
RLM_MODEL=gpt-4o-mini
```

### Diagnóstico

```powershell
rlm doctor
```

Verifica tudo de uma vez: .env, API key, tokens, servidor, canais.

---

## 3. Iniciar o Servidor

### Modo foreground (logs ao vivo)

```powershell
rlm start --foreground
```

Você verá os logs no terminal. Interrompa com `Ctrl+C`.

### Modo background

```powershell
rlm start
```

O servidor roda em background. PIDs salvos em `~/.rlm/run/`.

### Verificar status

```powershell
rlm status
```

Mostra:
- PID dos processos (API e WebSocket)
- Se estão ativos ou parados
- URLs dos endpoints

### Parar

```powershell
rlm stop
```

---

## 4. Usar o WebChat

Após `rlm start`, abra no navegador:

```
http://localhost:5000/webchat
```

Funcionalidades:
- Chat com streaming em tempo real
- Markdown renderizado (código, listas, títulos)
- Sessão persistente (sobrevive a refresh)
- Enter = enviar, Shift+Enter = nova linha

---

## 5. Usar como Biblioteca Python

```python
from rlm import RLM

rlm = RLM(
    backend="openai",
    backend_kwargs={"model_name": "gpt-4o-mini"},
    verbose=True,
)

# Completion simples
result = rlm.completion("Resuma as notícias de hoje")
print(result.response)
```

### Com sessão persistente e memória

```python
from rlm.session import RLMSession

session = RLMSession(
    backend="openai",
    backend_kwargs={"model_name": "gpt-4o-mini"},
    memory_db_path="minha_memoria.db",
)

# O agente lembra de conversas anteriores
response = session.chat("O que decidimos ontem sobre o projeto?")
print(response)
```

---

## 6. Conectar Telegram

1. Crie um bot no Telegram via [@BotFather](https://t.me/BotFather)
2. Copie o token do bot
3. Adicione ao `.env`:

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
```

4. Reinicie o servidor:

```powershell
rlm stop
rlm start
```

5. Converse com seu bot no Telegram — o RLM responde.

Para restringir acesso apenas ao seu chat:
```env
RLM_ALLOWED_CHATS=seu_chat_id_aqui
```

---

## 7. Conectar Discord

1. Crie um app no [Discord Developer Portal](https://discord.com/developers/)
2. Copie a Public Key e App ID
3. Adicione ao `.env`:

```env
DISCORD_APP_PUBLIC_KEY=sua_public_key
DISCORD_APP_ID=seu_app_id
```

4. Configure o Interactions Endpoint URL no portal do Discord:
```
https://seu-dominio.com/discord/interactions
```

---

## 8. API OpenAI-Compatible

O RLM expõe um endpoint compatível com a API da OpenAI:

```env
RLM_API_TOKEN=meu-token-secreto
```

Qualquer app que use a OpenAI SDK pode ser apontada para o RLM:

```python
import openai

client = openai.OpenAI(
    api_key="meu-token-secreto",
    base_url="http://localhost:5000/v1",
)

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Olá!"}],
)
print(response.choices[0].message.content)
```

---

## 9. Scheduler (Tarefas Agendadas)

O scheduler permite agendar tarefas que o RLM executa automaticamente.

### Via API

```bash
curl -X POST http://localhost:5000/scheduler/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "resumo_diario",
    "client_id": "meu_usuario",
    "prompt": "Pesquise as principais notícias de tecnologia de hoje e me envie um resumo",
    "trigger_type": "cron",
    "trigger_value": "0 9 * * 1-5"
  }'
```

### Tipos de trigger

| Tipo | Formato | Exemplo |
|---|---|---|
| `cron` | Expressão cron | `0 9 * * 1-5` (9h, seg-sex) |
| `once` | ISO 8601 | `2026-03-15T10:00:00` |
| `interval` | Segundos | `3600` (a cada hora) |

---

## 10. Skills Disponíveis

O RLM descobre automaticamente qual skill usar baseado na sua mensagem:

| Você diz... | Skill ativada |
|---|---|
| "Pesquisa sobre X" | `web_search` |
| "Lê o arquivo README.md" | `filesystem` |
| "Roda `git status`" | `shell` |
| "Como está o tempo?" | `weather` |
| "O que decidimos ontem?" | `memory` |
| "Navega até site.com" | `browser` |
| "Envia email para..." | `email` |
| "Agenda reunião..." | `calendar` |

### Listar skills instaladas

```powershell
rlm skill list
```

### Instalar skill externa

```powershell
rlm skill install github:usuario/repositorio
```

---

## 11. Manutenção

### Rotacionar tokens de segurança

```powershell
rlm token rotate
rlm stop && rlm start
```

### Updates

```powershell
cd C:\caminho\para\rlm-main
git pull
pip install -e .
rlm stop && rlm start
```

### Logs

Os logs ficam em `~/.rlm/logs/`:
- `api.log` — servidor FastAPI
- `ws.log` — servidor WebSocket

```powershell
# Ver logs ao vivo
Get-Content $HOME\.rlm\logs\api.log -Wait
```

### Backup da memória

```powershell
# O banco de memória fica na raiz do projeto
copy rlm_memory_v2.db rlm_memory_v2.backup.db

# O banco de sessões
copy rlm_sessions.db rlm_sessions.backup.db

# O banco do scheduler
copy $HOME\.rlm\scheduler.db scheduler.backup.db
```

---

## 12. Estrutura de Arquivos

```
rlm-main/
├── .env                    ← Configuração (gerado por rlm setup)
├── .env.example            ← Template de referência
├── rlm_sessions.db         ← Sessões (gerado em runtime)
├── rlm_memory_v2.db        ← Memória persistente (gerado em runtime)
├── rlm_states/             ← Estados de sessão (gerado em runtime)
├── rlm/
│   ├── __main__.py         ← Entry point (python -m rlm)
│   ├── __init__.py         ← Exporta classe RLM
│   ├── session.py          ← RLMSession (wrapper conversacional + memória)
│   ├── cli/
│   │   ├── main.py         ← CLI (rlm setup/start/stop/doctor/...)
│   │   ├── wizard.py       ← Wizard interativo de configuração
│   │   └── service.py      ← Gerenciamento de processos e daemons
│   ├── core/
│   │   ├── rlm.py          ← Engine recursivo principal
│   │   ├── session.py      ← SessionManager (pool SQLite)
│   │   ├── memory_manager.py ← MultiVectorMemory (FTS5 + vetores)
│   │   ├── supervisor.py   ← Timeout, abort, error detection
│   │   ├── skill_loader.py ← Discovery e routing de skills
│   │   ├── scheduler.py    ← Agendamento de tarefas
│   │   ├── hooks.py        ← Sistema de hooks
│   │   ├── auth.py         ← Autenticação JWT
│   │   └── security.py     ← Sanitização e validação
│   ├── server/
│   │   ├── api.py          ← FastAPI principal
│   │   ├── webchat.py      ← Chat web (SSE)
│   │   ├── ws_server.py    ← WebSocket de observabilidade
│   │   ├── telegram_gateway.py
│   │   ├── discord_gateway.py
│   │   ├── whatsapp_gateway.py
│   │   ├── slack_gateway.py
│   │   ├── scheduler.py    ← Scheduler standalone
│   │   ├── openai_compat.py ← API OpenAI-compatible
│   │   ├── webhook_dispatch.py
│   │   └── event_router.py
│   ├── skills/             ← 19 skills com SKILL.md
│   ├── plugins/            ← Plugins de canal e ferramentas
│   ├── clients/            ← Clientes LLM (OpenAI, Anthropic, etc.)
│   ├── environments/       ← REPL sandboxes
│   ├── tools/              ← Tools do REPL (memória, embeddings, etc.)
│   ├── static/
│   │   └── webchat.html    ← Interface web
│   └── utils/              ← Parsing, prompts, etc.
├── tests/                  ← 1070+ testes
├── examples/               ← Exemplos de uso
└── Makefile                ← Shortcuts de CLI
```

---

## Troubleshooting

| Problema | Solução |
|---|---|
| `rlm: command not found` | Execute `pip install -e .` no diretório do projeto |
| `python -m rlm` não funciona | Verifique se `rlm/__main__.py` existe |
| Servidor não carrega `.env` | Certifique-se que o `.env` está na raiz do projeto |
| API key inválida | Execute `rlm doctor` para testar a conexão |
| WebChat offline | Verifique `rlm status` e se a porta 5000 está livre |
| Telegram não responde | Verifique `TELEGRAM_BOT_TOKEN` no `.env` e reinicie |
| Memória não funciona | O `rlm_memory_v2.db` é criado automaticamente no primeiro uso |
| Testes travando | Exclua `test_live_llm.py` (faz chamadas reais à API) |
