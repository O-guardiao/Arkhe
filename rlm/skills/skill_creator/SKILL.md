+++
name = "skill_creator"
description = "Create new RLM skills dynamically. Generates SKILL.md with proper TOML frontmatter, SIF impl, metadata, and examples. Use when: user asks to criar nova skill, adicionar capacidade ao agente, registrar novo tool, empacotar funcionalidade como skill. NOT for: editing existing skills (edit directly), running skills (use the skill itself)."
tags = ["criar skill", "nova skill", "meta-skill", "gerar ferramenta", "registrar tool", "empacotar", "skill-creator", "auto-skill", "self-extend"]
priority = "lazy"

[sif]
signature = "create_skill(name: str, description: str, impl_code: str = '', tags: list = None) -> dict"
prompt_hint = "Use para criar nova skill RLM quando o agente precisa de uma capacidade que ainda não existe."
short_sig = "create_skill(name,desc,impl='')→{}"
compose = ["filesystem", "shell", "coding_agent"]
examples_min = ["criar nova skill RLM com TOML frontmatter e função impl"]

[requires]
bins = []

[runtime]
estimated_cost = 0.5
risk_level = "medium"
side_effects = ["file_write"]
postconditions = ["skill_file_created"]
fallback_policy = "provide_skill_template_for_manual_creation"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "create skill tool agent capability extend self-modify meta programming"
example_queries = ["crie uma nova skill", "adicione essa capacidade ao agente", "empacote como skill"]
+++

# Skill Creator

Meta-skill: cria novas skills RLM dinamicamente.

## Quando usar

✅ **USE quando:**
- "Crie uma skill para controlar a API do Jira"
- "Empacote essa funcionalidade como skill reutilizável"
- "O agente precisa de uma capacidade de [X] — crie a skill"
- "Registre esse código como ferramenta permanente"

❌ **NÃO use quando:**
- Editar skill existente → edite o SKILL.md diretamente
- Executar uma skill → use a skill em questão
- Código descartável → faça inline no REPL

## Template de SKILL.md

```python
import os

def create_skill(
    name: str,
    description: str,
    impl_code: str = "",
    tags: list = None,
    priority: str = "contextual",
    risk_level: str = "low",
    side_effects: list = None,
    compose: list = None,
    signature: str = "",
    prompt_hint: str = "",
) -> dict:
    """Cria nova skill RLM com SKILL.md completo."""
    
    if tags is None:
        tags = [name]
    if side_effects is None:
        side_effects = []
    if compose is None:
        compose = []
    if not signature:
        signature = f"{name}() -> dict"
    if not prompt_hint:
        prompt_hint = description
    
    short_sig = signature.split("->")[0].strip() + "→{}"
    
    tags_str = json_list(tags)
    se_str = json_list(side_effects)
    compose_str = json_list(compose)
    
    impl_section = ""
    if impl_code:
        impl_section = f'impl = """\n{impl_code}\n"""'
    
    skill_md = f'''+++
name = "{name}"
description = "{description}"
tags = {tags_str}
priority = "{priority}"

[sif]
signature = "{signature}"
prompt_hint = "{prompt_hint}"
short_sig = "{short_sig}"
compose = {compose_str}
examples_min = ["{description}"]
{impl_section}

[requires]
bins = []

[runtime]
estimated_cost = 0.5
risk_level = "{risk_level}"
side_effects = {se_str}
postconditions = ["{name}_completed"]
fallback_policy = "explain_manual_steps"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "{name} {description}"
example_queries = ["use {name}"]
+++

# {name.replace("_", " ").title()} Skill

{description}

## Função disponível no REPL

```python
resultado = {name}()
```
'''
    
    skills_dir = os.path.expanduser("~/.arkhe/repo/rlm/skills")
    skill_path = os.path.join(skills_dir, name, "SKILL.md")
    os.makedirs(os.path.dirname(skill_path), exist_ok=True)
    
    with open(skill_path, "w", encoding="utf-8") as f:
        f.write(skill_md)
    
    return {
        "created": skill_path,
        "name": name,
        "has_impl": bool(impl_code),
        "note": "Skill criada. Reinicie a sessão para carregar.",
    }


def json_list(items):
    return "[" + ", ".join(f'"{i}"' for i in items) + "]"
```

## Exemplo completo: criar skill de Jira

```python
resultado = create_skill(
    name="jira",
    description="Gerenciar issues e projetos no Jira via REST API",
    tags=["jira", "issue", "projeto", "ticket", "sprint"],
    priority="contextual",
    risk_level="medium",
    side_effects=["http_request"],
    compose=["github", "notion", "slack"],
    signature="jira_query(jql: str) -> list[dict]",
    prompt_hint="Use para consultar, criar e atualizar issues no Jira.",
    impl_code='''
def jira_query(jql):
    import urllib.request, json, os, base64
    domain = os.environ.get("JIRA_DOMAIN", "")
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    if not all([domain, email, token]):
        return [{"error": "JIRA_DOMAIN, JIRA_EMAIL, JIRA_API_TOKEN não configurados"}]
    cred = base64.b64encode(f"{email}:{token}".encode()).decode()
    url = f"https://{domain}/rest/api/3/search?jql=" + urllib.parse.quote(jql)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {cred}",
        "Accept": "application/json",
    })
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read())
    return [{"key": i["key"], "summary": i["fields"]["summary"], "status": i["fields"]["status"]["name"]}
            for i in data.get("issues", [])]
''',
)
print(resultado)
# {"created": "/root/.arkhe/repo/rlm/skills/jira/SKILL.md", "name": "jira", "has_impl": true}
```

## Estrutura esperada  

```
rlm/skills/
├── jira/
│   └── SKILL.md     ← criada pelo skill_creator
├── web_search/
│   └── SKILL.md
├── browser/
│   └── SKILL.md
└── ...
```

## Campos obrigatórios no frontmatter

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `name` | str | Identificador único da skill |
| `description` | str | Quando usar / não usar (usado pelo router) |
| `tags` | list | Palavras-chave para matching semântico |
| `priority` | str | `always`, `contextual`, ou `lazy` |
| `[sif].signature` | str | Assinatura da função principal |
| `[sif].impl` | str | Código Python inline (opcional) |
| `[runtime].risk_level` | str | `low`, `medium`, `high` |
