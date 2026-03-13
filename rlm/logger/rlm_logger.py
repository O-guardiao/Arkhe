"""
Logger de trajetórias completas do RLM.

Este módulo grava em JSONL a execução do agente em nível de iteração. Diferente
do logger estruturado de runtime, o foco aqui é preservar dados suficientes para
análise posterior, replay conceitual, visualização de trajetória e auditoria de
execução.

Cada arquivo contém, nesta ordem lógica:
- uma entrada inicial de ``metadata`` com a configuração do RLM;
- zero ou mais entradas de ``iteration`` com resposta, blocos de código,
  tempos e resposta final quando houver.

Use este logger quando precisar responder perguntas como:
- "o que o agente pensou e executou em cada iteração?"
- "qual bloco de código produziu determinada saída?"
- "qual foi a sequência de subchamadas e tempos durante a execução?"
"""

import json
import os
import threading
import uuid
from datetime import datetime

from rlm.core.types import RLMIteration, RLMMetadata


class RLMLogger:
    """Grava metadados e iterações do RLM em um arquivo JSONL.

    Args:
        log_dir: Diretório de saída onde o arquivo será criado.
        file_name: Prefixo lógico do arquivo. O nome final inclui timestamp e id.

    Notes:
        - O lock interno cobre apenas concorrência entre threads do mesmo
          processo.
        - O arquivo é aberto sob demanda a cada append para reduzir estado vivo.
        - ``close()`` existe por compatibilidade, mas atualmente é um no-op.
    """

    def __init__(self, log_dir: str, file_name: str = "rlm"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        run_id = str(uuid.uuid4())[:8]
        self.log_file_path = os.path.join(log_dir, f"{file_name}_{timestamp}_{run_id}.jsonl")

        self._iteration_count = 0
        self._metadata_logged = False
        self._lock = threading.Lock()

    def _append_entry(self, entry: dict) -> None:
        """Acrescenta uma entrada JSONL com serialização defensiva.

        ``default=repr`` evita falhas caso algum campo contenha tipos não
        serializáveis de forma nativa pelo ``json``.
        """
        with self._lock:
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False, default=repr)
                f.write("\n")

    def close(self) -> None:
        """No-op mantido para compatibilidade de API."""
        return None

    def __enter__(self) -> "RLMLogger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def log_metadata(self, metadata: RLMMetadata):
        """Grava a configuração do RLM no início lógico da trajetória.

        A operação é idempotente por instância: chamadas subsequentes são
        ignoradas após a primeira gravação bem-sucedida.
        """
        if self._metadata_logged:
            return

        entry = {
            "type": "metadata",
            "timestamp": datetime.now().isoformat(),
            **metadata.to_dict(),
        }

        self._append_entry(entry)

        self._metadata_logged = True

    def log(self, iteration: RLMIteration):
        """Grava uma iteração completa do agente no arquivo JSONL."""
        self._iteration_count += 1

        entry = {
            "type": "iteration",
            "iteration": self._iteration_count,
            "timestamp": datetime.now().isoformat(),
            **iteration.to_dict(),
        }

        self._append_entry(entry)

    @property
    def iteration_count(self) -> int:
        """Número de iterações gravadas por esta instância."""
        return self._iteration_count
