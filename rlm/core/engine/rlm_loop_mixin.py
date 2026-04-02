"""
RLMLoopMixin — motor de execução iterativa do RLM.

Responsabilidades extraídas de rlm.py:
- _build_recovery_nudge        : gera nudge quando o modelo não produz código/final
- _build_empty_response_nudge  : gera nudge para resposta vazia
- _is_empty_iteration_response : detecta iteração vazia (staticmethod)
- _run_inner_loop              : loop while principal (N iterações), compartilhado por
                                 completion(), completion_stream() e sentinel_completion()
- _fallback_answer_as_completion: wrapper de _fallback_answer → RLMChatCompletion
- _completion_turn             : executa um único turno: LM → parse → execute code
- _default_answer              : resposta de último recurso ao esgotar iterações
- _fallback_answer             : resposta direta quando depth >= max_depth
"""
from __future__ import annotations

import time
from typing import Any

from rlm.clients import BaseLM, get_client
from rlm.core.engine.lm_handler import LMHandler
from rlm.core.types import (
    CodeBlock,
    RLMChatCompletion,
    RLMIteration,
    REPLResult,
)
from rlm.environments import BaseEnv, SupportsPersistence
from rlm.core.optimized.fast import find_code_blocks, find_final_answer
from rlm.utils.parsing import format_iteration
from rlm.utils.prompts import (
    RLM_FORAGING_SYSTEM_PROMPT,
    build_user_prompt,
    build_multimodal_user_prompt,
)

PromptInput = str | dict[str, Any] | list[dict[str, Any]]


class RLMLoopMixin:
    """
    Mixin com o motor de execução iterativa do RLM.

    Todos os atributos referenciados via ``self`` (compactor, loop_detector,
    hooks, verbose, logger, event_bus, etc.) são definidos em ``RLM.__init__``.
    Este mixin é projetado para ser herdado exclusivamente pela classe ``RLM``.
    """

    # =========================================================================
    # Nudge helpers
    # =========================================================================

    def _build_recovery_nudge(self, *, has_code_blocks: bool, has_final: bool) -> dict[str, str] | None:
        if has_code_blocks or has_final:
            return None
        if self.interaction_mode == "text":
            return {
                "role": "user",
                "content": (
                    "Your previous response did not finish the task. Continue the analysis in plain text "
                    "and wrap your complete final answer inside FINAL(...)."
                ),
            }
        return {
            "role": "user",
            "content": (
                "Your previous response contained no ```repl``` code block. "
                "You MUST write executable Python code inside a ```repl``` block "
                "to make progress. Inspect context, run computations, or call "
                "FINAL_VAR(variable_name) to finish."
            ),
        }

    def _build_empty_response_nudge(self) -> dict[str, str]:
        if self.interaction_mode == "text":
            return {
                "role": "user",
                "content": "Your previous response was empty. Reply with actual reasoning or finish with FINAL(...).",
            }
        return {
            "role": "user",
            "content": (
                "Your previous response was empty. Do not stay silent. "
                "Return a ```repl``` block that makes progress or finish with FINAL(...) / FINAL_VAR(...)."
            ),
        }

    @staticmethod
    def _is_empty_iteration_response(response: str | None, *, has_code_blocks: bool, has_final: bool) -> bool:
        if has_code_blocks or has_final:
            return False
        return not (response or "").strip()

    # =========================================================================
    # Loop principal compartilhado
    # =========================================================================

    def _run_inner_loop(
        self,
        *,
        message_history: list[dict[str, Any]],
        lm_handler: LMHandler,
        environment: BaseEnv,
        root_prompt: str | None,
        turn_start: float,
        prompt_for_result: Any,
        capture_artifacts: bool = False,
    ) -> RLMChatCompletion:
        """
        Executa o loop recursivo interno (N iterações).

        Retorna RLMChatCompletion com is_complete=True se FINAL_ANSWER foi encontrado,
        ou is_complete=False se max_iterations esgotou.

        Compartilhado por completion(), completion_stream() e sentinel_completion().
        """
        cancelled_by_environment = False

        iteration_index = 0
        empty_retry_count = 0
        self._loop_detector_critical = False
        while iteration_index < self.max_iterations:
            self._record_environment_event(
                environment,
                "iteration.started",
                {"iteration": iteration_index + 1, "message_count": len(message_history)},
            )

            if self._cancel_token.is_cancelled:
                break

            env_cancel_requested = getattr(environment, "is_cancel_requested", None)
            if callable(env_cancel_requested) and env_cancel_requested():
                cancelled_by_environment = True
                break

            if self._abort_event is not None and self._abort_event.is_set():
                break

            if self._loop_detector_critical:
                break

            # Compaction
            if self.compactor.should_compact(message_history):
                pre_count = len(message_history)

                def _do_compact():
                    self._record_environment_event(
                        environment,
                        "compaction.started",
                        {"iteration": iteration_index + 1, "message_count": pre_count},
                    )
                    compacted = self.compactor.compact(
                        message_history,
                        llm_fn=lambda p: lm_handler.completion(p),
                    )
                    # Bug fix Fase 12: mutação in-place preserva a referência
                    # do chamador (completion_stream / sentinel_completion).
                    # Sem isso, compactação só funciona dentro do turno mas
                    # é perdida entre turnos — context rot progressivo.
                    message_history.clear()
                    message_history.extend(compacted)
                    self._record_environment_event(
                        environment,
                        "compaction.completed",
                        {"iteration": iteration_index + 1, "before": pre_count, "after": len(message_history)},
                    )
                    self.hooks.trigger("compaction.completed", context=self.compactor.get_stats())

                self._compaction_barrier.run_or_skip(_do_compact)

            # Foraging mode
            _foraging_active = (
                isinstance(environment, SupportsPersistence)
                and hasattr(environment, "is_in_foraging_mode")
                and environment.is_in_foraging_mode()
            )

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

            _mm = getattr(self, "_multimodal_first_content", None)
            if iteration_index == 0 and _mm is not None:
                current_prompt = message_history + [
                    build_multimodal_user_prompt(
                        _mm, root_prompt, context_count, history_count,
                        interaction_mode=self.interaction_mode,
                    )
                ]
            else:
                current_prompt = message_history + [
                    build_user_prompt(
                        root_prompt, iteration_index, context_count, history_count,
                        interaction_mode=self.interaction_mode,
                    )
                ]

            if _foraging_active and current_prompt and current_prompt[0].get("role") == "system":
                current_prompt = [{"role": "system", "content": RLM_FORAGING_SYSTEM_PROMPT}] + current_prompt[1:]

            iteration = self._completion_turn(
                prompt=current_prompt,
                lm_handler=lm_handler,
                environment=environment,
            )

            final_answer = find_final_answer(iteration.response, environment=environment)
            iteration.final_answer = final_answer

            if self._is_empty_iteration_response(
                iteration.response,
                has_code_blocks=bool(iteration.code_blocks),
                has_final=final_answer is not None,
            ):
                empty_retry_count += 1
                self._record_environment_event(
                    environment,
                    "iteration.empty_retry",
                    {
                        "iteration": iteration_index + 1,
                        "retry": empty_retry_count,
                    },
                )
                if empty_retry_count <= self.max_empty_response_retries:
                    message_history.append(self._build_empty_response_nudge())
                    continue

            empty_retry_count = 0

            self._record_environment_event(
                environment,
                "iteration.completed",
                {
                    "iteration": iteration_index + 1,
                    "code_blocks": len(iteration.code_blocks),
                    "has_final": final_answer is not None,
                    "iteration_time_s": iteration.iteration_time,
                },
            )

            if self.logger:
                self.logger.log(iteration)

            if self.event_bus is not None:
                self.event_bus.set_iteration(iteration_index)
                self.event_bus.emit("thought", {
                    "iteration": iteration_index + 1,
                    "response_preview": iteration.response[:500] if iteration.response else "",
                    "code_blocks": len(iteration.code_blocks) if iteration.code_blocks else 0,
                    "has_final": final_answer is not None,
                })
                if iteration.code_blocks:
                    for cb in iteration.code_blocks:
                        self.event_bus.emit("repl_exec", {
                            "code": cb.code[:300] if hasattr(cb, 'code') else str(cb)[:300],
                        })

            self.verbose.print_iteration(iteration, iteration_index + 1)

            if final_answer is not None:
                time_end = time.perf_counter()
                usage = lm_handler.get_usage_summary()
                self.verbose.print_final_answer(final_answer)
                self.verbose.print_summary(iteration_index + 1, time_end - turn_start, usage.to_dict())

                if self.event_bus is not None:
                    self.event_bus.emit("final_answer", {
                        "answer_preview": final_answer[:1000],
                        "iterations": iteration_index + 1,
                        "time": time_end - turn_start,
                    })

                if self.persistent and isinstance(environment, SupportsPersistence):
                    environment.add_history(message_history)

                _artifacts = (
                    environment.extract_artifacts()
                    if capture_artifacts and hasattr(environment, "extract_artifacts")
                    else None
                )
                self._last_message_history = list(message_history)
                return RLMChatCompletion(
                    root_model=self.backend_kwargs.get("model_name", "unknown")
                    if self.backend_kwargs else "unknown",
                    prompt=prompt_for_result,
                    response=final_answer,
                    usage_summary=usage,
                    execution_time=time_end - turn_start,
                    artifacts=_artifacts,
                    is_complete=True,
                )

            new_messages = format_iteration(iteration)
            nudge = self._build_recovery_nudge(
                has_code_blocks=bool(iteration.code_blocks),
                has_final=final_answer is not None,
            )
            if nudge is not None:
                new_messages.append(nudge)
            message_history.extend(new_messages)
            iteration_index += 1

        # Esgotou iterações ou foi cancelado
        if cancelled_by_environment:
            time_end = time.perf_counter()
            usage = lm_handler.get_usage_summary()
            self._last_message_history = list(message_history)
            return RLMChatCompletion(
                root_model=self.backend_kwargs.get("model_name", "unknown")
                if self.backend_kwargs else "unknown",
                prompt=prompt_for_result,
                response="[CANCELLED] coordination stop requested",
                usage_summary=usage,
                execution_time=time_end - turn_start,
                is_complete=False,
            )

        time_end = time.perf_counter()
        final_answer = self._default_answer(message_history, lm_handler)
        usage = lm_handler.get_usage_summary()
        self.verbose.print_final_answer(final_answer)

        if self.persistent and isinstance(environment, SupportsPersistence):
            environment.add_history(message_history)

        consumed_iterations = max(1, iteration_index)
        self.verbose.print_summary(consumed_iterations, time_end - turn_start, usage.to_dict())

        self._record_environment_event(
            environment,
            "completion.finalized",
            {
                "iteration": consumed_iterations,
                "elapsed_s": time_end - turn_start,
                "used_default_answer": True,
            },
        )

        _artifacts = (
            environment.extract_artifacts()
            if capture_artifacts and hasattr(environment, "extract_artifacts")
            else None
        )
        self._last_message_history = list(message_history)
        return RLMChatCompletion(
            root_model=self.backend_kwargs.get("model_name", "unknown")
            if self.backend_kwargs else "unknown",
            prompt=prompt_for_result,
            response=final_answer,
            usage_summary=usage,
            execution_time=time_end - turn_start,
            artifacts=_artifacts,
            is_complete=False,
        )

    # =========================================================================
    # Fallback helpers
    # =========================================================================

    def _fallback_answer_as_completion(self, prompt: Any) -> RLMChatCompletion:
        """Wrapper de _fallback_answer que retorna RLMChatCompletion."""
        from rlm.core.types import UsageSummary
        response = self._fallback_answer(prompt)
        return RLMChatCompletion(
            root_model=self.backend_kwargs.get("model_name", "unknown")
            if self.backend_kwargs else "unknown",
            prompt=prompt,
            response=response,
            usage_summary=UsageSummary(model_usage_summaries={}),
            execution_time=0.0,
            is_complete=True,
        )

    def _fallback_answer(self, message: PromptInput) -> str:
        """
        Fallback behavior if the RLM is actually at max depth, and should be treated as an LM.
        """
        client: BaseLM = get_client(self.backend, self.backend_kwargs or {})
        response = client.completion(message)
        return response

    # =========================================================================
    # Single-turn execution
    # =========================================================================

    def _completion_turn(
        self,
        prompt: PromptInput,
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
                    if self.verbose.enabled:
                        print(f"\n[Loop Detector] Critical loop detected: {loop_res.message}. Aborting code execution.")
                    self._loop_detector_critical = True
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
