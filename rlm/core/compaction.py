"""
RLM Context Compaction — Fase 8.1

Inspirado em: OpenClaw agents/compaction.ts (465 LOC)

Quando o message_history do RLM cresce além do limiar de tokens, o
Compactor resume mensagens antigas automaticamente, preservando:
- System prompt (sempre intocado)
- Últimas N mensagens (intactas para contexto imediato)
- Resumo das mensagens descartadas (gerado via LLM)

Isso evita que o RLM gaste tokens reenviando contexto irrelevante
e previne erros de context window overflow.
"""
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class CompactionConfig:
    """Configurações do compactor."""
    max_history_tokens: int = 8000      # Ativar compaction acima deste limiar
    summary_ratio: float = 0.3          # Alvo: resumir para ~30% do original
    preserve_last_n: int = 4            # Manter as últimas N mensagens intactas
    preserve_system: bool = True        # Nunca compactar system prompt
    min_messages_to_compact: int = 6    # Mínimo de mensagens para ativar
    summary_max_tokens: int = 500       # Tamanho máximo do resumo
    identifier_preservation: bool = True  # Legado — use identifier_policy
    # Política de preservação de identificadores:
    #   "strict" — preserva UUIDs, paths, nomes de função, URLs
    #   "off"    — resumo livre sem instruções específicas
    #   "custom" — usa identifier_custom_instructions como instrução livre
    identifier_policy: Literal["strict", "off", "custom"] = "strict"
    identifier_custom_instructions: str = ""  # Usado quando policy == "custom"


# ---------------------------------------------------------------------------
# Token Estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """
    Estimativa rápida de tokens via heurística.
    
    Regra: ~4 caracteres por token (média para inglês/português).
    Não é preciso, mas é suficiente para decidir se deve compactar.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estima tokens totais de uma lista de mensagens (suporta multimodal)."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            # Phase 11.2: Multimodal handling
            # Count tokens for text parts, add fixed overhead for images
            for part in content:
                if part.get("type") == "text":
                    total += estimate_tokens(part.get("text", ""))
                elif part.get("type") == "image_url":
                    # Assume ~85 tokens minimum for simple images, can get up to ~1100
                    # but we'll use a conservative heuristic
                    total += 300
        total += 4  # Overhead per message (role, formatting)
    return total


# ---------------------------------------------------------------------------
# Compaction Prompt
# ---------------------------------------------------------------------------

COMPACTION_SYSTEM_PROMPT = """You are a conversation summarizer. Your task is to create a concise summary of the conversation below.

RULES:
- Preserve ALL key facts, decisions, results, and action items
- Preserve ALL identifiers exactly: UUIDs, file paths, function names, API keys (masked), URLs
- Preserve the current state of any ongoing tasks
- Drop small talk, redundant explanations, and repeated information
- Use bullet points for clarity
- Keep the summary to {max_tokens} tokens or less
- Write in the same language as the conversation

CONVERSATION TO SUMMARIZE:
"""

NO_IDENTIFIER_PROMPT = """You are a conversation summarizer. Create a concise summary of the conversation below.
Preserve key facts, decisions, and results. Drop redundant information.
Keep to {max_tokens} tokens or less.

CONVERSATION TO SUMMARIZE:
"""

CUSTOM_IDENTIFIER_PROMPT = """You are a conversation summarizer. Create a concise summary of the conversation below.

SPECIAL INSTRUCTIONS:
{custom_instructions}

Keep to {max_tokens} tokens or less.

CONVERSATION TO SUMMARIZE:
"""


# ---------------------------------------------------------------------------
# Context Compactor
# ---------------------------------------------------------------------------

class ContextCompactor:
    """
    Compacta o histórico de mensagens do RLM quando excede o limiar de tokens.
    
    Fluxo:
    1. Verifica se compaction é necessária
    2. Separa mensagens em: system + preservadas + compactáveis
    3. Gera resumo das compactáveis via LLM
    4. Retorna: [system, {"role": "system", summary}, preservadas]
    
    Usage:
        compactor = ContextCompactor()
        if compactor.should_compact(messages):
            messages = compactor.compact(messages, llm_fn=lm_handler.completion)
    """

    def __init__(self, config: CompactionConfig | None = None):
        self.config = config or CompactionConfig()
        self._compaction_count = 0
        self._total_tokens_saved = 0

    def should_compact(self, messages: list[dict]) -> bool:
        """Check if the message history needs compaction."""
        if len(messages) < self.config.min_messages_to_compact:
            return False
        return estimate_messages_tokens(messages) > self.config.max_history_tokens

    def compact(
        self,
        messages: list[dict],
        llm_fn: Callable[[Any], str],
    ) -> list[dict]:
        """
        Compact message history by summarizing older messages.
        
        Args:
            messages: Full message history.
            llm_fn: Function that takes a prompt (str or list[dict]) and returns a string.
            
        Returns:
            Compacted message history with summary replacing older messages.
        """
        if not self.should_compact(messages):
            return messages

        tokens_before = estimate_messages_tokens(messages)

        # Split messages into segments
        system_msgs, compactable, preserved = self._split_messages(messages)

        if len(compactable) < 2:
            return messages  # Not enough to compact

        # Generate summary of compactable messages
        summary = self._generate_summary(compactable, llm_fn)

        # Build compacted result
        summary_msg = {
            "role": "system",
            "content": (
                f"[CONVERSATION SUMMARY — {len(compactable)} messages compacted]\n\n"
                f"{summary}\n\n"
                f"[END SUMMARY — {self._compaction_count + 1} compaction(s) performed]"
            ),
        }

        result = system_msgs + [summary_msg] + preserved

        # Track stats
        tokens_after = estimate_messages_tokens(result)
        self._compaction_count += 1
        self._total_tokens_saved += max(0, tokens_before - tokens_after)

        return result

    def get_stats(self) -> dict:
        """Get compaction statistics."""
        return {
            "compaction_count": self._compaction_count,
            "total_tokens_saved": self._total_tokens_saved,
        }

    # --- Internal ---

    def _split_messages(
        self, messages: list[dict]
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """
        Split messages into three groups:
        1. System messages (always preserved)
        2. Compactable messages (will be summarized)
        3. Preserved messages (last N, always kept)
        """
        system_msgs = []
        non_system = []

        for msg in messages:
            if self.config.preserve_system and msg.get("role") == "system":
                system_msgs.append(msg)
            else:
                non_system.append(msg)

        # Preserve last N messages
        n = self.config.preserve_last_n
        if len(non_system) <= n:
            return system_msgs, [], non_system

        compactable = non_system[:-n]
        preserved = non_system[-n:]

        return system_msgs, compactable, preserved

    def _generate_summary(
        self, messages: list[dict], llm_fn: Callable[[Any], str]
    ) -> str:
        """Generate a summary of the given messages using the LLM."""
        # Build conversation text
        conversation_text = self._format_messages_for_summary(messages)

        # Escolher template baseado na identifier_policy
        # Suporte legado: se identifier_preservation==False mas policy manteve "strict",
        # atualiza automaticamente para "off".
        policy = self.config.identifier_policy
        if policy == "strict" and not self.config.identifier_preservation:
            policy = "off"  # retrocompatibilidade com código legado

        if policy == "strict":
            template = COMPACTION_SYSTEM_PROMPT
            prompt = template.format(max_tokens=self.config.summary_max_tokens)
        elif policy == "custom":
            prompt = CUSTOM_IDENTIFIER_PROMPT.format(
                custom_instructions=self.config.identifier_custom_instructions or "Preserve todos os detalhes relevantes.",
                max_tokens=self.config.summary_max_tokens,
            )
        else:  # "off"
            prompt = NO_IDENTIFIER_PROMPT.format(max_tokens=self.config.summary_max_tokens)
        prompt += conversation_text

        try:
            summary = llm_fn(prompt)
            return summary.strip()
        except Exception as e:
            # Fallback: simple truncation if LLM fails
            return self._fallback_summary(messages)

    def _format_messages_for_summary(self, messages: list[dict]) -> str:
        """Format messages as text for summarization (safely handles images)."""
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            
            if isinstance(content, str):
                if len(content) > 2000:
                    content = content[:1800] + "\n[...truncated...]"
                parts.append(f"[{role}]: {content}")
            elif isinstance(content, list):
                # Phase 11.2: Strip images and leave markers
                text_blocks = []
                for part in content:
                    if part.get("type") == "text":
                        t = part.get("text", "")
                        if len(t) > 2000:
                            t = t[:1800] + "\n[...truncated...]"
                        text_blocks.append(t)
                    elif part.get("type") == "image_url":
                        text_blocks.append("[IMAGEM OMITIDA PARA ECONOMIA DE TOKENS]")
                
                combined = "\n".join(text_blocks)
                parts.append(f"[{role}]: {combined}")
                
        return "\n\n".join(parts)

    def _fallback_summary(self, messages: list[dict]) -> str:
        """Fallback summary when LLM summarization fails."""
        n_msgs = len(messages)
        roles = {}
        for msg in messages:
            r = msg.get("role", "unknown")
            roles[r] = roles.get(r, 0) + 1

        summary_parts = [f"Previous conversation ({n_msgs} messages):"]
        for role, count in roles.items():
            summary_parts.append(f"- {count} {role} message(s)")

        # Extract key identifiers from the conversation (handling multimodal strings)
        text_chunks = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                text_chunks.append(content)
            elif isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        text_chunks.append(part.get("text", ""))
        
        all_text = " ".join(text_chunks)
        # Find file paths
        paths = set(re.findall(r'[/\\][\w/\\.-]+\.\w+', all_text))
        if paths:
            summary_parts.append(f"- Files mentioned: {', '.join(list(paths)[:10])}")

        return "\n".join(summary_parts)
