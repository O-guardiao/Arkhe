import os as _os
import queue
import time
from typing import Any, Generator

from rlm.core.engine.lm_handler import LMHandler
from rlm.core.types import (
    ClientBackend,
    EnvironmentType,
    RLMChatCompletion,
    RLMMetadata,
)
from rlm.environments import SupportsPersistence
from rlm.logger import RLMLogger, VerbosePrinter
from rlm.utils.prompts import RLM_SYSTEM_PROMPT
from rlm.utils.rlm_utils import filter_sensitive_keys
from rlm.core.engine.compaction import ContextCompactor, CompactionConfig
from rlm.core.engine.loop_detector import LoopDetector, LoopDetectorConfig
from rlm.utils.token_utils import get_context_limit
from rlm.core.engine.hooks import HookSystem
from rlm.core.orchestration.sibling_bus import SiblingBus
from rlm.core.lifecycle.cancellation import CancellationToken
from rlm.core.lifecycle.disposable import DisposableStore
from rlm.core.engine.control_flow import ReentrancyBarrier


from rlm.core.engine.rlm_context_mixin import RLMContextMixin
from rlm.core.engine.rlm_loop_mixin import RLMLoopMixin
from rlm.core.engine.rlm_mcts_mixin import RLMMCTSMixin
from rlm.core.engine.rlm_persistence_mixin import RLMPersistenceMixin

class RLM(RLMContextMixin, RLMLoopMixin, RLMMCTSMixin, RLMPersistenceMixin):
    """
    Recursive Language Model class that the user instantiates and runs on their tasks.

    Each completion() call spawns its own environment and LM handler, which are
    cleaned up when the call completes.
    """

    def __init__(
        self,
        backend: ClientBackend = "openai",
        backend_kwargs: dict[str, Any] | None = None,
        environment: EnvironmentType = "local",
        environment_kwargs: dict[str, Any] | None = None,
        depth: int = 0,
        max_depth: int = 1,
        max_iterations: int = 30,
        custom_system_prompt: str | None = None,
        interaction_mode: str = "repl",
        other_backends: list[ClientBackend] | None = None,
        other_backend_kwargs: list[dict[str, Any]] | None = None,
        logger: RLMLogger | None = None,
        verbose: bool = False,
        persistent: bool = False,
        custom_tools: dict[str, Any] | None = None,
        custom_sub_tools: dict[str, Any] | None = None,
        event_bus: Any | None = None,
    ):
        """
        Args:
            backend: The backend to use for the RLM.
            backend_kwargs: The kwargs to pass to the backend.
            environment: The environment to use for the RLM.
            environment_kwargs: The kwargs to pass to the environment.
            depth: The current depth of the RLM (0-indexed).
            max_depth: The maximum recursion depth for sub-RLM calls (default 1). Set higher values
                to enable multi-level agent hierarchies. Sub-agents at depth N can spawn agents at depth N+1
                up to max_depth. Set RLM_MAX_DEPTH env var to override the default.
            max_iterations: The maximum number of iterations of the RLM.
            custom_system_prompt: The custom system prompt to use for the RLM.
            interaction_mode: Interaction contract for the loop. 'repl' preserves the
                original behavior. 'text' allows direct plain-text reasoning terminated
                with FINAL(...), without REPL-specific user nudges.
            other_backends: A list of other client backends that the environments can use to make sub-calls.
            other_backend_kwargs: The kwargs to pass to the other client backends (ordered to match other_backends).
            logger: The logger to use for the RLM.
            verbose: Whether to print verbose output in rich to console.
            persistent: If True, reuse the environment across completion() calls for multi-turn conversations.
            custom_tools: Dict of custom functions/tools available in the REPL. Keys are function names,
                values are callable functions or {"tool": callable, "description": "..."} dicts.
                These are injected into the REPL globals (callables) or locals (values).
            custom_sub_tools: Dict of custom tools for sub-agents. If None, inherits from custom_tools.
                Pass an empty dict {} to disable tools for sub-agents.
        """
        # Store config for spawning per-completion
        self.backend = backend
        self.backend_kwargs = backend_kwargs
        # Auto-select sandbox (DockerREPL) when RLM_SANDBOX=1 and explicit environment not overriding
        if _os.environ.get("RLM_SANDBOX", "") == "1" and environment == "local":
            environment = "sandbox"
        self.environment_type = environment
        self.environment_kwargs = (
            environment_kwargs.copy() if environment_kwargs is not None else {}
        )
        # Validate other_backends: support multiple backends for multi-depth routing
        if other_backends is not None:
            if other_backend_kwargs is not None and len(other_backends) != len(other_backend_kwargs):
                raise ValueError(
                    f"other_backends ({len(other_backends)}) and other_backend_kwargs "
                    f"({len(other_backend_kwargs)}) must have the same length."
                )

        self.other_backends = other_backends
        self.other_backend_kwargs = other_backend_kwargs

        # Custom tools: injected into the REPL environment for each completion call
        self.custom_tools = custom_tools
        # Sub-tools: if None, inherit from custom_tools; if {}, no tools for sub-agents
        self.custom_sub_tools = custom_sub_tools if custom_sub_tools is not None else custom_tools

        self.depth = depth
        self.max_depth = max_depth
        self.max_iterations = max_iterations
        self.max_empty_response_retries = 2
        if interaction_mode not in ("repl", "text"):
            raise ValueError(
                f"Unsupported interaction_mode={interaction_mode!r}. Expected 'repl' or 'text'."
            )
        self.interaction_mode = interaction_mode
        self.system_prompt = custom_system_prompt if custom_system_prompt else RLM_SYSTEM_PROMPT
        # skills_context: SIF table injected per-request by api.py to advertise active skills
        # in the system prompt itself. None = standalone mode (no skills).
        self.skills_context: str | None = None
        self.logger = logger
        self.verbose = VerbosePrinter(enabled=verbose)

        # Persistence support
        self.persistent = persistent
        self._persistent_env: SupportsPersistence | None = None

        # Evolution 5: Event bus for streaming observability
        self.event_bus = event_bus

        # Phase 8: Advanced Infrastructure
        self.hooks = HookSystem()
        # Calcula limiar de compaction baseado no contexto real do modelo (85% do limite)
        # Em vez do hardcoded 8000 que compactava 26-42x cedo demais em modelos grandes
        _model_name = (backend_kwargs or {}).get("model_name", "unknown")
        _ctx_limit = get_context_limit(_model_name)
        self.compactor = ContextCompactor(CompactionConfig(max_history_tokens=int(_ctx_limit * 0.85)))
        self.loop_detector = LoopDetector(LoopDetectorConfig())

        # Lazy P2P bus — inicializado na primeira chamada a make_sub_rlm_async_fn
        self._async_bus: SiblingBus | None = None
        self._async_branch_counter: int = 0

        # Supervisor abort signal — setado externamente via Supervisor.execute()
        self._abort_event: Any = None

        # Fase 10: Cancellation Token — substitui _abort_event ad-hoc
        # Consumidores checam self._cancel_token.is_cancelled no loop
        self._cancel_token: CancellationToken = CancellationToken.NONE

        # Fase 10: Resource tracking — coleta disposables para cleanup
        self._disposables = DisposableStore()

        # Fase 10: Reentrancy barrier para compactação
        self._compaction_barrier = ReentrancyBarrier()

        # Fase 12: Persistent LM handler — sobrevive entre turnos quando persistent=True
        self._persistent_lm_handler: LMHandler | None = None

        # Fase 12: Sentinel mode — recursão que dorme em vez de morrer (Solução A)
        self._sentinel_input_queue: queue.Queue[str | None] = queue.Queue()
        self._sentinel_output_queue: queue.Queue[RLMChatCompletion] = queue.Queue()

        # Validate persistence support at initialization
        if self.persistent:
            self._validate_persistent_environment_support()

        # Log metadata if logger is provided
        if self.logger or verbose:
            metadata = RLMMetadata(
                root_model=backend_kwargs.get("model_name", "unknown")
                if backend_kwargs
                else "unknown",
                max_depth=max_depth,
                max_iterations=max_iterations,
                backend=backend,
                backend_kwargs=filter_sensitive_keys(backend_kwargs) if backend_kwargs else {},
                environment_type=environment,
                environment_kwargs=filter_sensitive_keys(environment_kwargs)
                if environment_kwargs
                else {},
                other_backends=other_backends,
            )
            if self.logger:
                self.logger.log_metadata(metadata)
            self.verbose.print_metadata(metadata)

    def completion(
        self,
        prompt: str | dict[str, Any],
        root_prompt: str | None = None,
        mcts_branches: int = 0,
        capture_artifacts: bool = False,
    ) -> RLMChatCompletion:
        """
        Recursive Language Model completion call.

        Args:
            prompt: A single string or dictionary of messages to pass as context to the model.
            root_prompt: Optional root prompt visible to the main LM.
            mcts_branches: Evolution 6.3. If > 0, runs this many parallel branches via MCTS
                before starting the main loop. The best branch namespace is seeded into the
                main REPL. Default 0 = standard behaviour (no MCTS, no extra cost).
            capture_artifacts: Se True, extrai os locals do REPL do filho antes do cleanup
                e os armazena em ``RLMChatCompletion.artifacts``. Usado internamente por
                ``sub_rlm(..., return_artifacts=True)`` para Recursive Primitive Accumulation.
                Default False = comportamento original (sem overhead).
        Returns:
            RLMChatCompletion with the final answer.
        """
        time_start = time.perf_counter()

        if self.depth >= self.max_depth:
            return self._fallback_answer_as_completion(prompt)

        with self._spawn_completion_context(prompt) as (lm_handler, environment):
            self._clear_active_mcts_strategy(environment)
            self._record_environment_event(
                environment,
                "completion.started",
                {
                    "depth": self.depth,
                    "max_iterations": self.max_iterations,
                    "persistent": self.persistent,
                },
            )
            message_history = self._setup_prompt(prompt)
            self._inject_repl_globals(lm_handler, environment)

            if mcts_branches > 0 and self.depth == 0:
                self._run_mcts_preamble(
                    prompt, mcts_branches, lm_handler, environment, message_history,
                )

            self.hooks.trigger("completion.started", context={"prompt": str(prompt)[:100]})

            return self._run_inner_loop(
                message_history=message_history,
                lm_handler=lm_handler,
                environment=environment,
                root_prompt=root_prompt,
                turn_start=time_start,
                prompt_for_result=prompt,
                capture_artifacts=capture_artifacts,
            )

    def completion_stream(
        self,
        prompt: str | dict[str, Any],
        root_prompt: str | None = None,
        mcts_branches: int = 0,
    ) -> Generator[RLMChatCompletion, str | None, None]:
        """
        Generator-based completion: o context manager NÃO fecha entre turnos.

        O environment, lm_handler, variáveis REPL — TUDO sobrevive entre turnos.
        O chamador controla o ciclo de vida via send()/close().

        Uso::

            gen = rlm.completion_stream("Analise X")
            result = next(gen)              # primeiro turno
            print(result.response)
            result = gen.send("E agora?")   # continua no MESMO contexto
            print(result.response)
            gen.send(None)                  # encerra (ou gen.close())

        Yields:
            RLMChatCompletion para cada turno.

        Receives (via send):
            str  — nova mensagem do usuário, continua no mesmo contexto
            None — encerra o stream graciosamente
        """
        if self.depth >= self.max_depth:
            yield self._fallback_answer_as_completion(prompt)
            return

        with self._spawn_completion_context(prompt) as (lm_handler, environment):
            self._clear_active_mcts_strategy(environment)
            message_history = self._setup_prompt(prompt)

            # Injeta sub_rlm, browser globals etc. (mesma lógica do completion())
            self._inject_repl_globals(lm_handler, environment)

            # MCTS se configurado (apenas no primeiro turno)
            if mcts_branches > 0 and self.depth == 0:
                self._run_mcts_preamble(
                    prompt, mcts_branches, lm_handler, environment, message_history,
                )

            self.hooks.trigger("completion.started", context={"prompt": str(prompt)[:100]})

            current_prompt_text = prompt
            turn_number = 0

            while True:
                turn_start = time.perf_counter()
                turn_number += 1

                # Executa o loop recursivo interno para este turno
                result = self._run_inner_loop(
                    message_history=message_history,
                    lm_handler=lm_handler,
                    environment=environment,
                    root_prompt=root_prompt,
                    turn_start=turn_start,
                    prompt_for_result=current_prompt_text,
                )

                # Yield resultado e espera próxima mensagem do usuário
                next_input = yield result

                if next_input is None:
                    # Usuário encerrou o stream
                    self._record_environment_event(
                        environment,
                        "stream.closed",
                        {"turn_number": turn_number, "source": "user"},
                    )
                    return

                # Continua no MESMO contexto — sem destruir nada
                current_prompt_text = next_input
                message_history.append({"role": "user", "content": next_input})
                self.loop_detector.reset()

    # =========================================================================
    # Fase 12 — Solução A: Sentinel Mode (blocking queue)
    # =========================================================================

    def sentinel_completion(
        self,
        prompt: str | dict[str, Any],
        root_prompt: str | None = None,
    ) -> None:
        """
        Modo sentinela: a recursão não morre, fica dormindo esperando input.

        A thread que chama este método fica BLOQUEADA até shutdown.
        Interação via filas:
          - self._sentinel_input_queue.put("nova mensagem")  → enviar
          - self._sentinel_output_queue.get()                → receber resposta
          - self._sentinel_input_queue.put(None)             → shutdown graceful

        Uso (de outra thread)::

            import threading
            t = threading.Thread(target=rlm.sentinel_completion, args=("Olá",))
            t.daemon = True
            t.start()

            result = rlm._sentinel_output_queue.get()      # primeiro turno
            rlm._sentinel_input_queue.put("Próximo passo?")
            result = rlm._sentinel_output_queue.get()      # segundo turno
            rlm._sentinel_input_queue.put(None)            # encerra
        """
        if self.depth >= self.max_depth:
            self._sentinel_output_queue.put(
                self._fallback_answer_as_completion(prompt)
            )
            return

        with self._spawn_completion_context(prompt) as (lm_handler, environment):
            self._clear_active_mcts_strategy(environment)
            message_history = self._setup_prompt(prompt)
            self._inject_repl_globals(lm_handler, environment)

            self.hooks.trigger("completion.started", context={"prompt": str(prompt)[:100]})

            turn_number = 0

            while True:
                turn_start = time.perf_counter()
                turn_number += 1

                result = self._run_inner_loop(
                    message_history=message_history,
                    lm_handler=lm_handler,
                    environment=environment,
                    root_prompt=root_prompt,
                    turn_start=turn_start,
                    prompt_for_result=prompt,
                )

                self._sentinel_output_queue.put(result)

                # Bloqueia até próxima mensagem do humano
                next_input = self._sentinel_input_queue.get()

                if next_input is None:
                    self._record_environment_event(
                        environment,
                        "sentinel.shutdown",
                        {"turn_number": turn_number},
                    )
                    return

                message_history.append({"role": "user", "content": next_input})
                self.loop_detector.reset()
                prompt = next_input

