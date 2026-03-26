# Pós-Mortem: Refatoração Mixin do RLM — Diagnóstico e Correções

**Data:** Março 2026  
**Projeto:** `RLM_OpenClaw_Engine / rlm-main`  
**Estado final:** 1341/1341 testes passando, 3 skipped, 6 subtests passed

---

## 1. Contexto

O arquivo monolítico `rlm.py` (1.876 linhas) foi refatorado para uma arquitetura baseada em mixins, dividida em 5 arquivos:

```
rlm/core/
├── rlm.py                    # Orquestrador fino (herda dos mixins)
├── rlm_context_mixin.py      # _spawn_completion_context, _setup_prompt, _inject_repl_globals
├── rlm_loop_mixin.py         # Lógica de loop principal
├── rlm_mcts_mixin.py         # Monte Carlo Tree Search
└── rlm_persistence_mixin.py  # Persistência de sessão
```

A classe principal passou a ser:

```python
class RLM(RLMContextMixin, RLMLoopMixin, RLMMCTSMixin, RLMPersistenceMixin):
    ...
```

Após a refatoração, **5 categorias de problemas** foram encontradas e corrigidas.

---

## 2. Problemas Encontrados e Corrigidos

### Problema 1 — 26 ghost imports em `rlm/core/rlm.py`

**Sintoma:** `rlm.py` ainda importava símbolos (`get_client`, `hashlib`, `BaseLM`, `CodeBlock`, `find_code_blocks`, `make_sub_rlm_fn`, `make_browser_globals`, etc.) que haviam sido movidos para os mixins durante a refatoração.

**Causa raiz:** Os métodos foram extraídos para os mixins, mas os imports correspondentes não foram removidos do orquestrador.

**Correção:** Remoção dos 26 imports fantasmas de `rlm/core/rlm.py`.

---

### Problema 2 — `import os as _os` dentro de `__init__`

**Sintoma:** `import os as _os` estava posicionado dentro do método `__init__` da classe em vez de no nível do módulo.

**Causa raiz:** Descuido durante a criação dos mixins.

**Correção:** Import movido para o topo do módulo.

---

### Problema 3 — 3 testes de texto estático desatualizados

**Sintoma:** Testes em `test_critical_subrlm.py`, `test_critical_skills_new.py` e `test_critical_subrlm_parallel.py` verificavam se determinadas funções estavam presentes em `rlm.py`, mas essas funções haviam sido movidas para `rlm_context_mixin.py`.

**Causa raiz:** Os testes específicos de presença de código (grep estático) não foram atualizados junto com a refatoração.

**Correção:** Os 3 testes foram atualizados para apontar para `rlm_context_mixin.py`.

**Arquivos modificados:**
- `tests/test_critical_subrlm.py` — `test_make_sub_rlm_fn_imported_in_rlm_py`
- `tests/test_critical_skills_new.py` — `test_rlm_imports_make_browser_globals`
- `tests/test_critical_subrlm_parallel.py` — `test_rlm_py_imports_make_sub_rlm_parallel_fn`

---

### Problema 4 — 20 testes com `AttributeError: module 'rlm.core.rlm' has no attribute 'get_client'`

**Sintoma:** `test_multi_turn_integration.py` e `test_session_async.py` usavam `patch.object(rlm_module, "get_client")` onde `rlm_module = rlm.core.rlm`. Porém `get_client` foi para `rlm_context_mixin.py`.

**Causa raiz:** O `patch.object` precisa apontar para o módulo onde o símbolo **reside**, não onde ele era usado antes da refatoração.

> **Regra de ouro do `unittest.mock`:**  
> Sempre faça patch de onde o símbolo é **importado e usado**, não de onde ele é **definido**.  
> Como `rlm_context_mixin.py` faz `from rlm.clients import get_client` e usa esse `get_client` localmente, o patch deve ser feito em `rlm.core.rlm_context_mixin`.

**Correção:**

```python
# Antes (quebrado)
import rlm.core.rlm as rlm_module

# Depois (correto)
import rlm.core.rlm_context_mixin as rlm_module
```

**Arquivos modificados:**
- `tests/test_multi_turn_integration.py` — 1 import no topo + 13 chamadas `patch.object(rlm_module, "get_client")`
- `tests/test_session_async.py` — 8 imports inline (nas linhas 650, 677, 701, 728, 758, 785, 961, 991)

---

### Problema 5 — `test_tui.py` e `test_mcts.py` falhando intermitentemente no suite completo (xdist)

**Sintoma:** Em execução isolada, todos os testes passavam. No suite completo com `pytest-xdist` (`-n auto`), `AttributeError: module 'rlm' has no attribute 'server'` surgia esporadicamente.

**Causa raiz (deep):** O mecanismo `_dot_lookup` do `unittest.mock.patch()` resolve caminhos pontilhados como `"rlm.server.runtime_pipeline.X"` assim:

```
1. getattr(rlm, 'server')          → falha (rlm/__init__.py não expõe rlm.server)
2. __import__('rlm.server')        → cache hit em sys.modules (outro worker já importou)
                                      mas NÃO re-vincula 'server' como atributo no módulo rlm
3. getattr(rlm, 'server')          → ainda falha → AttributeError
```

Com workers `xdist` compartilhando `sys.modules` via cache, `rlm.server` podia estar em `sys.modules` sem nunca ter sido vinculado como atributo no objeto módulo `rlm`. Isso tornava o comportamento dependente da **ordem de execução dos testes**.

**Por que `rlm/__init__.py` não expunha `rlm.server`:**

```python
# Estado original — sem binding de subpacotes
from rlm.core.rlm import RLM

__all__ = ["RLM"]
```

**Correção — PEP 562 (`__getattr__` de módulo):**

```python
from rlm.core.rlm import RLM

__all__ = ["RLM"]


def __getattr__(name: str):
    """PEP 562 — acesso lazy a subpacotes como atributo do pacote.

    Quando unittest.mock.patch() resolve um caminho como
    "rlm.server.runtime_pipeline.X", chama _dot_lookup que faz
    getattr(rlm, 'server'). Se o atributo não existe (workers xdist podem
    ter entradas em sys.modules sem o atributo pai vinculado), Python cai
    aqui, que importa e retorna o subpacote sob demanda — corrigindo o
    AttributeError silenciosamente sem carregamento antecipado.
    """
    import importlib
    try:
        return importlib.import_module(f"rlm.{name}")
    except ModuleNotFoundError:
        raise AttributeError(f"module 'rlm' has no attribute {name!r}") from None
```

**Por que PEP 562 e não as alternativas:**

| Alternativa | Problema |
|---|---|
| `import rlm.server` eager no `__init__.py` | Carrega `fastapi`, `pydantic`, `starlette` em todo `import rlm` — inaceitável em produção |
| `import rlm.server` antes de cada `patch` nos testes | Dezenas de testes afetados; frágil; qualquer teste futuro repetiria o mesmo erro |
| Converter todos os `patch("rlm.server.X")` para `patch.object(module, ...)` | `_dot_lookup` sempre percorre de `rlm` para strings dotadas — não há como evitar sem reescrever todos os patches |
| **PEP 562** | Uma única correção na camada certa, sem carregamento antecipado, cobre todos os casos atuais e futuros |

---

## 3. Resumo de Todos os Arquivos Modificados

| Arquivo | Tipo de mudança |
|---|---|
| `rlm/core/rlm.py` | Remoção de 26 ghost imports; `import os` para nível de módulo |
| `rlm/__init__.py` | PEP 562 `__getattr__` adicionado |
| `tests/test_critical_subrlm.py` | Target de teste estático → `rlm_context_mixin.py` |
| `tests/test_critical_skills_new.py` | Target de teste estático → `rlm_context_mixin.py` |
| `tests/test_critical_subrlm_parallel.py` | Target de teste estático → `rlm_context_mixin.py` |
| `tests/test_multi_turn_integration.py` | Import de `rlm_module` → `rlm_context_mixin` |
| `tests/test_session_async.py` | 8 imports inline → `rlm_context_mixin` |

---

## 4. Estado Final do `rlm/__init__.py`

```python
from rlm.core.rlm import RLM

__all__ = ["RLM"]


def __getattr__(name: str):
    import importlib
    try:
        return importlib.import_module(f"rlm.{name}")
    except ModuleNotFoundError:
        raise AttributeError(f"module 'rlm' has no attribute {name!r}") from None
```

> **Importante:** O `__getattr__` é intencional e **não deve ser removido**. Ele resolve o problema de race condition do xdist com `unittest.mock._dot_lookup`.

---

## 5. Validação Final

```
1341 passed, 3 skipped, 6 subtests passed in ~38s
```

Executado com:

```powershell
python -m pytest tests/ -q --ignore=tests/test_real_api.py --ignore=tests/test_openai_integration.py
```

> `test_real_api.py` e `test_openai_integration.py` foram ignorados por fazerem chamadas reais à API (não são testes unitários/de integração local).

Os 3 testes "skipped" são skips programáticos (`@pytest.mark.skip`) — não são falhas.

---

## 6. Lições Aprendidas

1. **Ao mover métodos para mixins, remova imediatamente os imports do arquivo de origem.** Ghost imports mascaram onde o código realmente vive e causam bugs silenciosos em patches de teste.

2. **`patch.object(module, name)` deve apontar para onde o símbolo é importado e usado, não onde foi definido originalmente.** Após uma refatoração, revise todos os patches.

3. **Execução paralela (xdist) expõe bugs de estado global que execução serial esconde.** Workers paralelos compartilham `sys.modules` de forma que estados intermediários podem ser observáveis.

4. **PEP 562 (`__getattr__` de módulo) é a solução idiomática para expor subpacotes lazily.** Use quando o pacote pai precisa resolver atributos dinamicamente sem carregamento antecipado.

5. **Nunca confie apenas em execução isolada de testes.** Sempre valide com o suite completo e com paralelismo habilitado antes de declarar uma refatoração como "completa".

---

## 7. Referências

- [PEP 562 — Module `__getattr__` and `__dir__`](https://peps.python.org/pep-0562/)
- [unittest.mock — `patch` internals (`_dot_lookup`)](https://github.com/python/cpython/blob/main/Lib/unittest/mock.py)
- [pytest-xdist — execução paralela](https://pytest-xdist.readthedocs.io/)
