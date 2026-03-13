+++
name = "playwright"
description = "Navegação web completa com JavaScript via Playwright MCP: clica em botões, preenche formulários, faz screenshots, navega em SPAs, faz scraping de sites que exigem JS, autentica em portais, interage com elementos dinâmicos. Use when: site requires JavaScript, login automático, formulário web, SPA (React/Angular/Vue), download via browser, captura de screenshot. PREFERRED over browser skill when: JS rendering needed."
tags = ["browser", "site", "formulário", "screenshot", "scraping", "login", "javascript", "spa", "navegar", "clicar", "preencher", "react", "portal", "automação web"]
priority = "contextual"

[mcp]
command = "npx.cmd"
args = ["-y", "@playwright/mcp"]

[requires]
bins = ["node"]

[sif]
signature = "playwright.goto(url: str) -> str"
prompt_hint = "Use para sites com JavaScript, login, clique, formulário, screenshot ou scraping de SPA dinâmica."
short_sig = "playwright.goto(url)→str"
compose = ["browser", "web_search", "sqlite"]
examples_min = ["abrir um site com JavaScript e interagir com formulário"]

[runtime]
estimated_cost = 1.8
risk_level = "medium"
side_effects = ["browser_session", "remote_form_submit", "screenshot_capture"]
postconditions = ["dynamic_page_interaction_completed"]
fallback_policy = "fallback_to_browser_for_static_pages"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "playwright browser javascript spa login click form screenshot automation"
example_queries = ["preencha este formulário web", "abra a SPA e clique no botão"]
+++

# Playwright Skill (Tier C — MCP)

Navegação web com JavaScript real via `@playwright/mcp`. Funciona em sites SPA, portais que exigem login, e qualquer página que falha com requests simples.

## Diferença entre Browser Skill e Playwright Skill

| Característica | `browser` (Tier B) | `playwright` (Tier C — esta skill) |
|---|---|---|
| Execução JS | ❌ Não | ✅ Sim |
| Login / cookies | ❌ Não | ✅ Sim |
| SPAs (React/Vue/Angular) | ❌ Falha | ✅ Funciona |
| Screenshots | ❌ Não | ✅ Sim |
| Clique em botões | ❌ Não | ✅ Sim |
| Velocidade | 🟢 Rápido | 🟡 Moderado |
| Dependência | stdlib | node + npx |

## Quando usar esta skill

✅ **USE quando:**
- "Faz login no LinkedIn e extrai conexões"
- "Abre Google Flights e busca voos SP→Lisboa"
- "Tira screenshot da homepage do site"
- "Preenche e submete formulário de contato"
- "Extrai dados de tabela em site com carregamento dinâmico"
- "Navega por N páginas de resultados de busca"

## Ferramentas disponíveis via MCP

O servidor Playwright MCP expõe automaticamente as seguintes ferramentas ao LLM:

### Navegação
- `browser_navigate(url)` — navega para URL
- `browser_go_back()` / `browser_go_forward()` — histórico
- `browser_reload()` — recarrega página
- `browser_wait_for(selector, timeout_ms)` — aguarda elemento aparecer

### Interação
- `browser_click(selector)` — clica em elemento CSS/xpath
- `browser_type(selector, text)` — digita texto em campo
- `browser_select_option(selector, value)` — seleciona opção em `<select>`
- `browser_check(selector)` / `browser_uncheck(selector)` — checkboxes
- `browser_hover(selector)` — hover em elemento
- `browser_press_key(key)` — pressiona tecla (Enter, Tab, Escape, etc.)
- `browser_scroll(direction, amount)` — rola página

### Extração de dados
- `browser_snapshot()` — retorna acessibility tree da página (texto estruturado)
- `browser_get_text(selector)` — texto de elemento específico
- `browser_get_attribute(selector, attribute)` — atributo HTML

### Visual / Debug
- `browser_screenshot()` — captura screenshot (retorna base64 ou salva em arquivo)
- `browser_pdf()` — exporta página como PDF

### Contexto / Sessão
- `browser_new_tab(url)` — abre nova aba
- `browser_close_tab()` — fecha aba atual
- `browser_set_viewport(width, height)` — define tamanho da janela

## Exemplos de uso (como o LLM deve chamar)

### Login automático

```
1. Navegar para https://www.site.com/login
2. Digitar email em [selector: #email] 
3. Digitar senha em [selector: #password]
4. Clicar em [selector: button[type="submit"]]
5. Aguardar carregamento: browser_wait_for(".dashboard", 5000)
6. Tirar snapshot para confirmar login
```

### Scraping com JS

```
1. Navegar para https://site-dinamico.com/listagem
2. Aguardar: browser_wait_for(".items-container", 5000)
3. Scroll para baixo para carregar lazy-load
4. Capturar snapshot completo da página
5. Extrair dados da estrutura retornada
```

### Screenshot de página

```
1. browser_navigate("https://exemplo.com")
2. browser_wait_for("body", 3000)
3. browser_screenshot() → salva imagem
4. Retorna path do arquivo ou base64
```

### Formulário multi-passo

```
1. Navegar para formulário
2. Preencher campos do passo 1
3. Clicar "Próximo"
4. Aguardar passo 2 carregar
5. Preencher campos do passo 2
6. Submeter
7. Verificar confirmação via snapshot
```

## Configuração do servidor MCP

O servidor é iniciado automaticamente pelo RLM quando esta skill é carregada.

```bash
# Instalação manual (primeira vez — npx baixa automaticamente)
npx -y @playwright/mcp

# Instalar browsers (Chromium por padrão)
npx playwright install chromium
```

### Opções de ambiente

```bash
# Modo headless (padrão em servidor)
PLAYWRIGHT_HEADLESS=true

# Para debug visual (mostra browser)
PLAYWRIGHT_HEADLESS=false

# Timeout padrão de navegação (ms)
PLAYWRIGHT_TIMEOUT=30000
```

## Preços e limites

- `@playwright/mcp` é **100% gratuito e open-source**
- Requer Node.js ≥ 18 instalado (`node --version` para verificar)
- Chromium é baixado automaticamente (~130 MB na primeira execução)
- Sem limites de requisições ou rate limiting

## Fallback automático

Se `playwright` não estiver disponível (node não instalado), o RLM usa automaticamente a skill `browser` (Tier B) para requisições sem JS. Para sites que exigem JS, informa ao usuário que Node.js é necessário.
