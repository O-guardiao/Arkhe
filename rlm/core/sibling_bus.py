"""
sibling_bus — Comunicação peer-to-peer entre agentes filhos paralelos.

PROBLEMA QUE RESOLVE
--------------------
``sub_rlm_parallel()`` executa N filhos em threads independentes sem nenhuma
comunicação entre eles. Se filho-A descobre que um dado está corrompido,
filho-B continua processando esse dado inutilmente até esgotar seu timeout.

``SiblingBus`` resolve isso: cada filho pode publicar descobertas em tópicos
nomeados e se inscrever para receber mensagens de outros filhos — consenso
emergente sem coordenador central.

GARANTIAS
---------
- Thread-safe: todas as operações são protegidas por Lock interno.
- Non-blocking peek: ``peek()`` nunca bloqueia nenhum filho.
- Timeout no subscribe: ``subscribe()`` nunca bloqueia indefinidamente.
- Isolamento por tópicos: canais são completamente independentes.
- Sem dependência circular: este módulo não importa nenhum outro módulo RLM.

API NO REPL (quando injetada via sub_rlm_parallel)
---------------------------------------------------
  ``sibling_publish(topic, data)``         → publica dado no canal
  ``sibling_subscribe(topic, timeout_s)``  → bloqueia até receber ou timeout → None
  ``sibling_peek(topic)``                  → lê todos msgs sem bloquear
    ``sibling_subscribe_meta(...)``        → consome envelope com sender_id/timestamp
    ``sibling_peek_meta(topic)``           → lê envelopes sem consumir
    ``sibling_drain(topic)``               → esvazia o canal
    ``sibling_control_publish(...)``       → publica sinal broadcast por geração
    ``sibling_control_poll(topic)``        → recebe o último sinal não-visto deste branch
    ``sibling_control_wait(...)``          → aguarda próximo sinal desta geração
    ``sibling_control_peek(topic)``        → inspeciona último sinal broadcast
    ``sibling_bus_stats()``                → telemetria agregada do barramento
    ``sibling_topic_stats(topic)``         → telemetria por tópico
  ``sibling_topics()``                     → lista canais com mensagens

EXEMPLO DE USO NO REPL DE AGENTES PARALELOS
--------------------------------------------
Filho-A (branch 0) detecta anomalia e a anuncia::

    # Filho-A: detecta problema e publica para todos
    sibling_publish("anomaly/found", {"column": 7, "rows": [124, 891]})

Filho-B (branch 1) verifica antes de processar::

    # Filho-B: verifica se há anomalias antes de trabalhar
    anomaly = sibling_subscribe("anomaly/found", timeout_s=2.0)
    if anomaly:
        print(f"Pulando coluna {anomaly['column']} — anomalia reportada por irmão")

Qualquer filho vê todos os anúncios sem consumir::

    todas = sibling_peek("anomaly/found")
    print(f"{len(todas)} anomalias reportadas pelos irmãos")

CONSENSO ENTRE FILHOS (padrão avançado)
----------------------------------------
::

    # Filho-A conclui que dados precisam de normalização
    sibling_publish("consensus/needs_norm", True)

    # Filho-B confirma
    sibling_publish("consensus/needs_norm", True)

    # Filho-C verifica se há consenso antes de continuar
    votos = sibling_peek("consensus/needs_norm")
    if sum(votos) >= 2:
        normalizar = True  # maioria confirmou
"""
from __future__ import annotations

import queue
import sys
import threading
from dataclasses import asdict, dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# SiblingMessage — envelope de dados publicados no bus
# ---------------------------------------------------------------------------

@dataclass
class SiblingMessage:
    """Envelope de uma mensagem publicada no SiblingBus."""

    topic: str
    """Nome do canal em que a mensagem foi publicada."""

    data: Any
    """Payload da mensagem (qualquer objeto Python)."""

    sender_id: int | None = None
    """branch_id do remetente (0-based), preenchido automaticamente."""

    semantic_type: str = "data"
    """Tipo semântico da mensagem para coordenação explícita."""

    timestamp: float = field(
        default_factory=lambda: __import__("time").perf_counter()
    )
    """Timestamp relativo (perf_counter) do momento de publicação."""


@dataclass
class ControlChannel:
    """Canal broadcast por geração para sinais de coordenação.

    Cada publicação substitui o último valor e incrementa a geração.
    Cada receiver vê no máximo uma vez cada geração.
    """

    generation: int = 0
    latest: SiblingMessage | None = None
    seen_generations: dict[str | int, int] = field(default_factory=dict)
    condition: threading.Condition = field(default_factory=threading.Condition)


# ---------------------------------------------------------------------------
# Limites de segurança do SiblingBus
# ---------------------------------------------------------------------------

#: Número máximo de tópicos distintos por instância de bus.
#: Evita que código malicioso crie infinitos canais consumindo memória.
_MAX_CHANNELS: int = 500

#: Número máximo de mensagens pendentes por tópico.
#: Evita acúmulo ilimitado de mensagens em canais ignorados.
_CHANNEL_MAXSIZE: int = 1_000

#: Tamanho máximo do payload de uma mensagem (1 MB).
#: Bloqueia ataques de memory-bomb via publicação de objetos gigantes.
_MAX_PAYLOAD_BYTES: int = 1_048_576

VALID_SIGNAL_TYPES: frozenset[str] = frozenset(
    {"data", "solution_found", "stop", "switch_strategy", "consensus_reached", "custom"}
)

SIGNAL_TOPIC_MAP: dict[str, str] = {
    "solution_found": "control/solution_found",
    "stop": "control/stop",
    "switch_strategy": "control/switch_strategy",
    "consensus_reached": "control/consensus_reached",
}


class SiblingBusError(RuntimeError):
    """Levantada quando o SiblingBus rejeita uma operação por segurança.

    Exemplos: canal cheio, excesso de tópicos, payload acima do limite.
    """


# ---------------------------------------------------------------------------
# SiblingBus — canal pub/sub thread-safe
# ---------------------------------------------------------------------------

class SiblingBus:
    """
    Canal de comunicação thread-safe entre agentes filhos paralelos.

    Criado uma vez por ``make_sub_rlm_parallel_fn()`` e injetado em todos
    os filhos via ``environment_kwargs["_sibling_bus"]``. Cada filho recebe
    as quatro funções ``sibling_*`` no seu REPL globals.

    Internamente usa ``queue.Queue`` por tópico — FIFO, thread-safe, com
    timeout nativo no ``get()``.

    Exemplo de criação e injeção manual::

        bus = SiblingBus()

        # Filho-A recebe funções com sender_id=0
        globals_a = bus.make_repl_functions(sender_id=0)
        env_a.globals.update(globals_a)

        # Filho-B recebe funções com sender_id=1
        globals_b = bus.make_repl_functions(sender_id=1)
        env_b.globals.update(globals_b)
    """

    def __init__(self) -> None:
        self._channels: dict[str, queue.Queue] = {}
        self._control_channels: dict[str, ControlChannel] = {}
        self._lock = threading.Lock()
        self._telemetry_lock = threading.Lock()
        self._observer_lock = threading.Lock()
        self._observers: list[Any] = []
        self._operation_counts: dict[str, int] = {
            "publish": 0,
            "subscribe_hit": 0,
            "subscribe_timeout": 0,
            "peek": 0,
            "drain": 0,
            "topics": 0,
            "control_publish": 0,
            "control_poll_hit": 0,
            "control_poll_miss": 0,
            "control_wait_hit": 0,
            "control_wait_timeout": 0,
        }
        self._topic_operation_counts: dict[str, dict[str, int]] = {}

    def _normalize_topic(self, topic: str) -> str:
        """Normaliza e valida nomes de tópicos."""
        if not isinstance(topic, str):
            raise SiblingBusError("topic deve ser uma string não-vazia")
        normalized = topic.strip()
        if not normalized:
            raise SiblingBusError("topic deve ser uma string não-vazia")
        return normalized

    def _normalize_timeout(self, timeout_s: float) -> float:
        """Converte timeout para float e colapsa valores negativos para 0."""
        try:
            numeric = float(timeout_s)
        except (TypeError, ValueError) as exc:
            raise SiblingBusError("timeout_s deve ser numérico") from exc
        return max(0.0, numeric)

    def _normalize_signal_type(self, signal_type: str | None) -> str:
        normalized = str(signal_type or "data").strip().lower().replace("-", "_")
        if normalized not in VALID_SIGNAL_TYPES:
            raise SiblingBusError(
                f"signal_type inválido: {signal_type!r}. Use um de {sorted(VALID_SIGNAL_TYPES)}"
            )
        return normalized

    def topic_for_signal(self, signal_type: str) -> str:
        normalized = self._normalize_signal_type(signal_type)
        return SIGNAL_TOPIC_MAP.get(normalized, f"control/{normalized}")

    def _get_or_create_channel(self, topic: str) -> queue.Queue:
        """Recupera ou cria um canal respeitando os limites documentados."""
        with self._lock:
            if topic not in self._channels:
                if len(self._channels) + len(self._control_channels) >= _MAX_CHANNELS:
                    raise SiblingBusError(
                        f"Limite de {_MAX_CHANNELS} tópicos por bus atingido. "
                        "Reutilize tópicos existentes."
                    )
                self._channels[topic] = queue.Queue(maxsize=_CHANNEL_MAXSIZE)
            return self._channels[topic]

    def _get_or_create_control_channel(self, topic: str) -> ControlChannel:
        """Recupera ou cria canal de controle broadcast respeitando o cap global."""
        with self._lock:
            if topic not in self._control_channels:
                if len(self._channels) + len(self._control_channels) >= _MAX_CHANNELS:
                    raise SiblingBusError(
                        f"Limite de {_MAX_CHANNELS} tópicos por bus atingido. "
                        "Reutilize tópicos existentes."
                    )
                self._control_channels[topic] = ControlChannel()
            return self._control_channels[topic]

    def _receiver_key(self, receiver_id: int | None) -> str | int:
        """Gera chave estável por receiver para controle broadcast."""
        if receiver_id is not None:
            return receiver_id
        return f"thread:{threading.get_ident()}"

    def _record_operation(self, name: str, topic: str | None = None) -> None:
        """Acumula telemetria leve do barramento."""
        with self._telemetry_lock:
            self._operation_counts[name] = self._operation_counts.get(name, 0) + 1
            if topic is not None:
                topic_stats = self._topic_operation_counts.setdefault(topic, {})
                topic_stats[name] = topic_stats.get(name, 0) + 1

    def add_observer(self, observer: Any) -> None:
        """Registra callback para receber eventos do barramento."""
        if not callable(observer):
            raise TypeError("observer must be callable")
        with self._observer_lock:
            if observer not in self._observers:
                self._observers.append(observer)

    def remove_observer(self, observer: Any) -> None:
        """Remove callback previamente registrado."""
        with self._observer_lock:
            self._observers = [item for item in self._observers if item is not observer]

    def _notify_observers(
        self,
        operation: str,
        *,
        topic: str = "",
        sender_id: int | None = None,
        receiver_id: int | None = None,
        payload: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._observer_lock:
            observers = list(self._observers)
        if not observers:
            return
        event = {
            "operation": operation,
            "topic": topic,
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "payload": payload,
            "metadata": dict(metadata or {}),
            "stats": self.get_stats(),
        }
        for observer in observers:
            try:
                observer(event)
            except Exception:
                pass

    def _format_control_message(
        self, topic: str, channel: ControlChannel, msg: SiblingMessage
    ) -> dict[str, Any]:
        """Converte o último sinal de controle em payload serializável."""
        payload = asdict(msg)
        payload["generation"] = channel.generation
        payload["topic"] = topic
        return payload

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def publish(
        self,
        topic: str,
        data: Any,
        sender_id: int | None = None,
        *,
        semantic_type: str = "data",
    ) -> None:
        """Publica uma mensagem no tópico. Nunca bloqueia.

        Args:
            topic: Nome do canal (qualquer string não-vazia).
            data: Dado a publicar (qualquer objeto Python).
            sender_id: branch_id do remetente, para rastreabilidade.

        Raises:
            SiblingBusError: se o número de tópicos exceder ``_MAX_CHANNELS``,
                se o canal estiver cheio (≥ ``_CHANNEL_MAXSIZE`` mensagens),
                ou se o payload exceder ``_MAX_PAYLOAD_BYTES``.
        """
        topic = self._normalize_topic(topic)
        if sys.getsizeof(data) > _MAX_PAYLOAD_BYTES:
            raise SiblingBusError(
                f"Payload de {sys.getsizeof(data):,} bytes excede o limite de "
                f"{_MAX_PAYLOAD_BYTES:,} bytes por mensagem."
            )
        q = self._get_or_create_channel(topic)
        semantic_type = self._normalize_signal_type(semantic_type)
        msg = SiblingMessage(topic=topic, data=data, sender_id=sender_id, semantic_type=semantic_type)
        try:
            q.put_nowait(msg)
            self._record_operation("publish", topic)
            self._notify_observers(
                "publish",
                topic=topic,
                sender_id=sender_id,
                payload=data,
                metadata={"semantic_type": semantic_type},
            )
        except queue.Full as exc:
            raise SiblingBusError(
                f"Canal '{topic}' está cheio ({_CHANNEL_MAXSIZE} mensagens pendentes). "
                "Consuma mensagens com sibling_subscribe() ou sibling_peek()."
            ) from exc

    def publish_control(
        self,
        topic: str,
        data: Any,
        sender_id: int | None = None,
        *,
        signal_type: str | None = None,
    ) -> int:
        """Publica sinal broadcast por geração para coordenação entre irmãos.

        Diferente dos canais FIFO normais, todos os receivers podem observar a
        mesma geração exatamente uma vez via poll/wait.
        """
        topic = self._normalize_topic(topic)
        if sys.getsizeof(data) > _MAX_PAYLOAD_BYTES:
            raise SiblingBusError(
                f"Payload de {sys.getsizeof(data):,} bytes excede o limite de "
                f"{_MAX_PAYLOAD_BYTES:,} bytes por mensagem."
            )
        normalized_signal = self._normalize_signal_type(signal_type or "custom")
        channel = self._get_or_create_control_channel(topic)
        with channel.condition:
            channel.generation += 1
            channel.latest = SiblingMessage(
                topic=topic,
                data=data,
                sender_id=sender_id,
                semantic_type=normalized_signal,
            )
            generation = channel.generation
            channel.condition.notify_all()
        self._record_operation("control_publish", topic)
        self._notify_observers(
            "control_publish",
            topic=topic,
            sender_id=sender_id,
            payload=data,
            metadata={"generation": generation, "semantic_type": normalized_signal},
        )
        return generation

    def publish_signal(self, signal_type: str, data: Any, sender_id: int | None = None) -> int:
        normalized_signal = self._normalize_signal_type(signal_type)
        topic = self.topic_for_signal(normalized_signal)
        return self.publish_control(topic, data, sender_id=sender_id, signal_type=normalized_signal)

    def subscribe(self, topic: str, timeout_s: float = 5.0) -> Any | None:
        """Bloqueia até receber a próxima mensagem ou timeout.

        Consome a mensagem da fila (não re-enfileira).

        Args:
            topic: Nome do canal.
            timeout_s: Segundos máximos de espera. Passe 0 para não bloquear.

        Returns:
            O ``data`` da primeira mensagem disponível, ou ``None`` se timeout.
        """
        msg = self.subscribe_message(topic, timeout_s=timeout_s)
        return None if msg is None else msg.data

    def subscribe_message(
        self, topic: str, timeout_s: float = 5.0
    ) -> SiblingMessage | None:
        """Consome a próxima mensagem completa do tópico."""
        topic = self._normalize_topic(topic)
        timeout_s = self._normalize_timeout(timeout_s)
        q = self._get_or_create_channel(topic)
        try:
            block = timeout_s > 0
            msg: SiblingMessage = q.get(block=block, timeout=timeout_s if block else None)
            self._record_operation("subscribe_hit", topic)
            self._notify_observers(
                "subscribe_hit",
                topic=topic,
                sender_id=msg.sender_id,
                payload=msg.data,
                metadata={"semantic_type": msg.semantic_type},
            )
            return msg
        except queue.Empty:
            self._record_operation("subscribe_timeout", topic)
            self._notify_observers(
                "subscribe_timeout",
                topic=topic,
                metadata={"timeout_s": timeout_s},
            )
            return None

    def poll_control(self, topic: str, receiver_id: int | None = None) -> dict[str, Any] | None:
        """Obtém o último sinal de controle ainda não visto por este receiver."""
        topic = self._normalize_topic(topic)
        with self._lock:
            channel = self._control_channels.get(topic)
        if channel is None:
            self._record_operation("control_poll_miss", topic)
            return None
        receiver_key = self._receiver_key(receiver_id)
        with channel.condition:
            if channel.latest is None:
                self._record_operation("control_poll_miss", topic)
                self._notify_observers("control_poll_miss", topic=topic, receiver_id=receiver_id)
                return None
            if channel.seen_generations.get(receiver_key, 0) >= channel.generation:
                self._record_operation("control_poll_miss", topic)
                self._notify_observers("control_poll_miss", topic=topic, receiver_id=receiver_id)
                return None
            channel.seen_generations[receiver_key] = channel.generation
            self._record_operation("control_poll_hit", topic)
            payload = self._format_control_message(topic, channel, channel.latest)
            self._notify_observers(
                "control_poll_hit",
                topic=topic,
                sender_id=channel.latest.sender_id,
                receiver_id=receiver_id,
                payload=payload,
                metadata={"generation": channel.generation, "semantic_type": channel.latest.semantic_type},
            )
            return payload

    def wait_control(
        self,
        topic: str,
        receiver_id: int | None = None,
        timeout_s: float = 5.0,
    ) -> dict[str, Any] | None:
        """Aguarda até que uma nova geração de controle esteja disponível."""
        topic = self._normalize_topic(topic)
        timeout_s = self._normalize_timeout(timeout_s)
        channel = self._get_or_create_control_channel(topic)
        receiver_key = self._receiver_key(receiver_id)
        with channel.condition:
            def _has_new_generation() -> bool:
                return (
                    channel.latest is not None
                    and channel.seen_generations.get(receiver_key, 0) < channel.generation
                )

            if not _has_new_generation():
                block = timeout_s > 0
                if block:
                    channel.condition.wait_for(_has_new_generation, timeout=timeout_s)
            if not _has_new_generation():
                self._record_operation("control_wait_timeout", topic)
                self._notify_observers(
                    "control_wait_timeout",
                    topic=topic,
                    receiver_id=receiver_id,
                    metadata={"timeout_s": timeout_s},
                )
                return None
            channel.seen_generations[receiver_key] = channel.generation
            self._record_operation("control_wait_hit", topic)
            assert channel.latest is not None
            payload = self._format_control_message(topic, channel, channel.latest)
            self._notify_observers(
                "control_wait_hit",
                topic=topic,
                sender_id=channel.latest.sender_id,
                receiver_id=receiver_id,
                payload=payload,
                metadata={"generation": channel.generation, "semantic_type": channel.latest.semantic_type},
            )
            return payload

    def peek(self, topic: str) -> list[Any]:
        """Retorna todos os dados disponíveis sem bloquear (non-destructive).

        Drena a fila, coleta os dados e re-enfileira os itens na mesma ordem.

        Args:
            topic: Nome do canal.

        Returns:
            Lista de ``data`` de todas as mensagens disponíveis (pode ser vazia).
        """
        normalized_topic = self._normalize_topic(topic)
        self._record_operation("peek", normalized_topic)
        messages = self.peek_messages(normalized_topic)
        self._notify_observers(
            "peek",
            topic=normalized_topic,
            metadata={"message_count": len(messages)},
        )
        return [m.data for m in messages]

    def peek_messages(self, topic: str) -> list[SiblingMessage]:
        """Retorna snapshot não-destrutivo dos envelopes pendentes do tópico."""
        topic = self._normalize_topic(topic)
        with self._lock:
            q = self._channels.get(topic)
        if q is None:
            return []
        with q.mutex:
            return list(q.queue)

    def drain(self, topic: str) -> list[Any]:
        """Remove e retorna todos os dados do tópico (destrutivo).

        Ao contrário de ``peek()``, NÃO re-enfileira — esvazia o canal.

        Args:
            topic: Nome do canal.

        Returns:
            Lista de dados na ordem FIFO de publicação.
        """
        topic = self._normalize_topic(topic)
        with self._lock:
            q = self._channels.get(topic)
        if q is None:
            return []
        items: list[SiblingMessage] = []
        while True:
            try:
                items.append(q.get_nowait())
            except queue.Empty:
                break
        self._record_operation("drain", topic)
        self._notify_observers(
            "drain",
            topic=topic,
            metadata={"message_count": len(items)},
        )
        return [m.data for m in items]

    def peek_control(self, topic: str) -> dict[str, Any] | None:
        """Inspeciona o último sinal de controle sem marcar consumo."""
        topic = self._normalize_topic(topic)
        with self._lock:
            channel = self._control_channels.get(topic)
        if channel is None:
            return None
        with channel.condition:
            if channel.latest is None:
                return None
            return self._format_control_message(topic, channel, channel.latest)

    def topics(self) -> list[str]:
        """Retorna lista de tópicos que têm pelo menos uma mensagem pendente."""
        self._record_operation("topics")
        with self._lock:
            return [t for t, q in self._channels.items() if q.qsize() > 0]

    def control_topics(self) -> list[str]:
        """Retorna tópicos de controle com ao menos uma geração publicada."""
        with self._lock:
            return [
                topic
                for topic, channel in self._control_channels.items()
                if channel.latest is not None
            ]

    def get_stats(self) -> dict[str, Any]:
        """Snapshot de telemetria para diagnosticar coordenação ruim."""
        with self._telemetry_lock:
            operation_counts = dict(self._operation_counts)
            topic_counts = {
                topic: dict(stats)
                for topic, stats in self._topic_operation_counts.items()
            }
        with self._lock:
            pending_topics = {topic: q.qsize() for topic, q in self._channels.items() if q.qsize() > 0}
            control_generations = {
                topic: channel.generation
                for topic, channel in self._control_channels.items()
                if channel.latest is not None
            }
        return {
            "operation_counts": operation_counts,
            "pending_topics": pending_topics,
            "control_generations": control_generations,
            "topic_counts": topic_counts,
        }

    def get_topic_stats(self, topic: str) -> dict[str, Any]:
        """Telemetria por tópico para descobrir coordenação redundante."""
        topic = self._normalize_topic(topic)
        with self._telemetry_lock:
            topic_counts = dict(self._topic_operation_counts.get(topic, {}))
        with self._lock:
            queue_obj = self._channels.get(topic)
            control_channel = self._control_channels.get(topic)
        queue_size = queue_obj.qsize() if queue_obj is not None else 0
        control_generation = 0
        control_sender_id: int | None = None
        if control_channel is not None:
            with control_channel.condition:
                control_generation = control_channel.generation
                control_sender_id = (
                    control_channel.latest.sender_id if control_channel.latest is not None else None
                )
        return {
            "topic": topic,
            "queue_size": queue_size,
            "control_generation": control_generation,
            "latest_control_sender_id": control_sender_id,
            "operation_counts": topic_counts,
        }

    # ------------------------------------------------------------------
    # REPL injection factory
    # ------------------------------------------------------------------

    def make_repl_functions(self, sender_id: int | None = None) -> dict[str, Any]:
        """Gera o dict de funções para injeção no REPL globals de um filho.

        Cria closures que capturam ``self`` e ``sender_id`` — cada filho
        recebe suas próprias funções mas todas operam no mesmo bus.

        Args:
            sender_id: branch_id do filho. Será incluído automaticamente
                       em toda mensagem publicada por ele.

        Returns:
            ``dict`` com chaves ``sibling_publish``, ``sibling_subscribe``,
            ``sibling_peek``, ``sibling_subscribe_meta``, ``sibling_peek_meta``,
            ``sibling_drain``, ``sibling_control_*``, ``sibling_bus_stats``,
            ``sibling_topic_stats``, ``sibling_topics`` — pronto para
            ``env.globals.update(...)``.

        Exemplo::

            bus = SiblingBus()
            env.globals.update(bus.make_repl_functions(sender_id=2))
        """
        bus = self
        _sid = sender_id

        def sibling_publish(topic: str, data: Any) -> None:
            """Publica ``data`` no canal ``topic`` para irmãos paralelos lerem.

            Args:
                topic: Nome do canal (ex: ``"anomaly/found"``, ``"result/etl"``).
                data: Qualquer objeto Python.

            Uso::
                sibling_publish("etl/done", {"rows": 1024, "cols": 8})
            """
            bus.publish(topic, data, sender_id=_sid)

        def sibling_subscribe(topic: str, timeout_s: float = 5.0) -> Any:
            """Bloqueia até receber mensagem no canal ``topic`` (ou timeout).

            Args:
                topic: Nome do canal.
                timeout_s: Segundos máximos de espera. Default 5s.

            Returns:
                ``data`` da mensagem, ou ``None`` se timeout.

            Uso::
                resultado = sibling_subscribe("etl/done", timeout_s=10.0)
                if resultado:
                    print(f"Irmão processou {resultado['rows']} linhas")
            """
            return bus.subscribe(topic, timeout_s=timeout_s)

        def sibling_subscribe_meta(topic: str, timeout_s: float = 5.0) -> dict[str, Any] | None:
            """Consome a próxima mensagem completa com metadados do remetente."""
            msg = bus.subscribe_message(topic, timeout_s=timeout_s)
            return None if msg is None else asdict(msg)

        def sibling_peek(topic: str) -> list:
            """Retorna todos os dados disponíveis no canal sem bloquear.

            Não consome as mensagens — pode ser chamado múltiplas vezes.

            Args:
                topic: Nome do canal.

            Returns:
                Lista de dados (pode ser vazia).

            Uso::
                votos = sibling_peek("consensus/normalize")
                if sum(votos) >= 2:
                    normalizar = True
            """
            return bus.peek(topic)

        def sibling_peek_meta(topic: str) -> list[dict[str, Any]]:
            """Retorna snapshot dos envelopes pendentes com sender_id e timestamp."""
            return [asdict(msg) for msg in bus.peek_messages(topic)]

        def sibling_drain(topic: str) -> list:
            """Esvazia o canal e retorna os dados acumulados em ordem FIFO."""
            return bus.drain(topic)

        def sibling_control_publish(topic: str, data: Any) -> int:
            """Publica sinal broadcast por geração para todos os irmãos."""
            return bus.publish_control(topic, data, sender_id=_sid)

        def sibling_signal_publish(signal_type: str, data: Any) -> int:
            """Publica um sinal semântico padrão sem depender do nome do tópico."""
            return bus.publish_signal(signal_type, data, sender_id=_sid)

        def sibling_control_poll(topic: str) -> dict[str, Any] | None:
            """Recebe o último sinal de controle ainda não visto por este branch."""
            return bus.poll_control(topic, receiver_id=_sid)

        def sibling_control_wait(topic: str, timeout_s: float = 5.0) -> dict[str, Any] | None:
            """Aguarda a próxima geração de um sinal de controle."""
            return bus.wait_control(topic, receiver_id=_sid, timeout_s=timeout_s)

        def sibling_control_peek(topic: str) -> dict[str, Any] | None:
            """Inspeciona o último sinal de controle sem marcá-lo como lido."""
            return bus.peek_control(topic)

        def sibling_signal_peek(signal_type: str) -> dict[str, Any] | None:
            """Inspeciona o último sinal de um tipo semântico padrão."""
            return bus.peek_control(bus.topic_for_signal(signal_type))

        def sibling_bus_stats() -> dict[str, Any]:
            """Telemetria agregada do barramento para análise de coordenação."""
            return bus.get_stats()

        def sibling_topic_stats(topic: str) -> dict[str, Any]:
            """Telemetria por tópico para descobrir spam ou consumo ruim."""
            return bus.get_topic_stats(topic)

        def sibling_topics() -> list:
            """Lista tópicos com mensagens pendentes.

            Returns:
                Lista de strings com nomes de tópicos não-vazios.

            Uso::
                print("Canais ativos:", sibling_topics())
            """
            return bus.topics()

        return {
            "sibling_publish": sibling_publish,
            "sibling_subscribe": sibling_subscribe,
            "sibling_peek": sibling_peek,
            "sibling_subscribe_meta": sibling_subscribe_meta,
            "sibling_peek_meta": sibling_peek_meta,
            "sibling_drain": sibling_drain,
            "sibling_control_publish": sibling_control_publish,
            "sibling_control_poll": sibling_control_poll,
            "sibling_control_wait": sibling_control_wait,
            "sibling_control_peek": sibling_control_peek,
            "sibling_signal_publish": sibling_signal_publish,
            "sibling_signal_peek": sibling_signal_peek,
            "sibling_bus_stats": sibling_bus_stats,
            "sibling_topic_stats": sibling_topic_stats,
            "sibling_topics": sibling_topics,
        }
