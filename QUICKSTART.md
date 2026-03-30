# Arkhe — QuickStart Seguro

Guia curto para colocar o Arkhe no ar sem repetir os erros mais comuns de deploy local: modelo único caro, portas expostas sem controle e daemon brigando com start manual.

---

## Objetivo

Este guia serve para:

- subir o Arkhe localmente ou em VPS com o setup wizard atual
- configurar roteamento de modelos por papel
- manter API, WebSocket e rotas administrativas protegidos desde o início
- verificar operação sem abrir a máquina inteira na internet

Se você quer só instalar e experimentar, use o wizard. Se você quer expor o serviço para fora da máquina, leia a seção de segurança antes.

---

## Requisitos

- Python 3.11 ou superior
- uma chave de API de LLM: OpenAI, Anthropic ou Google
- `uv` recomendado; `pip` continua suportado

---

## Instalação

### Linux, macOS e WSL

```bash
curl -fsSL https://raw.githubusercontent.com/O-guardiao/Arkhe/main/install.sh | bash
```

Esse fluxo:

- instala `uv` se necessário
- clona ou atualiza o checkout em `~/.arkhe/repo`
- roda `uv sync`
- cria os wrappers `arkhe` e `rlm`
- entra no `arkhe setup` quando existe TTY interativo
- gera bootstrap seguro com `.env` local quando não existe TTY

### Windows

```powershell
cd C:\caminho\para\arkhe
pip install -e .
arkhe version
```

Se preferir `uv` no Windows:

```powershell
uv venv
uv pip install -e .
uv run arkhe version
```

---

## Setup Inicial

O caminho recomendado é o wizard:

```bash
arkhe setup
```

Alias legado:

```bash
rlm setup
```

O wizard atual já cobre o que o QuickStart antigo não cobria mais:

1. escolha do provedor LLM
2. escolha do modelo base
3. estratégia de modelos
4. bind e portas da API/WebSocket
5. geração de tokens de segurança
6. instalação de serviço com `systemd` ou `launchd`, quando disponível

Ao final, o arquivo `.env` é gravado na raiz real do projeto. Em instalação típica via one-liner em Linux, isso significa `~/.arkhe/repo/.env`.

---

## Estratégia de Modelos

O Arkhe já não precisa mais operar só com um modelo monolítico. O wizard suporta três modos:

1. `Um único modelo`
   Use se você quer simplicidade máxima e aceita custo maior por chamada.

2. `Split recomendado`
   Preenche automaticamente os papéis de execução.

3. `Escolher por papel`
   Configura manualmente planner, worker, evaluator, fast e minirepl.

Configuração recomendada para OpenAI:

```env
OPENAI_API_KEY=sk-...
RLM_MODEL=gpt-5.4-mini
RLM_MODEL_PLANNER=gpt-5.4
RLM_MODEL_WORKER=gpt-5.4-mini
RLM_MODEL_EVALUATOR=gpt-5.4-mini
RLM_MODEL_FAST=gpt-5.4-nano
RLM_MODEL_MINIREPL=gpt-5-nano
```

Leitura prática:

- `RLM_MODEL_PLANNER`: decide estratégia e orquestração raiz
- `RLM_MODEL_WORKER`: faz trabalho delegado e subagentes
- `RLM_MODEL_EVALUATOR`: crítica e validação
- `RLM_MODEL_FAST`: verificações simples e respostas operacionais curtas
- `RLM_MODEL_MINIREPL`: classificação barata e loops leves

Se você voltar para modo de modelo único pelo wizard, os overrides `RLM_MODEL_*` são removidos do `.env` automaticamente.

---

## Perfil Seguro Recomendado

Defaults seguros para quase todo cenário:

```env
RLM_API_HOST=127.0.0.1
RLM_API_PORT=5000
RLM_WS_HOST=127.0.0.1
RLM_WS_PORT=8765
```

Não faça isso no começo:

- não use `0.0.0.0` sem reverse proxy, TLS e autenticação bem definida
- não comite `.env`
- não reutilize o mesmo token para tudo se o servidor vai sair da máquina local
- não rode `arkhe start` manualmente se o daemon já foi instalado e iniciado pelo wizard

Tokens que devem existir em produção:

```env
RLM_WS_TOKEN=...
RLM_INTERNAL_TOKEN=...
RLM_ADMIN_TOKEN=...
RLM_HOOK_TOKEN=...
RLM_API_TOKEN=...
```

Separação correta:

- `RLM_WS_TOKEN`: autenticação do WebSocket
- `RLM_INTERNAL_TOKEN`: chamadas internas para `/webhook/{client_id}`
- `RLM_ADMIN_TOKEN`: health, sessões, scheduler, hooks e telemetria administrativa
- `RLM_HOOK_TOKEN`: integrações externas em `/api/hooks/...`
- `RLM_API_TOKEN`: endpoint OpenAI-compatible em `/v1/chat/completions`

Se você expôs uma chave ou token em terminal, chat, issue ou screenshot, trate como comprometido e rotacione.

Para ambientes mais sensíveis, habilite aprovação antes de execução:

```env
RLM_EXEC_APPROVAL_REQUIRED=true
# RLM_EXEC_APPROVAL_TIMEOUT=60
```

---

## Subindo o Serviço

### Se o wizard instalou daemon

Linux:

```bash
systemctl --user status rlm
systemctl --user restart rlm
```

macOS:

```bash
launchctl list | grep rlm
```

Nessa situação, prefira gerenciar o processo pelo serviço. Não dispare `arkhe start` por cima do daemon, ou você pode provocar disputa pela porta 5000.

### Se você está rodando manualmente

```bash
arkhe start --foreground
```

Ou em background:

```bash
arkhe start
arkhe status
```

Para parar:

```bash
arkhe stop
```

---

## Validação Rápida

Depois do setup:

```bash
arkhe doctor
arkhe status
```

O que validar:

- `.env` foi carregado
- chave LLM está presente
- tokens de segurança existem
- API está respondendo
- WebSocket está respondendo
- canais configurados não estão quebrados

---

## WebChat e API

Com a API no ar, abra:

```text
http://127.0.0.1:5000/webchat
```

Se você estiver atrás de proxy, publique o webchat só com autenticação e TLS.

API OpenAI-compatible:

```python
import openai

client = openai.OpenAI(
    api_key="seu-rlm-api-token",
    base_url="http://127.0.0.1:5000/v1",
)

response = client.chat.completions.create(
    model="gpt-5.4-mini",
    messages=[{"role": "user", "content": "Olá"}],
)
print(response.choices[0].message.content)
```

Esse endpoint exige `RLM_API_TOKEN`.

---

## Uso como Biblioteca Python

```python
from rlm import RLM

rlm = RLM(
    backend="openai",
    backend_kwargs={"model_name": "gpt-5.4-mini"},
    verbose=True,
)

result = rlm.completion("Resuma as mudanças do dia")
print(result.response)
```

Com sessão persistente:

```python
from rlm.session import RLMSession

session = RLMSession(
    backend="openai",
    backend_kwargs={"model_name": "gpt-5.4-mini"},
    memory_db_path="rlm_memory_v2.db",
)

print(session.chat("O que ficou pendente ontem?").response)
```

---

## Canais Externos

### Telegram

```env
TELEGRAM_BOT_TOKEN=123456:ABC...
RLM_ALLOWED_CHATS=123456789
```

Recomendação mínima:

- sempre preencha `RLM_ALLOWED_CHATS` em uso pessoal ou administrativo
- não deixe bot responder a qualquer chat se ele puder executar ações operacionais

Depois de editar o `.env`, reinicie o serviço.

### Discord, WhatsApp e Slack

Configure só depois de o `arkhe doctor` local estar limpo. Primeiro estabilize o core, depois abra superfície externa.

---

## Atualização

Checkout git:

```bash
git pull
pip install -e .
```

Se usa serviço:

```bash
systemctl --user restart rlm
```

Se usa processo manual:

```bash
arkhe stop
arkhe start
```

Depois rode:

```bash
arkhe doctor
```

---

## Logs e Backup

Logs típicos:

- `~/.rlm/logs/api.log`
- `~/.rlm/logs/ws.log`

Exemplo Windows:

```powershell
Get-Content $HOME\.rlm\logs\api.log -Wait
```

Backups mínimos:

- `rlm_memory_v2.db`
- `rlm_sessions.db`
- `~/.rlm/scheduler.db`
- `.env` guardado fora do repositório

---

## Troubleshooting

| Problema | Causa provável | Ação objetiva |
| --- | --- | --- |
| Porta 5000 ocupada | daemon ativo + start manual | use só `systemctl --user restart rlm` ou só `arkhe start`, não ambos |
| WebChat não abre | API fora do ar ou bind errado | rode `arkhe status` e confirme `RLM_API_HOST`/`RLM_API_PORT` |
| OpenAI-compatible retorna 401/403 | `RLM_API_TOKEN` ausente ou errado | gere/valide token e reinicie |
| Telegram não responde | token ausente, chat não permitido ou serviço não reiniciado | confira `TELEGRAM_BOT_TOKEN`, `RLM_ALLOWED_CHATS` e reinicie |
| O wizard manteve configuração antiga | você escolheu `Manter valores atuais` | rode `arkhe setup` de novo e escolha `Modificar valores` |
| O servidor responde mas usa um modelo só | `.env` sem `RLM_MODEL_*` | configure split recomendado ou manual no wizard |
| Exposição acidental de chave/token | segredo comprometido | rotacione imediatamente |

---

## Regra de Ouro

Para começar certo:

1. use o wizard
2. mantenha bind local
3. separe os tokens
4. ative split de modelos quando custo ou latência importarem
5. só exponha o serviço depois de autenticação, proxy e TLS estarem fechados
