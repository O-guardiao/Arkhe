"""Tests to verify all imports are correct and non-conflicting."""

import importlib
import sys
from collections import defaultdict

import pytest


class TestTopLevelImports:
    """Test top-level package imports."""

    def test_rlm_import(self):
        """Test that main rlm package can be imported."""
        import rlm

        assert hasattr(rlm, "RLM")
        assert "RLM" in rlm.__all__

    def test_rlm_rlm_import(self):
        """Test that RLM class can be imported from rlm."""
        from rlm import RLM

        assert RLM is not None

    def test_rlm_core_rlm_import(self):
        """Test that RLM can be imported from rlm.core.engine.rlm."""
        from rlm.core.engine.rlm import RLM

        assert RLM is not None


class TestClientImports:
    """Test client module imports."""

    def test_clients_module_import(self):
        """Test that clients module can be imported."""
        import rlm.clients

        assert hasattr(rlm.clients, "get_client")
        assert hasattr(rlm.clients, "BaseLM")

    def test_base_lm_import(self):
        """Test BaseLM import."""
        from rlm.clients.base_lm import BaseLM

        assert BaseLM is not None

    def test_openai_client_import(self):
        """Test OpenAIClient import."""
        pytest.importorskip("openai")
        from rlm.clients.openai import OpenAIClient

        assert OpenAIClient is not None

    def test_anthropic_client_import(self):
        """Test AnthropicClient import."""
        pytest.importorskip("anthropic")
        from rlm.clients.anthropic import AnthropicClient

        assert AnthropicClient is not None

    def test_portkey_client_import(self):
        """Test PortkeyClient import."""
        pytest.importorskip("portkey_ai")
        from rlm.clients.portkey import PortkeyClient

        assert PortkeyClient is not None

    def test_litellm_client_import(self):
        """Test LiteLLMClient import."""
        pytest.importorskip("litellm")
        from rlm.clients.litellm import LiteLLMClient

        assert LiteLLMClient is not None

    def test_get_client_function(self):
        """Test get_client function import."""
        from rlm.clients import get_client

        assert callable(get_client)


class TestCoreImports:
    """Test core module imports."""

    def test_core_types_import(self):
        """Test core types imports."""
        from rlm.core.types import (
            ClientBackend,
            CodeBlock,
            ModelUsageSummary,
            QueryMetadata,
            REPLResult,
            RLMIteration,
            RLMMetadata,
            UsageSummary,
        )

        assert ClientBackend is not None
        assert CodeBlock is not None
        assert ModelUsageSummary is not None
        assert QueryMetadata is not None
        assert REPLResult is not None
        assert RLMIteration is not None
        assert RLMMetadata is not None
        assert UsageSummary is not None

    def test_core_rlm_import(self):
        """Test core RLM import."""
        from rlm.core.engine.rlm import RLM

        assert RLM is not None

    def test_core_engine_package_import(self):
        """Test the engine package public API and lazy exports."""
        import rlm.core.engine as engine
        from rlm.core.engine.comms_utils import LMRequest, LMResponse
        from rlm.core.engine.enums import PermissionMode
        from rlm.core.engine.hooks import HookEvent, HookSystem
        from rlm.core.engine.lm_handler import LMHandler
        from rlm.core.engine.permission_policy import PermissionPolicy, PolicyAction, PolicyRule
        from rlm.core.engine.rlm import RLM
        from rlm.core.engine.runtime_workbench import AgentContext, TaskEntry, TaskLedger
        from rlm.core.engine.session_journal import JournalEntry, Role, SessionJournal
        from rlm.core.engine.sub_rlm import (
            AsyncHandle,
            SubRLMArtifactResult,
            SubRLMCallable,
            SubRLMDepthError,
            SubRLMError,
            SubRLMParallelCallable,
            SubRLMParallelDetailedResults,
            SubRLMParallelTaskResult,
            SubRLMResult,
            SubRLMTimeoutError,
            make_sub_rlm_async_fn,
            make_sub_rlm_fn,
            make_sub_rlm_parallel_fn,
        )

        assert engine.RLM is RLM
        assert engine.LMHandler is LMHandler
        assert engine.LMRequest is LMRequest
        assert engine.LMResponse is LMResponse
        assert engine.HookEvent is HookEvent
        assert engine.HookSystem is HookSystem
        assert engine.PermissionMode is PermissionMode
        assert engine.PermissionPolicy is PermissionPolicy
        assert engine.PolicyAction is PolicyAction
        assert engine.PolicyRule is PolicyRule
        assert engine.SessionJournal is SessionJournal
        assert engine.JournalEntry is JournalEntry
        assert engine.Role is Role
        assert engine.AgentContext is AgentContext
        assert engine.TaskEntry is TaskEntry
        assert engine.TaskLedger is TaskLedger
        assert engine.AsyncHandle is AsyncHandle
        assert engine.SubRLMArtifactResult is SubRLMArtifactResult
        assert engine.SubRLMCallable is SubRLMCallable
        assert engine.SubRLMDepthError is SubRLMDepthError
        assert engine.SubRLMError is SubRLMError
        assert engine.SubRLMParallelCallable is SubRLMParallelCallable
        assert engine.SubRLMParallelDetailedResults is SubRLMParallelDetailedResults
        assert engine.SubRLMParallelTaskResult is SubRLMParallelTaskResult
        assert engine.SubRLMResult is SubRLMResult
        assert engine.SubRLMTimeoutError is SubRLMTimeoutError
        assert engine.make_sub_rlm_fn is make_sub_rlm_fn
        assert engine.make_sub_rlm_async_fn is make_sub_rlm_async_fn
        assert engine.make_sub_rlm_parallel_fn is make_sub_rlm_parallel_fn

    def test_core_engine_lazy_submodule_access(self):
        """Test lazy submodule access on the engine package."""
        import rlm.core.engine as engine

        assert engine.hooks.HookSystem is not None
        assert engine.comms_utils.socket_send is not None
        assert engine.runtime_workbench.AgentContext is not None
        assert engine.sub_rlm.make_sub_rlm_fn is not None

    def test_core_memory_package_import(self):
        """Test the memory package public API and lazy exports."""
        import rlm.core.memory as memory
        from rlm.core.memory.embedding_backend import EmbeddingBackend, MockEmbeddingBackend
        from rlm.core.memory.hybrid_search import HybridSearcher, keyword_score, rrf
        from rlm.core.memory.knowledge_base import GlobalKnowledgeBase
        from rlm.core.memory.memory_budget import (
            IMPORTANCE_WEIGHT,
            MEMORY_BUDGET_PCT,
            RECENCY_HALF_LIFE_DAYS,
            RECENCY_WEIGHT,
            RELEVANCE_WEIGHT,
            RETRIEVAL_LIMIT,
            SCORE_THRESHOLD,
            TOKENS_PER_CHAR,
            estimate_tokens_from_text,
            format_memory_block,
            inject_memory_with_budget,
            score_tripartite,
        )
        from rlm.core.memory.memory_hot_cache import (
            MemorySessionCache,
            evict_cache,
            get_or_create_cache,
            registry_size,
        )
        from rlm.core.memory.memory_manager import MultiVectorMemory, cosine_similarity
        from rlm.core.memory.memory_types import (
            EmbeddingModel,
            EmbeddingProvider,
            EmbeddingRequest,
            EmbeddingResult,
            MemoryEntry,
            MemoryManagerConfig,
            SearchQuery,
            SearchResult,
            Vector,
        )
        from rlm.core.memory.mmr import mmr_rerank
        from rlm.core.memory.temporal_decay import age_in_days, apply_temporal_decay
        from rlm.core.memory.vector_utils import cosine_similarity_dense, dot_product, normalize_vector

        assert memory.Vector is Vector
        assert memory.EmbeddingProvider is EmbeddingProvider
        assert memory.EmbeddingModel is EmbeddingModel
        assert memory.EmbeddingRequest is EmbeddingRequest
        assert memory.EmbeddingResult is EmbeddingResult
        assert memory.MemoryEntry is MemoryEntry
        assert memory.SearchQuery is SearchQuery
        assert memory.SearchResult is SearchResult
        assert memory.MemoryManagerConfig is MemoryManagerConfig
        assert memory.cosine_similarity_dense is cosine_similarity_dense
        assert memory.normalize_vector is normalize_vector
        assert memory.dot_product is dot_product
        assert memory.mmr_rerank is mmr_rerank
        assert memory.age_in_days is age_in_days
        assert memory.apply_temporal_decay is apply_temporal_decay
        assert memory.EmbeddingBackend is EmbeddingBackend
        assert memory.MockEmbeddingBackend is MockEmbeddingBackend
        assert memory.keyword_score is keyword_score
        assert memory.rrf is rrf
        assert memory.HybridSearcher is HybridSearcher
        assert memory.MultiVectorMemory is MultiVectorMemory
        assert memory.cosine_similarity is cosine_similarity
        assert memory.GlobalKnowledgeBase is GlobalKnowledgeBase
        assert memory.MEMORY_BUDGET_PCT == MEMORY_BUDGET_PCT
        assert memory.SCORE_THRESHOLD == SCORE_THRESHOLD
        assert memory.IMPORTANCE_WEIGHT == IMPORTANCE_WEIGHT
        assert memory.RECENCY_WEIGHT == RECENCY_WEIGHT
        assert memory.RELEVANCE_WEIGHT == RELEVANCE_WEIGHT
        assert memory.RETRIEVAL_LIMIT == RETRIEVAL_LIMIT
        assert memory.TOKENS_PER_CHAR == TOKENS_PER_CHAR
        assert memory.RECENCY_HALF_LIFE_DAYS == RECENCY_HALF_LIFE_DAYS
        assert memory.score_tripartite is score_tripartite
        assert memory.inject_memory_with_budget is inject_memory_with_budget
        assert memory.estimate_tokens_from_text is estimate_tokens_from_text
        assert memory.format_memory_block is format_memory_block
        assert memory.MemorySessionCache is MemorySessionCache
        assert memory.get_or_create_cache is get_or_create_cache
        assert memory.evict_cache is evict_cache
        assert memory.registry_size is registry_size

    def test_core_memory_lazy_submodule_access(self):
        """Test lazy submodule access on the memory package."""
        import rlm.core.memory as memory

        assert memory.memory_manager.MultiVectorMemory is not None
        assert memory.knowledge_base.GlobalKnowledgeBase is not None
        assert memory.knowledge_consolidator.consolidate_session is not None
        assert memory.memory_budget.inject_memory_with_budget is not None
        assert memory.memory_hot_cache.get_or_create_cache is not None
        assert memory.semantic_retrieval.SemanticTextIndex is not None

    def test_core_integrations_package_import(self):
        """Test the integrations package public API and lazy exports."""
        import rlm.core.integrations as integrations
        from rlm.core.integrations.mcp_client import (
            BaseSyncMCPClient,
            SyncMCPClient,
            SyncMCPHttpClient,
        )
        from rlm.core.integrations.obsidian_bridge import ObsidianBridge
        from rlm.core.integrations.obsidian_mirror import (
            export_all_to_vault,
            export_document_to_vault,
            import_conceitos_from_vault,
        )

        assert integrations.BaseSyncMCPClient is BaseSyncMCPClient
        assert integrations.SyncMCPClient is SyncMCPClient
        assert integrations.SyncMCPHttpClient is SyncMCPHttpClient
        assert integrations.ObsidianBridge is ObsidianBridge
        assert integrations.export_document_to_vault is export_document_to_vault
        assert integrations.export_all_to_vault is export_all_to_vault
        assert integrations.import_conceitos_from_vault is import_conceitos_from_vault

    def test_core_integrations_lazy_submodule_access(self):
        """Test lazy submodule access on the integrations package."""
        import rlm.core.integrations as integrations

        assert integrations.mcp_client.SyncMCPClient is not None
        assert integrations.obsidian_bridge.ObsidianBridge is not None
        assert integrations.obsidian_mirror.export_document_to_vault is not None

    def test_core_optimized_package_import(self):
        """Test the optimized package public API and lazy exports."""
        import rlm.core.optimized as optimized
        from rlm.core.optimized.benchmark import benchmark
        from rlm.core.optimized.opt_types import LMRequest, LMResponse
        from rlm.core.optimized.parsing import (
            compute_hash,
            find_code_blocks,
            find_final_answer,
            format_iteration_rs,
        )
        from rlm.core.optimized.wire import (
            JSON_BACKEND,
            MAX_FRAME_SIZE,
            json_dumps,
            json_loads,
            send_lm_request,
            send_lm_request_batched,
            socket_recv,
            socket_request,
            socket_send,
        )

        assert optimized.JSON_BACKEND == JSON_BACKEND
        assert optimized.MAX_FRAME_SIZE == MAX_FRAME_SIZE
        assert optimized.LMRequest is LMRequest
        assert optimized.LMResponse is LMResponse
        assert callable(benchmark)
        assert "benchmark" not in optimized.__all__
        assert optimized.compute_hash is compute_hash
        assert optimized.find_code_blocks is find_code_blocks
        assert optimized.find_final_answer is find_final_answer
        assert optimized.format_iteration_rs is format_iteration_rs
        assert optimized.json_dumps is json_dumps
        assert optimized.json_loads is json_loads
        assert optimized.send_lm_request is send_lm_request
        assert optimized.send_lm_request_batched is send_lm_request_batched
        assert optimized.socket_recv is socket_recv
        assert optimized.socket_request is socket_request
        assert optimized.socket_send is socket_send

    def test_core_optimized_lazy_submodule_access(self):
        """Test lazy submodule access on the optimized package."""
        import rlm.core.optimized as optimized

        assert optimized.benchmark.benchmark is not None
        assert optimized.fast.find_code_blocks is not None
        assert optimized.opt_types.LMRequest is not None
        assert optimized.parsing.find_final_answer is not None
        assert optimized.wire.socket_send is not None

    def test_core_observability_package_import(self):
        """Test the observability package public API and lazy exports."""
        import rlm.core.observability as observability
        from rlm.core.observability.operator_surface import (
            apply_operator_command,
            build_activity_payload,
            build_runtime_snapshot,
            dispatch_operator_prompt,
        )
        from rlm.core.observability.turn_telemetry import TurnTelemetry, TurnTelemetryStore

        assert observability.TurnTelemetry is TurnTelemetry
        assert observability.TurnTelemetryStore is TurnTelemetryStore
        assert observability.build_runtime_snapshot is build_runtime_snapshot
        assert observability.build_activity_payload is build_activity_payload
        assert observability.apply_operator_command is apply_operator_command
        assert observability.dispatch_operator_prompt is dispatch_operator_prompt

    def test_core_observability_lazy_submodule_access(self):
        """Test lazy submodule access on the observability package."""
        import rlm.core.observability as observability

        assert observability.turn_telemetry.TurnTelemetryStore is not None
        assert observability.operator_surface.build_runtime_snapshot is not None

    def test_core_lm_handler_import(self):
        """Test LMHandler import."""
        from rlm.core.engine.lm_handler import LMHandler

        assert LMHandler is not None

    def test_core_comms_utils_import(self):
        """Test comms_utils imports."""
        from rlm.core.comms.comms_utils import (
            LMRequest,
            LMResponse,
            send_lm_request,
            send_lm_request_batched,
            socket_recv,
            socket_send,
        )

        assert LMRequest is not None
        assert LMResponse is not None
        assert callable(send_lm_request)
        assert callable(send_lm_request_batched)
        assert callable(socket_recv)
        assert callable(socket_send)


class TestEnvironmentImports:
    """Test environment module imports."""

    def test_environments_module_import(self):
        """Test that environments module can be imported."""
        import rlm.environments

        assert hasattr(rlm.environments, "get_environment")
        assert hasattr(rlm.environments, "BaseEnv")
        assert hasattr(rlm.environments, "LocalREPL")

    def test_base_env_import(self):
        """Test BaseEnv import."""
        from rlm.environments.base_env import BaseEnv, IsolatedEnv, NonIsolatedEnv

        assert BaseEnv is not None
        assert IsolatedEnv is not None
        assert NonIsolatedEnv is not None

    def test_local_repl_import(self):
        """Test LocalREPL import."""
        from rlm.environments.local_repl import LocalREPL

        assert LocalREPL is not None

    def test_modal_repl_import(self):
        """Test ModalREPL import."""
        pytest.importorskip("modal")
        from rlm.environments.modal_repl import ModalREPL

        assert ModalREPL is not None

    def test_docker_repl_import(self):
        """Test DockerREPL import."""
        from rlm.environments.docker_repl import DockerREPL

        assert DockerREPL is not None

    def test_prime_repl_import(self):
        """Test PrimeREPL import."""
        pytest.importorskip("prime_sandboxes")
        from rlm.environments.prime_repl import PrimeREPL

        assert PrimeREPL is not None

    def test_get_environment_function(self):
        """Test get_environment function import."""
        from rlm.environments import get_environment

        assert callable(get_environment)


class TestLoggerImports:
    """Test logger module imports."""

    def test_logger_module_import(self):
        """Test that logger module can be imported."""
        import rlm.logger

        assert hasattr(rlm.logger, "RLMLogger")
        assert hasattr(rlm.logger, "VerbosePrinter")
        assert "RLMLogger" in rlm.logger.__all__
        assert "VerbosePrinter" in rlm.logger.__all__

    def test_rlm_logger_import(self):
        """Test RLMLogger import."""
        from rlm.logger.rlm_logger import RLMLogger

        assert RLMLogger is not None

    def test_verbose_import(self):
        """Test VerbosePrinter import."""
        from rlm.logger.verbose import VerbosePrinter

        assert VerbosePrinter is not None

    def test_verbose_printer_disabled_noop(self):
        """Disabled verbose printer should be a no-op regardless of rich availability."""
        from rlm.logger.verbose import VerbosePrinter

        printer = VerbosePrinter(enabled=False)
        printer.print_final_answer("ok")
        printer.print_summary(0, 0.0)

        assert printer is not None


class TestUtilsImports:
    """Test utils module imports."""

    def test_parsing_import(self):
        """Test parsing module import."""
        from rlm.utils.parsing import (
            find_code_blocks,
            find_final_answer,
            format_execution_result,
            format_iteration,
        )

        assert callable(find_code_blocks)
        assert callable(find_final_answer)
        assert callable(format_iteration)
        assert callable(format_execution_result)

    def test_prompts_import(self):
        """Test prompts module import."""
        from rlm.utils.prompts import (
            RLM_SYSTEM_PROMPT,
            USER_PROMPT,
            build_rlm_system_prompt,
            build_user_prompt,
        )

        assert RLM_SYSTEM_PROMPT is not None
        assert USER_PROMPT is not None
        assert callable(build_rlm_system_prompt)
        assert callable(build_user_prompt)

    def test_rlm_utils_import(self):
        """Test rlm_utils module import."""
        from rlm.utils.rlm_utils import filter_sensitive_keys

        assert callable(filter_sensitive_keys)


class TestImportConflicts:
    """Test for import conflicts and naming issues."""

    def test_no_duplicate_names_in_rlm_all(self):
        """Test that __all__ in rlm.__init__ has no duplicates."""
        import rlm

        if hasattr(rlm, "__all__"):
            all_items = rlm.__all__
            assert len(all_items) == len(set(all_items)), (
                f"Duplicate items in rlm.__all__: {all_items}"
            )

    def test_no_duplicate_names_in_logger_all(self):
        """Test that __all__ in rlm.logger.__init__ has no duplicates."""
        import rlm.logger

        if hasattr(rlm.logger, "__all__"):
            all_items = rlm.logger.__all__
            assert len(all_items) == len(set(all_items)), (
                f"Duplicate items in rlm.logger.__all__: {all_items}"
            )

    def test_no_duplicate_names_in_engine_all(self):
        """Test that __all__ in rlm.core.engine.__init__ has no duplicates."""
        import rlm.core.engine as engine

        if hasattr(engine, "__all__"):
            all_items = engine.__all__
            assert len(all_items) == len(set(all_items)), (
                f"Duplicate items in rlm.core.engine.__all__: {all_items}"
            )

    def test_no_duplicate_names_in_memory_all(self):
        """Test that __all__ in rlm.core.memory.__init__ has no duplicates."""
        import rlm.core.memory as memory

        if hasattr(memory, "__all__"):
            all_items = memory.__all__
            assert len(all_items) == len(set(all_items)), (
                f"Duplicate items in rlm.core.memory.__all__: {all_items}"
            )

    def test_no_duplicate_names_in_integrations_all(self):
        """Test that __all__ in rlm.core.integrations.__init__ has no duplicates."""
        import rlm.core.integrations as integrations

        if hasattr(integrations, "__all__"):
            all_items = integrations.__all__
            assert len(all_items) == len(set(all_items)), (
                f"Duplicate items in rlm.core.integrations.__all__: {all_items}"
            )

    def test_no_duplicate_names_in_observability_all(self):
        """Test that __all__ in rlm.core.observability.__init__ has no duplicates."""
        import rlm.core.observability as observability

        if hasattr(observability, "__all__"):
            all_items = observability.__all__
            assert len(all_items) == len(set(all_items)), (
                f"Duplicate items in rlm.core.observability.__all__: {all_items}"
            )

    def test_no_duplicate_names_in_optimized_all(self):
        """Test that __all__ in rlm.core.optimized.__init__ has no duplicates."""
        import rlm.core.optimized as optimized

        if hasattr(optimized, "__all__"):
            all_items = optimized.__all__
            assert len(all_items) == len(set(all_items)), (
                f"Duplicate items in rlm.core.optimized.__all__: {all_items}"
            )

    def test_all_declarations_match_exports(self):
        """Test that __all__ declarations match actual exports."""
        import rlm
        import rlm.core.engine as engine
        import rlm.core.integrations as integrations
        import rlm.core.memory as memory
        import rlm.core.observability as observability
        import rlm.core.optimized as optimized
        import rlm.logger

        # Test rlm.__all__
        if hasattr(rlm, "__all__"):
            for name in rlm.__all__:
                assert hasattr(rlm, name), f"rlm.__all__ declares '{name}' but it's not exported"

        # Test rlm.logger.__all__
        if hasattr(rlm.logger, "__all__"):
            for name in rlm.logger.__all__:
                assert hasattr(rlm.logger, name), (
                    f"rlm.logger.__all__ declares '{name}' but it's not exported"
                )

        if hasattr(engine, "__all__"):
            for name in engine.__all__:
                assert hasattr(engine, name), (
                    f"rlm.core.engine.__all__ declares '{name}' but it's not exported"
                )

        if hasattr(memory, "__all__"):
            for name in memory.__all__:
                assert hasattr(memory, name), (
                    f"rlm.core.memory.__all__ declares '{name}' but it's not exported"
                )

        if hasattr(integrations, "__all__"):
            for name in integrations.__all__:
                assert hasattr(integrations, name), (
                    f"rlm.core.integrations.__all__ declares '{name}' but it's not exported"
                )

        if hasattr(observability, "__all__"):
            for name in observability.__all__:
                assert hasattr(observability, name), (
                    f"rlm.core.observability.__all__ declares '{name}' but it's not exported"
                )

        if hasattr(optimized, "__all__"):
            for name in optimized.__all__:
                assert hasattr(optimized, name), (
                    f"rlm.core.optimized.__all__ declares '{name}' but it's not exported"
                )

    def test_no_circular_imports(self):
        """Test that modules can be imported without circular import errors."""
        # Core modules that should always be importable
        core_modules = [
            "rlm",
            "rlm.clients",
            "rlm.clients.base_lm",
            "rlm.core",
            "rlm.core.engine",
            "rlm.core.integrations",
            "rlm.core.memory",
            "rlm.core.observability",
            "rlm.core.optimized",
            "rlm.core.types",
            "rlm.core.engine.rlm",
            "rlm.core.engine.lm_handler",
            "rlm.core.memory.memory_manager",
            "rlm.core.memory.knowledge_base",
            "rlm.core.observability.turn_telemetry",
            "rlm.core.optimized.fast",
            "rlm.core.comms.comms_utils",
            "rlm.environments",
            "rlm.environments.base_env",
            "rlm.environments.local_repl",
            "rlm.environments.docker_repl",
            "rlm.logger",
            "rlm.logger.rlm_logger",
            "rlm.logger.verbose",
            "rlm.utils",
            "rlm.utils.parsing",
            "rlm.utils.prompts",
            "rlm.utils.rlm_utils",
        ]

        # Optional modules that may not be available
        optional_modules = [
            ("rlm.clients.openai", "openai"),
            ("rlm.clients.anthropic", "anthropic"),
            ("rlm.clients.portkey", "portkey_ai"),
            ("rlm.clients.litellm", "litellm"),
            ("rlm.environments.modal_repl", "modal"),
            ("rlm.environments.prime_repl", "prime_sandboxes"),
        ]

        # Test core modules
        for module_name in core_modules:
            # Remove from sys.modules if present to test fresh import
            if module_name in sys.modules:
                del sys.modules[module_name]
            try:
                importlib.import_module(module_name)
            except ImportError as e:
                pytest.fail(f"Failed to import {module_name}: {e}")

        # Test optional modules (skip if dependency not available)
        for module_name, dependency in optional_modules:
            # Check if dependency is available
            try:
                importlib.import_module(dependency)
            except ImportError:
                continue  # Skip this module if dependency not available

            # If dependency is available, test the module import
            if module_name in sys.modules:
                del sys.modules[module_name]
            try:
                importlib.import_module(module_name)
            except ImportError as e:
                pytest.fail(f"Failed to import {module_name}: {e}")

    def test_no_naming_conflicts_across_modules(self):
        """Test that there are no naming conflicts across different modules."""
        # Collect all public names from each module
        module_exports: dict[str, set[str]] = {}

        # Check main modules
        import rlm
        import rlm.clients
        import rlm.environments
        import rlm.logger

        if hasattr(rlm, "__all__"):
            module_exports["rlm"] = set(rlm.__all__)
        else:
            module_exports["rlm"] = {name for name in dir(rlm) if not name.startswith("_")}

        if hasattr(rlm.clients, "__all__"):
            module_exports["rlm.clients"] = set(rlm.clients.__all__)
        else:
            module_exports["rlm.clients"] = {
                name for name in dir(rlm.clients) if not name.startswith("_")
            }

        if hasattr(rlm.environments, "__all__"):
            module_exports["rlm.environments"] = set(rlm.environments.__all__)
        else:
            module_exports["rlm.environments"] = {
                name for name in dir(rlm.environments) if not name.startswith("_")
            }

        if hasattr(rlm.logger, "__all__"):
            module_exports["rlm.logger"] = set(rlm.logger.__all__)
        else:
            module_exports["rlm.logger"] = {
                name for name in dir(rlm.logger) if not name.startswith("_")
            }

        # Check for conflicts (same name in multiple modules)
        name_to_modules: dict[str, list[str]] = defaultdict(list)
        for module_name, exports in module_exports.items():
            for export_name in exports:
                name_to_modules[export_name].append(module_name)

        conflicts = {name: modules for name, modules in name_to_modules.items() if len(modules) > 1}
        # Filter out common Python builtins/dunders and typing imports that are expected
        expected_duplicates = {
            "__file__",
            "__name__",
            "__package__",
            "__path__",
            "__doc__",
            "__loader__",
            "__spec__",
            "__cached__",
            "Any",  # Common typing import
            "Literal",  # Common typing import
            "Optional",  # Common typing import
            "Union",  # Common typing import
            "Dict",  # Common typing import
            "List",  # Common typing import
            "Tuple",  # Common typing import
            "Callable",  # Common typing import
        }
        conflicts = {
            name: modules for name, modules in conflicts.items() if name not in expected_duplicates
        }

        if conflicts:
            conflict_msg = "\n".join(
                f"  '{name}' exported from: {', '.join(modules)}"
                for name, modules in conflicts.items()
            )
            pytest.fail(f"Found naming conflicts across modules:\n{conflict_msg}")


class TestImportCompleteness:
    """Test that all expected imports are available."""

    def test_all_client_classes_importable(self):
        """Test that all client classes can be imported."""
        from rlm.clients.base_lm import BaseLM

        # Verify BaseLM is a class
        assert isinstance(BaseLM, type)

        # Test optional client classes (skip gracefully if dep missing)
        try:
            import openai  # noqa: F401
            from rlm.clients.openai import OpenAIClient
            assert isinstance(OpenAIClient, type)
        except ImportError:
            pass

        try:
            import anthropic  # noqa: F401
            from rlm.clients.anthropic import AnthropicClient
            assert isinstance(AnthropicClient, type)
        except ImportError:
            pass

        try:
            import portkey_ai  # noqa: F401
            from rlm.clients.portkey import PortkeyClient
            assert isinstance(PortkeyClient, type)
        except ImportError:
            pass

        try:
            import litellm  # noqa: F401
            from rlm.clients.litellm import LiteLLMClient
            assert isinstance(LiteLLMClient, type)
        except ImportError:
            pass

    def test_all_environment_classes_importable(self):
        """Test that all environment classes can be imported."""
        from rlm.environments.base_env import BaseEnv, IsolatedEnv, NonIsolatedEnv
        from rlm.environments.docker_repl import DockerREPL
        from rlm.environments.local_repl import LocalREPL

        # Verify they're all classes
        assert isinstance(BaseEnv, type)
        assert isinstance(IsolatedEnv, type)
        assert isinstance(NonIsolatedEnv, type)
        assert isinstance(LocalREPL, type)
        assert isinstance(DockerREPL, type)

        # Test optional ModalREPL
        try:
            import modal  # noqa: F401
            from rlm.environments.modal_repl import ModalREPL
            assert isinstance(ModalREPL, type)
        except ImportError:
            pass

        # Test optional PrimeREPL
        try:
            import prime_sandboxes  # noqa: F401
            from rlm.environments.prime_repl import PrimeREPL
            assert isinstance(PrimeREPL, type)
        except ImportError:
            pass
