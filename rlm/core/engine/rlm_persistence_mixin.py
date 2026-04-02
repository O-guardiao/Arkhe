"""
RLMPersistenceMixin — gerenciamento de ciclo de vida e estado persistido.

Responsabilidades extraídas de rlm.py:
- shutdown_persistent                  : desliga LMHandler e env persistentes
- _validate_persistent_environment_support : valida se o env suporta persistence
- _env_supports_persistence            : checa interface SupportsPersistence (staticmethod)
- save_state                           : serializa histórico + REPL checkpoint em disco
- resume_state                         : restaura histórico + REPL checkpoint de disco
- close                                : cleanup do env e lm_handler persistentes
- dispose                              : cleanup unificado (substitui close())
- __enter__ / __exit__                 : suporte ao protocolo `with RLM(...) as rlm:`
"""
from __future__ import annotations

from rlm.environments import BaseEnv, SupportsPersistence
from rlm.utils.rlm_utils import filter_sensitive_keys


class RLMPersistenceMixin:
    """
    Mixin com responsabilidades de ciclo de vida e persistência de estado.

    Todos os atributos referenciados via ``self`` (_persistent_lm_handler,
    _persistent_env, _disposables, hooks, etc.) são definidos em ``RLM.__init__``.
    Este mixin é projetado para ser herdado exclusivamente pela classe ``RLM``.
    """

    def shutdown_persistent(self) -> None:
        """
        Fase 12: Desliga recursos persistentes (lm_handler, environment).

        Deve ser chamado quando a sessão realmente termina (processo saindo,
        usuário desconectou, etc.). Sem isso, o lm_handler ficaria vivo
        indefinidamente em modo persistent.
        """
        if self._persistent_lm_handler is not None:
            try:
                self._persistent_lm_handler.stop()
            except Exception:
                pass
            self._persistent_lm_handler = None
        if self._persistent_env is not None:
            cleanup = getattr(self._persistent_env, "cleanup", None)
            if callable(cleanup):
                try:
                    cleanup()
                except Exception:
                    pass
            self._persistent_env = None

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
        save_checkpoint = getattr(self._persistent_env, "save_checkpoint", None) if self._persistent_env is not None else None
        if callable(save_checkpoint):
            checkpoint_path = os_mod.path.join(state_dir, "repl_checkpoint.json")
            repl_msg = save_checkpoint(checkpoint_path)

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
            load_checkpoint = getattr(self._persistent_env, "load_checkpoint", None)
            if callable(load_checkpoint):
                repl_msg = load_checkpoint(checkpoint_path)
                results.append(repl_msg)

        if not results:
            return "Warning: No state files found to restore."
        return "Resumed: " + " | ".join(results)

    # =========================================================================
    # Cleanup / context manager protocol
    # =========================================================================

    def close(self) -> None:
        """Clean up persistent environment. Call when done with multi-turn conversations."""
        # Fase 12 fix: também para o lm_handler persistente para evitar
        # resource leak se close() for chamado sem shutdown_persistent()
        if self._persistent_lm_handler is not None:
            try:
                self._persistent_lm_handler.stop()
            except Exception:
                pass
            self._persistent_lm_handler = None
        if self._persistent_env is not None:
            cleanup = getattr(self._persistent_env, "cleanup", None)
            if callable(cleanup):
                cleanup()
            self._persistent_env = None

    def dispose(self) -> None:
        """Fase 10: Unified resource cleanup. Substitui close() como contrato principal."""
        self.close()
        self._disposables.dispose()
        self.hooks.clear()

    def __enter__(self) -> "RLMPersistenceMixin":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.dispose()
        return False
