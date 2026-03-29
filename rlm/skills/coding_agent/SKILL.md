+++
name = "coding_agent"
description = "Delegate code tasks to a sub-RLM agent running in sandbox. Spawns an isolated coding session for implementation, debugging, or code review. Use when: user asks for complex code generation, refactoring, multi-file changes, debugging a project, or code review. NOT for: simple one-liner code (do it inline), running shell commands (use shell skill), file read/write (use filesystem skill)."
tags = ["código", "programar", "refatorar", "debug", "implementar", "code review", "gerar código", "coding agent", "sub-agente", "sandbox", "desenvolvimento"]
priority = "contextual"

[sif]
signature = "coding_agent(task: str, files: list = None, language: str = 'python', max_depth: int = 3) -> dict"
prompt_hint = "Use para delegar tarefas complexas de código a um sub-agente isolado. Fornece workspace próprio e retorna resultado + artefatos."
short_sig = "coding_agent(task,files=[],lang='python')→{}"
compose = ["shell", "filesystem", "github", "web_search"]
examples_min = ["delegar implementação complexa a sub-agente especializado em código"]

[requires]
bins = []

[runtime]
estimated_cost = 3.0
risk_level = "medium"
side_effects = ["sub_rlm_spawn", "file_write", "shell_exec"]
postconditions = ["code_artifacts_returned"]
fallback_policy = "implement_inline_or_provide_pseudocode"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "code generate implement debug refactor review programming agent sub-agent sandbox development"
example_queries = ["implemente esta funcionalidade", "refatore este código", "faça code review"]
+++

# Coding Agent Skill

Delega tarefas de código complexas a sub-agentes RLM isolados.

## Quando usar

✅ **USE quando:**
- "Implemente um servidor FastAPI com autenticação JWT"
- "Refatore este módulo para usar async/await"
- "Faça code review deste arquivo"
- "Debug: por que este teste falha?"
- "Gere testes unitários para este módulo"

❌ **NÃO use quando:**
- Código simples (1-2 funções) → faça inline no REPL
- Executar comando → use `shell` skill
- Ler/escrever arquivo → use `filesystem` skill

## Delegação via sub_rlm

```python
import json

def coding_agent(task: str, files: list = None, language: str = "python", max_depth: int = 3) -> dict:
    """
    Delega tarefa de código a um sub-RLM.
    
    task: descrição da tarefa
    files: lista de caminhos para ler como contexto
    language: linguagem alvo
    max_depth: profundidade máxima de recursão do sub-agente
    """
    context_parts = [f"Linguagem: {language}", f"Tarefa: {task}"]
    
    if files:
        for fpath in files:
            try:
                with open(fpath, 'r') as f:
                    content = f.read()
                context_parts.append(f"\n--- {fpath} ---\n{content}")
            except Exception as e:
                context_parts.append(f"\n--- {fpath} (erro: {e}) ---")
    
    full_prompt = "\n".join(context_parts)
    
    # Usa sub_rlm se disponível no REPL
    result = sub_rlm(
        f"Você é um coding agent especialista em {language}. "
        f"Execute a tarefa com código funcional, testável e idiomático.\n\n"
        f"{full_prompt}",
        max_depth=max_depth,
    )
    
    return {
        "task": task,
        "language": language,
        "result": result,
        "files_read": files or [],
    }
```

## Padrões de uso

### Implementação com contexto

```python
# Ler arquivos relevantes e delegar
resultado = coding_agent(
    task="Adicione endpoint POST /api/users com validação Pydantic",
    files=["/root/projeto/main.py", "/root/projeto/models.py"],
    language="python",
)
FINAL_VAR("resultado")
```

### Debug com stack trace

```python
resultado = coding_agent(
    task="""Debug este erro:
    TypeError: 'NoneType' object is not subscriptable
    em linha 42 de processor.py
    Identifique a causa raiz e proponha fix.""",
    files=["/root/projeto/processor.py"],
)
```

### Code review

```python
resultado = coding_agent(
    task="Faça code review focado em: segurança, performance, e manutenibilidade",
    files=[
        "/root/projeto/auth.py",
        "/root/projeto/database.py",
    ],
    language="python",
)
```

### Gerar testes

```python
resultado = coding_agent(
    task="Gere testes unitários com pytest cobrindo: happy path, edge cases, error handling",
    files=["/root/projeto/services/payment.py"],
    language="python",
)
```

## Fluxo interno

1. **Recebe tarefa** + arquivos de contexto
2. **Monta prompt** com código existente como referência
3. **Spawna `sub_rlm()`** com profundidade limitada
4. **Sub-agente executa**: lê, escreve, testa
5. **Retorna resultado** com artefatos produzidos

## Limitações

- Sub-agente herda access tokens do pai (filesystem, shell)
- Cada spawn consome tokens do modelo (~2-8k/tarefa)
- max_depth evita loops infinitos (default: 3)
- Prefira tarefas auto-contidas com contexto explícito
