"""
Infraestrutura de logging estruturado do runtime do RLM.

Este módulo é o logger operacional do processo. Ele existe para registrar eventos
curtos de runtime, com foco em diagnóstico de servidor, gateways, scheduler,
plugins e supervisor. O objetivo aqui não é guardar a trajetória completa de uma
execução do agente, mas sim produzir eventos legíveis por humano e/ou por
ferramenta de observabilidade.

Escopo deste módulo
-------------------
- Logging por subsistema: session, supervisor, repl, plugin, scheduler, gateway.
- Níveis simples: debug, info, warn e error.
- Redação automática de segredos conhecidos antes de emitir a mensagem.
- Saída opcional em formato humano ou JSON por linha.
- Escrita simultânea para stream e arquivo quando configurado.

O que este módulo nao faz
-------------------------
- Não substitui ``rlm.logger.RLMLogger`` do pacote ``rlm.logger``.
- Não grava a árvore completa de iterações do agente em JSONL rico.
- Não implementa agregação cross-process; o lock aqui é apenas intra-processo.

Relação com ``rlm.logger``
--------------------------
O projeto mantém dois mecanismos de logging com responsabilidades diferentes:

1. ``rlm.core.structured_log.RLMLogger``
    Usado para eventos operacionais curtos, por subsistema.

2. ``rlm.logger.RLMLogger``
    Usado para salvar trajetórias completas de execução do agente em arquivos
    JSONL, com metadados e iterações.

Se a pergunta for "quero analisar a execução completa do agente", use o logger
de trajetórias. Se a pergunta for "quero saber o que o gateway, scheduler ou
supervisor está fazendo agora", use este módulo.

Para código novo, prefira os nomes ``RuntimeLogger`` e ``get_runtime_logger``.
O nome ``RLMLogger`` é mantido aqui apenas por compatibilidade.
"""
import os
import re
import sys
import json
import time
import threading
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, TextIO


# ---------------------------------------------------------------------------
# Log Levels
# ---------------------------------------------------------------------------

LOG_LEVELS = {
    "debug": 10,
    "info": 20,
    "warn": 30,
    "error": 40,
}

# Controlado via env var RLM_LOG_LEVEL
_current_level = LOG_LEVELS.get(
    os.environ.get("RLM_LOG_LEVEL", "info").lower(), 20
)


# ---------------------------------------------------------------------------
# Secret Redaction
# ---------------------------------------------------------------------------

# Patterns que indicam segredos (API keys, tokens, etc.)
_SECRET_PATTERNS = [
    # OpenAI/Anthropic style keys
    re.compile(r'(sk-[a-zA-Z0-9]{20,})'),
    re.compile(r'(key-[a-zA-Z0-9]{20,})'),
    # Generic token patterns
    re.compile(r'([a-zA-Z0-9]{32,64})'),  # Long hex/alphanumeric strings
    # Bearer tokens
    re.compile(r'(Bearer\s+[a-zA-Z0-9._\-]+)', re.IGNORECASE),
]

# Env vars que contêm segredos
_SECRET_ENV_VARS = {
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN",
    "GOOGLE_API_KEY", "AZURE_API_KEY", "HF_TOKEN",
}

# Cache de valores secretos conhecidos
_known_secrets: set[str] = set()


def _init_known_secrets():
    """Carrega segredos conhecidos do ambiente para redação por valor exato.

    A estratégia é complementar à redação por regex. Quando uma variável de
    ambiente sensível está disponível, guardar o valor em memória permite mascarar
    o segredo mesmo que ele apareça fora dos padrões mais comuns.
    """
    for var_name in _SECRET_ENV_VARS:
        value = os.environ.get(var_name, "")
        if value and len(value) > 8:
            _known_secrets.add(value)

_init_known_secrets()


def redact_secrets(text: str) -> str:
    """
    Redige segredos e padrões que se parecem com tokens.

    A função atua em duas camadas:

    1. substitui valores exatos carregados do ambiente;
    2. aplica regex para padrões comuns, como ``sk-*`` e ``Bearer ...``.

    O mascaramento preserva um prefixo e sufixo curtos quando isso ajuda o
    diagnóstico sem expor o valor completo.
    """
    if not text:
        return text

    result = text

    # First, redact known secret values
    for secret in _known_secrets:
        if secret in result:
            masked = secret[:4] + "****" + secret[-4:] if len(secret) > 8 else "****"
            result = result.replace(secret, masked)

    # Redact sk-* and key-* patterns
    result = re.sub(
        r'(sk-|key-)([a-zA-Z0-9]{4})[a-zA-Z0-9]{12,}([a-zA-Z0-9]{4})',
        r'\1\2****\3',
        result,
    )

    # Redact Bearer tokens
    result = re.sub(
        r'(Bearer\s+)([a-zA-Z0-9._\-]{4})[a-zA-Z0-9._\-]{12,}([a-zA-Z0-9._\-]{4})',
        r'\1\2****\3',
        result,
        flags=re.IGNORECASE,
    )

    return result


# ---------------------------------------------------------------------------
# Log Entry
# ---------------------------------------------------------------------------

@dataclass
class LogEntry:
    """Representa uma linha lógica de log estruturado.

    Attributes:
        timestamp: Momento UTC formatado para emissão.
        level: Nível já normalizado em caixa alta.
        subsystem: Nome do subsistema emissor.
        message: Mensagem principal já sanitizada.
        context: Dicionário opcional com campos adicionais serializáveis.
    """
    timestamp: str
    level: str
    subsystem: str
    message: str
    context: dict


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class RLMLogger:
    """
    Logger estruturado leve para o runtime do RLM.

    Esse logger foi desenhado para ser barato de usar e simples de integrar em
    qualquer parte do runtime. Ele não depende do pacote ``logging`` da stdlib e
    evita configuração global. Em troca, mantém uma API pequena e previsível.

    Exemplos:
        >>> log = RLMLogger("supervisor")
        >>> log.info("Execution started", session_id="abc", prompt_len=150)
        >>> log.error("Timeout", session_id="abc", elapsed=120)
        >>> repl_log = log.child("repl")
        >>> repl_log.debug("Executing code block", depth=1)

    Args:
        subsystem: Nome lógico do emissor, usado para filtragem e leitura.
        log_file: Caminho opcional para duplicar a saída em arquivo.
        output: Stream de saída; por padrão usa stderr.
        json_format: Se True, emite uma linha JSON por evento.
    """

    def __init__(
        self,
        subsystem: str,
        log_file: str | None = None,
        output: TextIO | None = None,
        json_format: bool = False,
    ):
        self.subsystem = subsystem
        self.json_format = json_format
        self._output = output or sys.stderr
        self._log_file = log_file
        self._file_handle: TextIO | None = None
        self._lock = threading.Lock()

        if log_file:
            os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
            self._file_handle = open(log_file, "a", encoding="utf-8")

    def debug(self, msg: str, **ctx: Any) -> None:
        """Emite evento de depuração."""
        self._log("debug", msg, ctx)

    def info(self, msg: str, **ctx: Any) -> None:
        """Emite evento informativo."""
        self._log("info", msg, ctx)

    def warn(self, msg: str, **ctx: Any) -> None:
        """Emite evento de aviso."""
        self._log("warn", msg, ctx)

    def error(self, msg: str, **ctx: Any) -> None:
        """Emite evento de erro."""
        self._log("error", msg, ctx)

    def child(self, sub_subsystem: str) -> "RLMLogger":
        """Cria um logger derivado com subsistema hierárquico.

        Exemplo: ``gateway`` -> ``gateway.telegram``.
        O child compartilha a mesma configuração de saída e formato.
        """
        return RLMLogger(
            subsystem=f"{self.subsystem}.{sub_subsystem}",
            log_file=self._log_file,
            output=self._output,
            json_format=self.json_format,
        )

    def close(self) -> None:
        """Fecha o arquivo de log, se houver.

        A instância continua utilizável para saída em stream, mas eventos futuros
        deixam de ser gravados no arquivo após o fechamento.
        """
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None

    # --- Internal ---

    def _log(self, level: str, msg: str, ctx: dict) -> None:
        """Materializa e emite uma entrada de log.

        O método aplica filtragem por nível, redação de segredos, formatação e
        escrita protegida por lock. O lock garante integridade entre threads do
        mesmo processo, mas não resolve concorrência entre processos distintos.
        """
        if LOG_LEVELS.get(level, 0) < _current_level:
            return

        # Redact secrets from message and context values
        safe_msg = redact_secrets(msg)
        safe_ctx = {
            k: redact_secrets(str(v)) if isinstance(v, str) else v
            for k, v in ctx.items()
        }

        entry = LogEntry(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            level=level.upper(),
            subsystem=self.subsystem,
            message=safe_msg,
            context=safe_ctx,
        )

        line = self._format(entry)

        with self._lock:
            try:
                print(line, file=self._output, flush=True)
            except (BrokenPipeError, OSError, ValueError):
                pass

            if self._file_handle:
                try:
                    print(line, file=self._file_handle, flush=True)
                except (OSError, ValueError):
                    pass

    def _format(self, entry: LogEntry) -> str:
        """Formata uma entrada para JSON ou texto humano.

        No modo texto, o formato foi mantido propositalmente compacto para uso em
        terminal. No modo JSON, o resultado é uma linha única adequada para grep,
        ingestão por collector ou parsing posterior.
        """
        if self.json_format:
            return json.dumps({
                "ts": entry.timestamp,
                "level": entry.level,
                "sub": entry.subsystem,
                "msg": entry.message,
                **entry.context,
            }, ensure_ascii=False)

        # Human-readable format
        ctx_str = ""
        if entry.context:
            ctx_parts = [f"{k}={v}" for k, v in entry.context.items()]
            ctx_str = " " + " ".join(ctx_parts)

        level_colors = {
            "DEBUG": "\033[90m",   # Gray
            "INFO": "\033[36m",    # Cyan
            "WARN": "\033[33m",    # Yellow
            "ERROR": "\033[31m",   # Red
        }
        reset = "\033[0m"
        color = level_colors.get(entry.level, "")

        return (
            f"{color}[{entry.timestamp}] "
            f"[{entry.level:5s}] "
            f"[{entry.subsystem}] "
            f"{entry.message}{ctx_str}{reset}"
        )


RuntimeLogger = RLMLogger


# ---------------------------------------------------------------------------
# Global loggers (convenience)
# ---------------------------------------------------------------------------

def get_logger(subsystem: str, **kwargs) -> RLMLogger:
    """Atalho para criar um logger de subsistema.

    Esse helper existe para reduzir ruído nos pontos de chamada e padronizar a
    criação das instâncias do logger estruturado.
    """
    return RLMLogger(subsystem, **kwargs)


def get_runtime_logger(subsystem: str, **kwargs) -> RuntimeLogger:
    """Nome explícito para o logger operacional do runtime.

    O comportamento é idêntico a ``get_logger``. A diferença é apenas semântica:
    reduzir a ambiguidade com ``rlm.logger.RLMLogger`` em código novo.
    """
    return RuntimeLogger(subsystem, **kwargs)


# Pre-configured loggers for common subsystems
session_log = RLMLogger("session")
supervisor_log = RLMLogger("supervisor")
repl_log = RLMLogger("repl")
plugin_log = RLMLogger("plugin")
scheduler_log = RLMLogger("scheduler")
gateway_log = RLMLogger("gateway")


__all__ = [
    "RLMLogger",
    "RuntimeLogger",
    "get_logger",
    "get_runtime_logger",
    "session_log",
    "supervisor_log",
    "repl_log",
    "plugin_log",
    "scheduler_log",
    "gateway_log",
    "redact_secrets",
]
