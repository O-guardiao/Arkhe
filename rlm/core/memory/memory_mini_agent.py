"""
memory_mini_agent.py — Agente de Memória com GPT-4.1-nano

Responsabilidade única: toda inteligência relacionada a AVALIAR e ESTRUTURAR
memórias usa este módulo. Nenhum outro módulo chama GPT-4.1-nano diretamente.

Por que GPT-4.1-nano?
  - Baixíssima latência (<500ms tipicamente)
  - Barato: ideal para chamadas auxiliares que não precisam de raciocínio profundo
  - Projetado para trabalho agêntico com instruções muito detalhadas
  IMPORTANTE: nano precisa de prompts altamente explícitos com exemplos e escala
  definida. Prompts vagos produzem resultados inconsistentes. Todos os prompts
  aqui foram construídos com esse requisito em mente.

Funções exportadas:
  assign_importance(text)           → float 0.0–1.0
  extract_memory_nuggets(user_msg, assistant_response) → list[str]
  identify_edge(existing_text, new_text) → str | None

Todas as funções:
  - São síncronas (chamadas do thread de compactação/background)
  - Retornam valor padrão seguro em caso de falha (sem raise)
  - São idempotentes (mesmas entradas → mesma saída)
  - Usam SOMENTE o modelo gpt-4.1-nano
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional

try:
    import openai as _openai
    _openai_available = True
except ImportError:
    _openai = None  # type: ignore[assignment]
    _openai_available = False

from rlm.core.structured_log import get_logger

_log = get_logger("memory_mini_agent")

# ---------------------------------------------------------------------------
# Constantes do modelo
# ---------------------------------------------------------------------------
MINI_AGENT_MODEL = "gpt-4.1-nano"
MINI_AGENT_TIMEOUT = 12.0       # segundos — nano é rápido; acima disso é falha de rede
MINI_AGENT_TEMPERATURE = 0.1    # quase determinístico — respostas de classificação/extração
MINI_AGENT_MAX_TOKENS = 512     # saídas curtas — nunca deixar nano "vagar"

# ---------------------------------------------------------------------------
# Singleton do cliente OpenAI
# ---------------------------------------------------------------------------
_client: Any = None  # openai.OpenAI instance or None


def _get_client() -> Any:
    """
    Retorna o cliente OpenAI singleton.
    Criado na primeira chamada (lazy init). Retorna None se OpenAI não está disponível.
    """
    global _client
    if _client is not None:
        return _client
    if not _openai_available:
        _log.warn("openai package não disponível — mini agent desabilitado.")
        return None
    if not os.getenv("OPENAI_API_KEY"):
        _log.warn("OPENAI_API_KEY não definida — mini agent desabilitado.")
        return None
    try:
        import openai
        _client = openai.OpenAI()
        return _client
    except Exception as exc:
        _log.warn(f"Falha ao criar cliente OpenAI para mini agent: {exc}")
        return None


def _call_nano(system_prompt: str, user_content: str) -> Optional[str]:
    """
    Faz uma chamada ao GPT-4.1-nano com retry único em falha transitória.

    Args:
        system_prompt: Instrução de sistema — o nano precisa de instruções muito
                       detalhadas aqui. Inclua escala, exemplos e formato de saída.
        user_content: Conteúdo do usuário a ser processado.

    Returns:
        Texto da resposta ou None em caso de falha.
    """
    client = _get_client()
    if client is None:
        return None

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=MINI_AGENT_MODEL,
                temperature=MINI_AGENT_TEMPERATURE,
                max_tokens=MINI_AGENT_MAX_TOKENS,
                timeout=MINI_AGENT_TIMEOUT,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            if attempt == 0:
                err_str = str(exc)
                # Retry apenas em erros transitórios comuns
                if any(k in err_str.lower() for k in ("timeout", "connection", "502", "503", "429")):
                    _log.warn(f"Mini agent tentativa {attempt+1} falhou ('{err_str[:80]}'), retentando em 1s...")
                    time.sleep(1.0)
                    continue
            _log.warn(f"Mini agent falhou (suprimido): {exc}")
            return None
    return None


# ---------------------------------------------------------------------------
# 1. assign_importance — avalia a importância estratégica de um fragmento
# ---------------------------------------------------------------------------

_IMPORTANCE_SYSTEM_PROMPT = """
Você é um avaliador de importância de memórias para um assistente de IA.
Sua função é atribuir uma pontuação de importância a um fragmento de texto.

ESCALA DE IMPORTÂNCIA (1 a 10):
  10 — Decisão crítica e irreversível (ex: "decidimos usar PostgreSQL para produção",
       "o usuário cancelou o projeto X", "nova restrição de segurança aprovada")
   9 — Compromisso explícito ou preferência forte e duradoura
       (ex: "sempre usar tipagem estrita", "nunca usar sleep() em produção")
   8 — Correção de erro grave ou aprendizado técnico importante
       (ex: "o bug era causado por race condition no lock", "descobrimos que a API retorna UTC")
   7 — Preferência pessoal declarada ou convenção de projeto estabelecida
       (ex: "prefiro funções pequenas", "padrão de nomeclatura: snake_case")
   6 — Fato técnico novo e relevante para o projeto
       (ex: "a versão 3.2 da biblioteca mudou o comportamento de X")
   5 — Informação contextual útil mas não crítica
       (ex: "o projeto usa Python 3.11", "o ambiente é Ubuntu 22.04")
   4 — Detalhe operacional de vida curta
       (ex: "rodando testes agora", "abrindo o arquivo config.json")
   3 — Comentário conversacional com algum conteúdo
       (ex: "obrigado, vou tentar isso")
   2 — Saudação, encerramentos, ou confirmações vazias
       (ex: "ok", "entendido", "ótimo!")
   1 — Ruído puro sem valor informacional
       (ex: "...", "[rindo]", "hm")

REGRA DE OURO: Informações que mudam o comportamento futuro do assistente (decisões,
preferências, restrições, erros corrigidos) valem 7-10. Fatos técnicos neutros valem
5-6. Conversas de baixo valor valem 1-4.

FORMATO DE SAÍDA: Retorne APENAS um número inteiro de 1 a 10, sem texto adicional.
Exemplos de saída válida: "8" ou "3" ou "10"
""".strip()


def assign_importance(text: str) -> float:
    """
    Avalia a importância estratégica de um fragmento de texto para memória de longo prazo.

    Usa GPT-4.1-nano com prompt altamente detalhado para classificar o valor
    informacional do conteúdo em uma escala de 1 a 10.

    Args:
        text: Fragmento de texto a avaliar (máximo recomendado: 1000 chars).

    Returns:
        float entre 0.0 e 1.0. Retorna 0.5 (valor neutro) em caso de falha.
        A escala 1-10 do nano é normalizada para 0.0-1.0 (dividindo por 10).
    """
    if not text or not text.strip():
        return 0.1  # texto vazio = importância mínima

    # Limita o texto para evitar tokens excessivos no nano
    truncated = text.strip()[:1200]

    raw = _call_nano(_IMPORTANCE_SYSTEM_PROMPT, f"Avalie este fragmento:\n{truncated}")
    if raw is None:
        return 0.5  # fallback neutro

    try:
        # Extrai o número da resposta (pode ter espaços ou pontuação ao redor)
        match = re.search(r'\b([1-9]|10)\b', raw.strip())
        if match:
            score = int(match.group(1))
            return round(score / 10.0, 2)
    except (ValueError, AttributeError):
        pass

    _log.warn(f"assign_importance: resposta inesperada '{raw[:50]}' — usando 0.5")
    return 0.5


# ---------------------------------------------------------------------------
# 2. extract_memory_nuggets — extrai fragmentos memorizáveis de um turno
# ---------------------------------------------------------------------------

_NUGGETS_SYSTEM_PROMPT = """
Você é um extrator de "pepitas de memória" (memory nuggets) para um assistente de IA.
Sua função é ler um turno de conversa e extrair APENAS as informações que VALEM SER
LEMBRADAS a longo prazo.

O QUE EXTRAIR (vale a pena lembrar):
  ✅ Decisões técnicas tomadas (frameworks, bibliotecas, arquiteturas escolhidas)
  ✅ Preferências explícitas do usuário ("prefiro X", "sempre use Y", "nunca faça Z")
  ✅ Restrições do projeto (limitações técnicas, de segurança, de negócio)
  ✅ Erros identificados e suas causas-raiz ("o bug era X porque Y")
  ✅ Informações do contexto do usuário (linguagens, ambiente, nível de experiência)
  ✅ Correções que o usuário fez ao assistente
  ✅ Nomes de entidades importantes (variáveis, funções, módulos, endpoints)
  ✅ Fatos técnicos novos e relevantes

O QUE NÃO EXTRAIR:
  ❌ Saudações ("olá", "obrigado", "tudo bem")
  ❌ Confirmações vazias ("ok", "entendido", "sim")
  ❌ Repetições de informações já óbvias no texto
  ❌ Passos procedurais temporários sem relevância futura
  ❌ Conteúdo de código fonte completo (apenas aprendizados sobre o código)

FORMATO DE SAÍDA: JSON array de strings. Cada string é uma pepita de memória.
  - Máximo de 5 pepitas por turno (selecione as mais importantes)
  - Cada pepita deve ser autocontida (entendível sem o contexto do turno)
  - Use linguagem declarativa: "O usuário prefere X", "O projeto usa Y", "A função Z faz W"
  - Se não há nada relevante, retorne: []

Exemplos:
  Input: "Usuário: use sempre async/await. Assistente: entendido."
  Output: ["O usuário exige uso de async/await em todo o código"]

  Input: "Usuário: oi. Assistente: olá!"
  Output: []

  Input: "Usuário: o banco é PostgreSQL 15 na AWS RDS. Assistente: anotado."
  Output: ["O banco de dados é PostgreSQL 15 hospedado na AWS RDS"]

RETORNE APENAS O JSON ARRAY. Nenhum texto antes ou depois.
""".strip()


def extract_memory_nuggets(user_message: str, assistant_response: str) -> list[str]:
    """
    Extrai fragmentos de informação memorável de um turno de conversa.

    Analisa a troca usuário↔assistente e extrai declarações autocontidas que
    valem ser armazenadas como memória de longo prazo.

    Args:
        user_message: Mensagem enviada pelo usuário neste turno.
        assistant_response: Resposta completa do assistente neste turno.

    Returns:
        Lista de strings — cada uma é um "nugget" de memória autocontido.
        Retorna lista vazia em caso de falha ou conteúdo sem valor.
    """
    if not user_message and not assistant_response:
        return []

    # Limita para não gastar tokens em turnos muito longos
    user_trunc = (user_message or "").strip()[:600]
    assistant_trunc = (assistant_response or "").strip()[:600]

    content = f"Turno de conversa:\nUsuário: {user_trunc}\nAssistente: {assistant_trunc}"

    raw = _call_nano(_NUGGETS_SYSTEM_PROMPT, content)
    if raw is None:
        return []

    try:
        # Procura por JSON array na resposta — o nano às vezes adiciona markdown
        json_match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if json_match:
            nuggets = json.loads(json_match.group(0))
            if isinstance(nuggets, list):
                # Filtra entradas válidas (strings não-vazias), limita a 5
                valid = [n for n in nuggets if isinstance(n, str) and n.strip()][:5]
                return valid
    except (json.JSONDecodeError, AttributeError):
        pass

    _log.warn(f"extract_memory_nuggets: parsing falhou para resposta '{raw[:80]}' — retornando []")
    return []


# ---------------------------------------------------------------------------
# 3. identify_edge — detecta relações semânticas entre dois fragmentos
# ---------------------------------------------------------------------------

_EDGE_SYSTEM_PROMPT = """
Você é um detector de relações semânticas entre fragmentos de memória.
Sua função é determinar se um novo fragmento de memória se relaciona com um
fragmento existente, e de que forma.

TIPOS DE RELAÇÃO (retorne APENAS um destes valores):
  "contradicts" — O novo fragmento nega ou contradiz o existente.
                  Ex: Existente: "Use sempre tabs". Novo: "Migramos para spaces"
  "extends"     — O novo fragmento adiciona informação ao existente sem contradizê-lo.
                  Ex: Existente: "O projeto usa React". Novo: "O projeto usa React com TypeScript"
  "updates"     — O novo fragmento substitui/atualiza o existente com informação mais recente.
                  Ex: Existente: "A versão é 2.0". Novo: "Atualizamos para versão 3.0"
  "causes"      — O novo fragmento é uma consequência causal do existente.
                  Ex: Existente: "O banco estava com alta latência". Novo: "Adicionamos índice no campo user_id"
  "fixes"       — O novo fragmento resolve um problema descrito no existente.
                  Ex: Existente: "O bug causa crash no login". Novo: "Corrigimos o bug de login com patch X"
  null          — Não há relação semântica relevante entre os dois fragmentos.

REGRAS:
  - Só aponte uma relação se ela for CLARA e INEQUÍVOCA.
  - Relações fracas ou especulativas → retorne null.
  - Fragmentos sobre tópicos completamente diferentes → retorne null.
  - Quando em dúvida, prefira null.

FORMATO DE SAÍDA: Retorne APENAS um dos valores: "contradicts", "extends", "updates",
"causes", "fixes" ou null (sem aspas para null, sem nenhum texto adicional).

Exemplos de saída válida:
  "extends"
  null
  "contradicts"
""".strip()

# Valores de borda aceitos
_VALID_EDGES = {"contradicts", "extends", "updates", "causes", "fixes"}


def identify_edge(existing_text: str, new_text: str) -> Optional[str]:
    """
    Detecta se existe uma relação semântica entre um fragmento existente e um novo.

    Usado para construir o grafo de memória — permite deprecar memórias contraditas,
    encadear fatos relacionados, e rastrear evoluções de decisões técnicas.

    Args:
        existing_text: Fragmento de memória já armazenado.
        new_text: Novo fragmento candidato a armazenamento.

    Returns:
        str com o tipo de relação ("contradicts", "extends", "updates", "causes", "fixes")
        ou None se não houver relação relevante ou em caso de falha.
    """
    if not existing_text or not new_text:
        return None

    existing_trunc = existing_text.strip()[:500]
    new_trunc = new_text.strip()[:500]

    content = (
        f"Fragmento EXISTENTE:\n{existing_trunc}\n\n"
        f"Fragmento NOVO:\n{new_trunc}"
    )

    raw = _call_nano(_EDGE_SYSTEM_PROMPT, content)
    if raw is None:
        return None

    raw_clean = raw.strip().strip('"').lower()

    if raw_clean in _VALID_EDGES:
        return raw_clean
    if raw_clean in ("null", "none", "nenhuma", "nenhum", "no relation", "sem relação"):
        return None

    _log.warn(f"identify_edge: valor inesperado '{raw[:50]}' — retornando None")
    return None
