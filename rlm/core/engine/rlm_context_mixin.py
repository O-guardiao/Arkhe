"""
RLMContextMixin — preparação de contexto e ambiente para completions.

Responsabilidades extraídas de rlm.py:
- _spawn_completion_context : cria/reusa LMHandler e BaseEnv por completion
- _setup_prompt             : monta message_history inicial com system prompt
- _is_multimodal_content_list  : detecta content parts multimodais (staticmethod)
- _extract_text_from_multimodal: extrai texto de content parts (staticmethod)
- _record_environment_event    : registra eventos de runtime no env (staticmethod)
- _inject_repl_globals         : injeta sub_rlm, browser globals e tools no REPL
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, cast

from rlm.clients import BaseLM, get_client
from rlm.core.engine.lm_handler import LMHandler
from rlm.environments import BaseEnv, SupportsPersistence, get_environment
from rlm.core.engine.sub_rlm import make_sub_rlm_fn, make_sub_rlm_parallel_fn, make_sub_rlm_async_fn
from rlm.plugins.browser import make_browser_globals
from rlm.utils.prompts import (
    RLM_CODE_SYSTEM_PROMPT,
    QueryMetadata,
    build_rlm_system_prompt,
)

PromptInput = str | dict[str, Any] | list[dict[str, Any]]


class RLMContextMixin:
    """
    Mixin com responsabilidades de preparação de contexto e ambiente.

    Todos os atributos referenciados via ``self`` (backend, persistent, etc.)
    são definidos em ``RLM.__init__``. Este mixin é projetado para ser herdado
    exclusivamente pela classe ``RLM``.
    """

    @contextmanager
    def _spawn_completion_context(self, prompt: PromptInput):
        """
        Spawn an LM handler and environment for a single completion call.

        When persistent=True, the environment is reused across calls.
        When persistent=False (default), creates fresh environment each call.

        Fase 12: Quando persistent=True, o lm_handler também é preservado
        entre turnos, eliminando a "zona morta" onde o sistema fica sem cérebro.
        """
        # Fase 12: Reusar lm_handler existente quando persistent=True
        if self.persistent and self._persistent_lm_handler is not None:
            lm_handler = self._persistent_lm_handler
            reused_handler = True
        else:
            # Create client and wrap in handler
            client: BaseLM = get_client(self.backend, self.backend_kwargs or {})

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

            if self.persistent:
                self._persistent_lm_handler = lm_handler

            reused_handler = False

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
            reset_turn_state = getattr(environment, "reset_turn_state", None)
            if callable(reset_turn_state):
                reset_turn_state()
            environment.add_context(repl_context)

            # Cross-turn outcome: injeta resumo conciso do turno anterior
            # para que o LLM tenha visibilidade imediata de erros/resultados
            # sem precisar mergulhar na variável `history` (grande e opaca).
            _outcome = getattr(self, "_last_turn_outcome", None)
            if _outcome and hasattr(environment, "locals"):
                environment.locals["_last_turn_outcome"] = _outcome
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
            environment = get_environment(self.environment_type, env_kwargs)

            # Lacuna 1: Expor memória do environment para que filhos a herdem
            _mem = getattr(environment, "_memory", None)
            if _mem is not None:
                self._shared_memory = _mem

            if self.persistent:
                if not isinstance(environment, SupportsPersistence):
                    raise RuntimeError(
                        f"Environment '{self.environment_type}' does not support persistent mode. "
                        f"Use environment='local' for persistent=True."
                    )
                self._persistent_env = cast(SupportsPersistence, environment)

            # Apply deferred REPL injections from the server pipeline (first turn).
            # On the first turn, _prepare_repl_locals() runs before the env exists,
            # so it stores a closure here for us to apply now.
            _inject_fn = getattr(self, '_pending_repl_injection', None)
            if _inject_fn is not None and hasattr(environment, 'locals'):
                try:
                    _inject_fn(environment.locals)
                except Exception:
                    pass
                self._pending_repl_injection = None

        try:
            yield lm_handler, environment
        finally:
            # Fase 12: Quando persistent=True, NÃO mata o lm_handler.
            # Ele sobrevive entre turnos, eliminando a "zona morta".
            if not self.persistent:
                lm_handler.stop()
            if not self.persistent and hasattr(environment, "cleanup"):
                environment.cleanup()

    def _setup_prompt(self, prompt: PromptInput) -> list[dict[str, Any]]:
        """
        Setup the system prompt for the RLM. Build the initial message history.

        - Se prompt for uma lista de content parts multimodais (image_url / audio),
          armazena em self._multimodal_first_content para ser injetado na primeira
          mensagem de usuário do loop de completion (Phase 11.2: Vision/Audio).
        - Auto-selects codebase prompt when context is a directory path.
        """
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

    def _inject_repl_globals(self, lm_handler: LMHandler, environment: BaseEnv) -> None:
        """Injeta sub_rlm, browser globals e outros tools no REPL."""
        if not hasattr(environment, "globals"):
            return

        execution_policy = getattr(self, "_runtime_execution_policy", None)
        allow_recursion = getattr(execution_policy, "allow_recursion", True)

        if not allow_recursion:
            reason = getattr(execution_policy, "note", "execution policy disabled recursive tools")

            def _blocked_recursive_tool(*_args: Any, **_kwargs: Any) -> Any:
                raise RuntimeError(f"Recursive tools disabled by execution policy: {reason}")

            environment.globals["sub_rlm"] = _blocked_recursive_tool
            environment.globals["rlm_query"] = _blocked_recursive_tool
            environment.globals["sub_rlm_parallel"] = _blocked_recursive_tool
            environment.globals["sub_rlm_parallel_detailed"] = _blocked_recursive_tool
            environment.globals["rlm_query_batched"] = _blocked_recursive_tool
            environment.globals["sub_rlm_async"] = _blocked_recursive_tool
            environment.globals["AsyncHandle"] = __import__(
                "rlm.core.engine.sub_rlm", fromlist=["AsyncHandle"]
            ).AsyncHandle
            environment.globals["SubRLMParallelTaskResult"] = __import__(
                "rlm.core.engine.sub_rlm", fromlist=["SubRLMParallelTaskResult"]
            ).SubRLMParallelTaskResult
            environment.globals["async_bus"] = None
            environment._rlm_scaffold_refs = {
                "sub_rlm": _blocked_recursive_tool,
                "rlm_query": _blocked_recursive_tool,
                "sub_rlm_parallel": _blocked_recursive_tool,
                "sub_rlm_parallel_detailed": _blocked_recursive_tool,
                "rlm_query_batched": _blocked_recursive_tool,
                "sub_rlm_async": _blocked_recursive_tool,
                "async_bus": None,
            }
            environment.globals.update(make_browser_globals())
            return

        _sub_rlm_fn = make_sub_rlm_fn(self)
        environment.globals["sub_rlm"] = _sub_rlm_fn
        _rlm_query_fn = lambda prompt, model=None: _sub_rlm_fn(prompt, model=model)
        environment.globals["rlm_query"] = _rlm_query_fn
        _par, _par_det = make_sub_rlm_parallel_fn(self)
        environment.globals["sub_rlm_parallel"] = _par
        environment.globals["sub_rlm_parallel_detailed"] = _par_det
        environment.globals["rlm_query_batched"] = _par
        environment.globals["SubRLMParallelTaskResult"] = __import__(
            "rlm.core.engine.sub_rlm", fromlist=["SubRLMParallelTaskResult"]
        ).SubRLMParallelTaskResult
        _async_fn = make_sub_rlm_async_fn(self)
        environment.globals["sub_rlm_async"] = _async_fn
        environment.globals["AsyncHandle"] = __import__(
            "rlm.core.engine.sub_rlm", fromlist=["AsyncHandle"]
        ).AsyncHandle
        _async_bus = getattr(self, "_async_bus", None)
        environment.globals["async_bus"] = _async_bus
        environment._rlm_scaffold_refs = {
            "sub_rlm": _sub_rlm_fn,
            "rlm_query": _rlm_query_fn,
            "sub_rlm_parallel": _par,
            "sub_rlm_parallel_detailed": _par_det,
            "rlm_query_batched": _par,
            "sub_rlm_async": _async_fn,
            "async_bus": _async_bus,
        }
        environment.globals.update(make_browser_globals())
