import hashlib
import time
from contextlib import contextmanager
from typing import Any

from rlm.clients import BaseLM, get_client
from rlm.core.lm_handler import LMHandler
from rlm.core.types import (
    ClientBackend,
    CodeBlock,
    EnvironmentType,
    REPLResult,
    RLMChatCompletion,
    RLMIteration,
    RLMMetadata,
)
from rlm.environments import BaseEnv, SupportsPersistence, get_environment
from rlm.logger import RLMLogger, VerbosePrinter
from rlm.core.fast import find_code_blocks, find_final_answer
from rlm.utils.parsing import format_iteration
from rlm.utils.prompts import (
    RLM_SYSTEM_PROMPT,
    RLM_CODE_SYSTEM_PROMPT,
    RLM_FORAGING_SYSTEM_PROMPT,
    QueryMetadata,
    build_rlm_system_prompt,
    build_user_prompt,
    build_multimodal_user_prompt,
)
from rlm.utils.rlm_utils import filter_sensitive_keys
from rlm.core.mcts import MCTSOrchestrator, ProgramArchive, evolutionary_branch_search
from rlm.core.compaction import ContextCompactor, CompactionConfig
from rlm.core.loop_detector import LoopDetector, LoopDetectorConfig
from rlm.utils.token_utils import get_context_limit
from rlm.core.hooks import HookSystem
from rlm.core.sibling_bus import SiblingBus
from rlm.core.sub_rlm import make_sub_rlm_fn, make_sub_rlm_parallel_fn, make_sub_rlm_async_fn
from rlm.plugins.browser import make_browser_globals
from rlm.core.cancellation import CancellationToken
from rlm.core.disposable import DisposableStore, adapt_closeable
from rlm.core.control_flow import ReentrancyBarrier


class RLM:
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
        import os as _os
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

    @contextmanager
    def _spawn_completion_context(self, prompt: str | dict[str, Any]):
        """
        Spawn an LM handler and environment for a single completion call.

        When persistent=True, the environment is reused across calls.
        When persistent=False (default), creates fresh environment each call.
        """
        # Create client and wrap in handler
        client: BaseLM = get_client(self.backend, self.backend_kwargs)

        # Create other_backend_client if provided (for depth=1 routing)
        other_backend_client: BaseLM | None = None
        if self.other_backends and self.other_backend_kwargs:
            other_backend_client = get_client(self.other_backends[0], self.other_backend_kwargs[0])

        lm_handler = LMHandler(client, other_backend_client=other_backend_client)

        # Register other clients to be available as sub-call options (by model name)
        if self.other_backends and self.other_backend_kwargs:
            for backend, kwargs in zip(self.other_backends, self.other_backend_kwargs, strict=True):
                other_client: BaseLM = get_client(backend, kwargs)
                lm_handler.register_client(other_client.model_name, other_client)

        lm_handler.start()

        # Environment: reuse if persistent, otherwise create fresh
        if self.persistent and self._persistent_env is not None:
            environment = self._persistent_env
            # Defensive check: ensure environment supports persistence methods
            if not self._env_supports_persistence(environment):
                raise RuntimeError(
                    f"Persistent environment of type '{type(environment).__name__}' does not "
                    f"implement required methods (update_handler_address, add_context, get_context_count). "
                    f"This should have been caught at initialization."
                )
            environment.update_handler_address((lm_handler.host, lm_handler.port))
            # Phase 11.2: Para prompts multimodais, usa texto extraído como REPL context
            # (a imagem/áudio vai direto no message history, não no contexto REPL)
            repl_context = (
                self._extract_text_from_multimodal(prompt)
                if self._is_multimodal_content_list(prompt)
                else prompt
            )
            environment.add_context(repl_context)
        else:
            env_kwargs = self.environment_kwargs.copy()
            env_kwargs["lm_handler_address"] = (lm_handler.host, lm_handler.port)
            env_kwargs["event_bus"] = self.event_bus
            # Phase 11.2: Para prompts multimodais, usa texto extraído como REPL context
            env_kwargs["context_payload"] = (
                self._extract_text_from_multimodal(prompt)
                if self._is_multimodal_content_list(prompt)
                else prompt
            )
            env_kwargs["depth"] = self.depth + 1  # Environment depth is RLM depth + 1
            # Pass custom tools to the environment
            if self.custom_tools is not None:
                env_kwargs["custom_tools"] = self.custom_tools
            if self.custom_sub_tools is not None:
                env_kwargs["custom_sub_tools"] = self.custom_sub_tools
            environment: BaseEnv = get_environment(self.environment_type, env_kwargs)

            # Lacuna 1: Expor memória do environment para que filhos a herdem
            _mem = getattr(environment, "_memory", None)
            if _mem is not None:
                self._shared_memory = _mem

            if self.persistent:
                self._persistent_env = environment

        try:
            yield lm_handler, environment
        finally:
            lm_handler.stop()
            if not self.persistent and hasattr(environment, "cleanup"):
                environment.cleanup()

    def _setup_prompt(self, prompt: str | list | dict[str, Any]) -> list[dict[str, Any]]:
        """
        Setup the system prompt for the RLM. Build the initial message history.

        - Se prompt for uma lista de content parts multimodais (image_url / audio),
          armazena em self._multimodal_first_content para ser injetado na primeira
          mensagem de usuário do loop de completion (Phase 11.2: Vision/Audio).
        - Auto-selects codebase prompt when context is a directory path.
        """
        import os

        system_prompt = self.system_prompt

        # Phase 11.2: Detect multimodal content parts (OpenAI vision/audio format).
        # Content parts have "type" key; message history has "role" key.
        if self._is_multimodal_content_list(prompt):
            self._multimodal_first_content = list(prompt)
            metadata = QueryMetadata(prompt)
            message_history = build_rlm_system_prompt(
                system_prompt=system_prompt,
                query_metadata=metadata,
                skills_context=self.skills_context,
                custom_tools=self.custom_tools,
            )
            return message_history

        # Non-multimodal path: reset flag
        self._multimodal_first_content = None

        # Auto-detect codebase mode: if prompt is a directory path, use code prompt
        if isinstance(prompt, str) and os.path.isdir(prompt):
            system_prompt = RLM_CODE_SYSTEM_PROMPT

        metadata = QueryMetadata(prompt)
        message_history = build_rlm_system_prompt(
            system_prompt=system_prompt,
            query_metadata=metadata,
            skills_context=self.skills_context,
            custom_tools=self.custom_tools,
        )
        return message_history

    @staticmethod
    def _is_multimodal_content_list(prompt: object) -> bool:
        """
        Retorna True se prompt for uma lista de OpenAI content parts
        (ex: [{"type": "image_url", ...}, {"type": "text", ...}]).

        Distingue de um message history (dicts com chave "role").
        """
        if not isinstance(prompt, list) or not prompt:
            return False
        first = prompt[0]
        if not isinstance(first, dict):
            return False
        # Content parts têm "type"; message history têm "role"
        return "type" in first and "role" not in first

    @staticmethod
    def _extract_text_from_multimodal(parts: list[dict]) -> str:
        """
        Extrai representação textual de uma lista de content parts multimodais.
        Usado para popular o REPL `context` com uma descrição legível.
        """
        texts: list[str] = []
        image_count = 0
        audio_count = 0
        for part in parts:
            part_type = part.get("type", "")
            if part_type == "text":
                texts.append(part.get("text", ""))
            elif part_type == "image_url":
                image_count += 1
            elif part_type in ("audio", "audio_url", "input_audio"):
                audio_count += 1
        if image_count:
            texts.append(f"[{image_count} imagem(ns) fornecida(s) no contexto visual]")
        if audio_count:
            texts.append(f"[{audio_count} arquivo(s) de áudio fornecido(s)]")
        return " ".join(texts)

    @staticmethod
    def _record_environment_event(
        environment: BaseEnv,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        origin: str = "rlm",
    ) -> None:
        recorder = getattr(environment, "record_runtime_event", None)
        if callable(recorder):
            try:
                recorder(event_type, data, origin=origin)
            except Exception:
                pass

    @staticmethod
    def _build_mcts_evaluation_stages(environment: BaseEnv) -> list[Any]:
        from rlm.core.mcts import EvaluationStage

        evaluator_candidates: list[tuple[str, Any]] = []
        env_globals = getattr(environment, "globals", {}) or {}
        env_locals = getattr(environment, "locals", {}) or {}
        for name in ("evaluate", "score_candidate", "evaluate_candidate"):
            fn = env_locals.get(name) or env_globals.get(name)
            if callable(fn):
                evaluator_candidates.append((name, fn))

        stages: list[EvaluationStage] = []
        for evaluator_name, evaluator_fn in evaluator_candidates:
            def _invoke(snapshot: dict[str, Any], fn=evaluator_fn, name=evaluator_name) -> float:
                try:
                    value = fn(snapshot)
                except TypeError:
                    value = fn(
                        snapshot["code"],
                        snapshot["stdout"],
                        snapshot["stderr"],
                        snapshot["locals"],
                    )
                if isinstance(value, dict):
                    if name in value:
                        return float(value[name])
                    if len(value) == 1:
                        return float(next(iter(value.values())))
                    raise ValueError(
                        f"Evaluator '{name}' returned multiple metrics; provide a wrapper that returns one scalar"
                    )
                return float(value)

            stages.append(EvaluationStage(name=evaluator_name, evaluator=_invoke))
        return stages

    @staticmethod
    def _attach_mcts_archive(
        environment: BaseEnv,
        archive_key: str,
        archive: ProgramArchive,
        round_history: list[dict[str, Any]],
        best_branch: Any,
    ) -> None:
        attach_context = getattr(environment, "globals", {}).get("attach_context") if hasattr(environment, "globals") else None
        if not callable(attach_context):
            return
        try:
            attach_context(
                f"mcts_archive_{archive_key}",
                {
                    "archive_key": archive_key,
                    "archive_size": archive.size(),
                    "best_branch": {
                        "branch_id": best_branch.branch_id,
                        "total_score": best_branch.total_score,
                        "aggregated_metrics": dict(best_branch.aggregated_metrics),
                        "final_code": best_branch.final_code,
                        "strategy_name": best_branch.strategy_name,
                        "strategy": dict(best_branch.strategy or {}),
                    },
                    "archive_entries": [
                        {
                            "branch_id": branch.branch_id,
                            "total_score": branch.total_score,
                            "aggregated_metrics": dict(branch.aggregated_metrics),
                            "pruned_reason": branch.pruned_reason,
                            "final_code": branch.final_code,
                            "strategy_name": branch.strategy_name,
                            "strategy": dict(branch.strategy or {}),
                        }
                        for branch in archive.sample(6)
                    ],
                    "round_history": round_history,
                },
                kind="mcts_archive",
                metadata={"archive_key": archive_key, "rounds": len(round_history)},
            )
        except Exception:
            pass

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
                before starting the main loop. The best branch's namespace is seeded into the
                main REPL. Default 0 = standard behaviour (no MCTS, no extra cost).
            capture_artifacts: Se True, extrai os locals do REPL do filho antes do cleanup
                e os armazena em ``RLMChatCompletion.artifacts``. Usado internamente por
                ``sub_rlm(..., return_artifacts=True)`` para Recursive Primitive Accumulation.
                Default False = comportamento original (sem overhead).
        Returns:
            A final answer as a string.
        """
        time_start = time.perf_counter()

        # If we're at max depth, the RLM is an LM, so we fallback to the regular LM.
        if self.depth >= self.max_depth:
            return self._fallback_answer(prompt)

        with self._spawn_completion_context(prompt) as (lm_handler, environment):
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
            cancelled_by_environment = False

            # ── Phase 9.3: Inject sub_rlm into REPL globals ────────────────────
            # sub_rlm(task)               → execução serial (1 filho de cada vez)
            # sub_rlm_parallel(tasks)     → N filhos em paralelo, retorna list[str]
            # sub_rlm_parallel_detailed   → idem, retorna list[SubRLMParallelTaskResult]
            # ── Phase 9.4 (browser): web_get, web_post, web_scrape, web_search, web_download
            if hasattr(environment, "globals"):
                _sub_rlm_fn = make_sub_rlm_fn(self)
                environment.globals["sub_rlm"] = _sub_rlm_fn
                # Aliases compatíveis com o sistema original (rlm_query / rlm_query_batched)
                _rlm_query_fn = lambda prompt, model=None: _sub_rlm_fn(prompt)
                environment.globals["rlm_query"] = _rlm_query_fn
                _par, _par_det = make_sub_rlm_parallel_fn(self)
                environment.globals["sub_rlm_parallel"]          = _par
                environment.globals["sub_rlm_parallel_detailed"] = _par_det
                environment.globals["rlm_query_batched"]         = _par  # alias compatível
                environment.globals["SubRLMParallelTaskResult"]  = __import__(
                    "rlm.core.sub_rlm", fromlist=["SubRLMParallelTaskResult"]
                ).SubRLMParallelTaskResult
                # sub_rlm_async: fire-and-forget, retorna AsyncHandle sem bloquear
                _async_fn = make_sub_rlm_async_fn(self)
                environment.globals["sub_rlm_async"] = _async_fn
                environment.globals["AsyncHandle"] = __import__(
                    "rlm.core.sub_rlm", fromlist=["AsyncHandle"]
                ).AsyncHandle
                # async_bus: SiblingBus persistente no pai — exposto no REPL para
                # que o LLM publique comandos que todos os filhos async recebem.
                # O bus nasce em make_sub_rlm_async_fn() e vive no objeto RLM pai.
                # Pai escreve:  async_bus.publish("control/stop", True)
                # Filho lê:     sibling_subscribe("control/stop", timeout_s=1.0)
                _async_bus = getattr(self, "_async_bus", None)
                environment.globals["async_bus"] = _async_bus
                # Register refs so _restore_scaffold() can restore them after each exec
                environment._rlm_scaffold_refs = {
                    "sub_rlm": _sub_rlm_fn,
                    "rlm_query": _rlm_query_fn,
                    "sub_rlm_parallel": _par,
                    "sub_rlm_parallel_detailed": _par_det,
                    "rlm_query_batched": _par,
                    "sub_rlm_async": _async_fn,
                    "async_bus": _async_bus,
                }
                # ── Phase 9.4: Browser globals — disponíveis no REPL sem import ──
                environment.globals.update(make_browser_globals())
            # ── End Phase 9.3 / 9.4 ───────────────────────────────────────────

            # ── Evolution 6.3: MCTS Branching ─────────────────────────────────
            if mcts_branches > 0 and self.depth == 0:
                _mcts_prompt = prompt if isinstance(prompt, str) else str(prompt)
                archive_key = hashlib.sha1(_mcts_prompt.strip().encode("utf-8", errors="ignore")).hexdigest()[:16]
                archive_store = getattr(self, "_mcts_archives", None)
                if archive_store is None:
                    archive_store = {}
                    self._mcts_archives = archive_store
                archive = archive_store.setdefault(archive_key, ProgramArchive())
                orchestrator = MCTSOrchestrator(
                    lm_handler_address=lm_handler.address if hasattr(lm_handler, "address") else None,
                    branches=mcts_branches,
                    max_depth=2,
                    evaluation_stages=self._build_mcts_evaluation_stages(environment),
                    event_bus=self.event_bus,
                )

                def _direct_llm(p: str) -> str:
                    return lm_handler.completion(p)

                try:
                    search_result = evolutionary_branch_search(
                        _mcts_prompt,
                        mcts_branches,
                        _direct_llm,
                        orchestrator,
                        rounds=2,
                        elite_count=min(2, mcts_branches),
                        archive=archive,
                    )
                    best_branch = search_result["best_branch"]
                    round_history = search_result["history"]
                    self._attach_mcts_archive(environment, archive_key, archive, round_history, best_branch)

                    # Seed winner's namespace into the main environment
                    if hasattr(environment, "locals") and best_branch.repl_locals:
                        for k, v in best_branch.repl_locals.items():
                            if not k.startswith("_"):
                                environment.locals[k] = v

                    if self.event_bus:
                        self.event_bus.emit("mcts_complete", {
                            "best_branch": best_branch.branch_id,
                            "best_score": best_branch.total_score,
                            "seeded_vars": list(best_branch.repl_locals.keys()),
                            "rounds": len(round_history),
                            "winner_metrics": best_branch.aggregated_metrics,
                            "archive_key": archive_key,
                            "archive_size": archive.size(),
                        })

                    # Inject a summary of what MCTS found into the first user message
                    best_round = max(round_history, key=lambda item: item["best_score"])
                    mcts_note = (
                        f"\n[MCTS PRE-EXPLORATION: Ran {mcts_branches} parallel branches across {len(round_history)} rounds. "
                        f"Best branch (score={best_branch.total_score:.1f}, round={best_round['round']}, strategy={best_branch.strategy_name or 'unknown'}) found: "
                        f"{best_branch.final_code[:200]}]"
                    )
                    message_history[-1]["content"] += mcts_note

                except Exception as _mcts_err:
                    # MCTS failure is non-fatal — continue with normal greedy loop
                    if self.verbose:
                        print(f"[MCTS] Pre-exploration failed: {_mcts_err}. Continuing normally.")
            # ── End MCTS ───────────────────────────────────────────────────────

            self.hooks.trigger("completion.started", context={"prompt": str(prompt)[:100]})

            for i in range(self.max_iterations):
                self._record_environment_event(
                    environment,
                    "iteration.started",
                    {
                        "iteration": i + 1,
                        "message_count": len(message_history),
                    },
                )
                # Fase 10: CancellationToken check (composicional, substitui abort_event)
                if self._cancel_token.is_cancelled:
                    if getattr(self, 'verbose', None):
                        print(f"[CancellationToken] Cancelled: {self._cancel_token.reason}")
                    break

                env_cancel_requested = getattr(environment, "is_cancel_requested", None)
                if callable(env_cancel_requested) and env_cancel_requested():
                    cancelled_by_environment = True
                    self._record_environment_event(
                        environment,
                        "completion.cancelled",
                        {"iteration": i + 1, "source": "environment"},
                    )
                    break

                # Supervisor hook: check for external abort signal (legacy compat)
                if self._abort_event is not None and self._abort_event.is_set():
                    if getattr(self, 'verbose', None):
                        print("[Supervisor] Abort signal received. Terminating RLM loop.")
                    break

                # Phase 8 + Fase 10: Context Compaction com ReentrancyBarrier
                if self.compactor.should_compact(message_history):
                    pre_compaction_count = len(message_history)

                    def _do_compact():
                        nonlocal message_history
                        self._record_environment_event(
                            environment,
                            "compaction.started",
                            {
                                "iteration": i + 1,
                                "message_count": pre_compaction_count,
                            },
                        )
                        message_history = self.compactor.compact(
                            message_history,
                            llm_fn=lambda p: lm_handler.completion(p)
                        )
                        self._record_environment_event(
                            environment,
                            "compaction.completed",
                            {
                                "iteration": i + 1,
                                "before": pre_compaction_count,
                                "after": len(message_history),
                            },
                        )
                        self.hooks.trigger("compaction.completed", context=self.compactor.get_stats())
                        if getattr(self, 'verbose', None):
                            print("[Compaction] Reduced message history context window.")
                    self._compaction_barrier.run_or_skip(_do_compact)

                # Evolution 6.1: Check if Foraging Mode is active and swap system prompt
                _foraging_active = (
                    isinstance(environment, SupportsPersistence)
                    and hasattr(environment, "is_in_foraging_mode")
                    and environment.is_in_foraging_mode()
                )

                # Current prompt = message history + additional prompt suffix
                context_count = (
                    environment.get_context_count()
                    if isinstance(environment, SupportsPersistence)
                    else 1
                )
                history_count = (
                    environment.get_history_count()
                    if isinstance(environment, SupportsPersistence)
                    else 0
                )

                # Phase 11.2: Primeira iteração com conteúdo multimodal:
                # injeta image_url/audio parts + instrução de ação como única mensagem
                # de usuário para que o LLM de visão "veja" a imagem diretamente.
                _mm = getattr(self, "_multimodal_first_content", None)
                if i == 0 and _mm is not None:
                    current_prompt = message_history + [
                        build_multimodal_user_prompt(
                            _mm, root_prompt, context_count, history_count
                        )
                    ]
                else:
                    current_prompt = message_history + [
                        build_user_prompt(root_prompt, i, context_count, history_count)
                    ]

                # Evolution 6.1: Swap system message when in Foraging Mode
                if _foraging_active and current_prompt and current_prompt[0].get("role") == "system":
                    current_prompt = [{"role": "system", "content": RLM_FORAGING_SYSTEM_PROMPT}] + current_prompt[1:]

                iteration: RLMIteration = self._completion_turn(
                    prompt=current_prompt,
                    lm_handler=lm_handler,
                    environment=environment,
                )

                # Check if RLM is done and has a final answer.
                final_answer = find_final_answer(iteration.response, environment=environment)
                iteration.final_answer = final_answer

                self._record_environment_event(
                    environment,
                    "iteration.completed",
                    {
                        "iteration": i + 1,
                        "code_blocks": len(iteration.code_blocks),
                        "has_final": final_answer is not None,
                        "iteration_time_s": iteration.iteration_time,
                    },
                )

                # If logger is used, log the iteration.
                if self.logger:
                    self.logger.log(iteration)

                # Evolution 5: Emit event for streaming observers
                if self.event_bus is not None:
                    self.event_bus.set_iteration(i)
                    self.event_bus.emit("thought", {
                        "iteration": i + 1,
                        "response_preview": iteration.response[:500] if iteration.response else "",
                        "code_blocks": len(iteration.code_blocks) if iteration.code_blocks else 0,
                        "has_final": final_answer is not None,
                    })
                    if iteration.code_blocks:
                        for cb in iteration.code_blocks:
                            self.event_bus.emit("repl_exec", {
                                "code": cb.code[:300] if hasattr(cb, 'code') else str(cb)[:300],
                            })

                # Verbose output for this iteration
                self.verbose.print_iteration(iteration, i + 1)

                if final_answer is not None:
                    time_end = time.perf_counter()
                    usage = lm_handler.get_usage_summary()
                    self.verbose.print_final_answer(final_answer)
                    self.verbose.print_summary(i + 1, time_end - time_start, usage.to_dict())

                    # Evolution 5: Emit final answer event
                    if self.event_bus is not None:
                        self.event_bus.emit("final_answer", {
                            "answer_preview": final_answer[:1000],
                            "iterations": i + 1,
                            "time": time_end - time_start,
                        })

                    self._record_environment_event(
                        environment,
                        "completion.finalized",
                        {
                            "iteration": i + 1,
                            "elapsed_s": time_end - time_start,
                            "used_default_answer": False,
                        },
                    )

                    # Store message history in persistent environment
                    if self.persistent and isinstance(environment, SupportsPersistence):
                        environment.add_history(message_history)

                    # Recursive Primitive Accumulation: capture REPL locals before cleanup
                    _artifacts = (
                        environment.extract_artifacts()
                        if capture_artifacts and hasattr(environment, "extract_artifacts")
                        else None
                    )
                    return RLMChatCompletion(
                        root_model=self.backend_kwargs.get("model_name", "unknown")
                        if self.backend_kwargs
                        else "unknown",
                        prompt=prompt,
                        response=final_answer,
                        usage_summary=usage,
                        execution_time=time_end - time_start,
                        artifacts=_artifacts,
                    )

                # Format the iteration for the next prompt.
                new_messages = format_iteration(iteration)

                # Update message history with the new messages.
                message_history.extend(new_messages)

            if cancelled_by_environment:
                time_end = time.perf_counter()
                usage = lm_handler.get_usage_summary()
                cancelled_answer = "[CANCELLED] coordination stop requested"
                return RLMChatCompletion(
                    root_model=self.backend_kwargs.get("model_name", "unknown")
                    if self.backend_kwargs
                    else "unknown",
                    prompt=prompt,
                    response=cancelled_answer,
                    usage_summary=usage,
                    execution_time=time_end - time_start,
                    artifacts=None,
                )

            # Default behavior: we run out of iterations, provide one final answer
            time_end = time.perf_counter()
            final_answer = self._default_answer(message_history, lm_handler)
            usage = lm_handler.get_usage_summary()
            self.verbose.print_final_answer(final_answer)
            self.verbose.print_summary(self.max_iterations, time_end - time_start, usage.to_dict())

            self._record_environment_event(
                environment,
                "completion.finalized",
                {
                    "iteration": self.max_iterations,
                    "elapsed_s": time_end - time_start,
                    "used_default_answer": True,
                },
            )

            # Store message history in persistent environment
            if self.persistent and isinstance(environment, SupportsPersistence):
                environment.add_history(message_history)

            # Recursive Primitive Accumulation: capture REPL locals before cleanup
            _artifacts = (
                environment.extract_artifacts()
                if capture_artifacts and hasattr(environment, "extract_artifacts")
                else None
            )
            return RLMChatCompletion(
                root_model=self.backend_kwargs.get("model_name", "unknown")
                if self.backend_kwargs
                else "unknown",
                prompt=prompt,
                response=final_answer,
                usage_summary=usage,
                execution_time=time_end - time_start,
                artifacts=_artifacts,
            )

    def _completion_turn(
        self,
        prompt: str | dict[str, Any],
        lm_handler: LMHandler,
        environment: BaseEnv,
    ) -> RLMIteration:
        """
        Perform a single iteration of the RLM, including prompting the model
        and code execution + tool execution.
        """
        iter_start = time.perf_counter()
        response = lm_handler.completion(prompt)
        code_block_strs = find_code_blocks(response)
        code_blocks = []

        self._record_environment_event(
            environment,
            "model.response_received",
            {
                "response_chars": len(response),
                "code_blocks": len(code_block_strs),
            },
        )

        for code_block_str in code_block_strs:
            code_result: REPLResult = environment.execute_code(code_block_str)
            code_blocks.append(CodeBlock(code=code_block_str, result=code_result))

            # Phase 8: Loop Detection
            self.loop_detector.record(
                code=code_block_str,
                output=str(code_result.stdout),
                is_error=bool(code_result.stderr)
            )
            loop_res = self.loop_detector.check()
            if loop_res.stuck:
                self.hooks.trigger("loop_detector.stuck", context={"result": loop_res.message})
                if loop_res.level == "critical":
                    if getattr(self, 'verbose', None):
                        print(f"\\n[Loop Detector] Critical loop detected: {loop_res.message}. Aborting code execution.")
                    if self._abort_event is not None:
                        self._abort_event.set()
                    break

        iteration_time = time.perf_counter() - iter_start
        return RLMIteration(
            prompt=prompt,
            response=response,
            code_blocks=code_blocks,
            iteration_time=iteration_time,
        )

    def _default_answer(self, message_history: list[dict[str, Any]], lm_handler: LMHandler) -> str:
        """
        Default behavior if the RLM runs out of iterations and does not find a final answer.
        It will take the message history, and try to generate a final answer from it.
        """
        current_prompt = message_history + [
            {
                "role": "assistant",
                "content": "Please provide a final answer to the user's question based on the information provided.",
            }
        ]
        response = lm_handler.completion(current_prompt)

        if self.logger:
            self.logger.log(
                RLMIteration(
                    prompt=current_prompt,
                    response=response,
                    final_answer=response,
                    code_blocks=[],
                )
            )

        return response

    def _fallback_answer(self, message: str | dict[str, Any]) -> str:
        """
        Fallback behavior if the RLM is actually at max depth, and should be treated as an LM.
        """
        client: BaseLM = get_client(self.backend, self.backend_kwargs)
        response = client.completion(message)
        return response

    def _validate_persistent_environment_support(self) -> None:
        """
        Validate that the configured environment type supports persistent mode.

        Persistent mode requires environments to implement:
        - update_handler_address(address): Update LM handler address between calls
        - add_context(payload, index): Add new context for multi-turn conversations
        - get_context_count(): Return the number of loaded contexts

        Currently only 'local' (LocalREPL) supports these methods.

        Raises:
            ValueError: If the environment type does not support persistent mode.
        """
        # Known environments that support persistence
        persistent_supported_environments = {"local"}

        if self.environment_type not in persistent_supported_environments:
            raise ValueError(
                f"persistent=True is not supported for environment type '{self.environment_type}'. "
                f"Persistent mode requires environments that implement update_handler_address(), "
                f"add_context(), and get_context_count(). "
                f"Supported environments: {sorted(persistent_supported_environments)}"
            )

    @staticmethod
    def _env_supports_persistence(env: BaseEnv) -> bool:
        """Check if an environment instance supports persistent mode methods."""
        return isinstance(env, SupportsPersistence)

    # =========================================================================
    # Evolution 3: High-Level State Persistence (Sleep/Wake)
    # =========================================================================

    def save_state(self, state_dir: str) -> str:
        """Save the entire RLM state (conversation + REPL) to a directory.

        This allows killing the process and resuming later exactly where 
        the analysis left off.

        Args:
            state_dir: Directory to save state files.

        Returns:
            Status message.
        """
        import json as json_mod
        import os as os_mod

        os_mod.makedirs(state_dir, exist_ok=True)

        # Save conversation history
        if hasattr(self, "_last_message_history") and self._last_message_history:
            history_path = os_mod.path.join(state_dir, "conversation_history.json")
            # Filter out non-serializable content from messages
            serializable_history = []
            for msg in self._last_message_history:
                try:
                    json_mod.dumps(msg)
                    serializable_history.append(msg)
                except (TypeError, ValueError):
                    serializable_history.append({
                        "role": msg.get("role", "unknown"),
                        "content": str(msg.get("content", "")),
                    })
            with open(history_path, "w", encoding="utf-8") as f:
                json_mod.dump(serializable_history, f, indent=2, ensure_ascii=False)

        # Save RLM config
        config = {
            "backend": self.backend,
            "backend_kwargs": filter_sensitive_keys(self.backend_kwargs) if self.backend_kwargs else {},
            "environment_type": self.environment_type,
            "depth": self.depth,
            "max_depth": self.max_depth,
            "max_iterations": self.max_iterations,
            "persistent": self.persistent,
        }
        config_path = os_mod.path.join(state_dir, "rlm_config.json")
        with open(config_path, "w") as f:
            json_mod.dump(config, f, indent=2)

        # Save REPL checkpoint if persistent env exists
        repl_msg = ""
        if self._persistent_env is not None and hasattr(self._persistent_env, "save_checkpoint"):
            checkpoint_path = os_mod.path.join(state_dir, "repl_checkpoint.json")
            repl_msg = self._persistent_env.save_checkpoint(checkpoint_path)

        return f"State saved to {state_dir}. REPL: {repl_msg}"

    def resume_state(self, state_dir: str) -> str:
        """Resume RLM state from a previously saved directory.

        Loads the conversation history and REPL checkpoint.

        Args:
            state_dir: Directory containing saved state files.

        Returns:
            Status message with details of what was restored.
        """
        import json as json_mod
        import os as os_mod

        if not os_mod.path.isdir(state_dir):
            return f"Error: State directory not found: {state_dir}"

        results = []

        # Load conversation history
        history_path = os_mod.path.join(state_dir, "conversation_history.json")
        if os_mod.path.exists(history_path):
            with open(history_path, "r", encoding="utf-8") as f:
                self._last_message_history = json_mod.load(f)
            results.append(f"Conversation history restored ({len(self._last_message_history)} messages)")

        # Load REPL checkpoint
        checkpoint_path = os_mod.path.join(state_dir, "repl_checkpoint.json")
        if os_mod.path.exists(checkpoint_path) and self._persistent_env is not None:
            if hasattr(self._persistent_env, "load_checkpoint"):
                repl_msg = self._persistent_env.load_checkpoint(checkpoint_path)
                results.append(repl_msg)

        if not results:
            return "Warning: No state files found to restore."
        return "Resumed: " + " | ".join(results)

    def close(self) -> None:
        """Clean up persistent environment. Call when done with multi-turn conversations."""
        if self._persistent_env is not None:
            if hasattr(self._persistent_env, "cleanup"):
                self._persistent_env.cleanup()
            self._persistent_env = None

    def dispose(self) -> None:
        """Fase 10: Unified resource cleanup. Substitui close() como contrato principal."""
        self.close()
        self._disposables.dispose()
        self.hooks.clear()

    def __enter__(self) -> "RLM":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.dispose()
        return False
