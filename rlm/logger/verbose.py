"""
Verbose printing for RLM using rich when available.

Falls back to plain-text output when rich is not installed so importing the
logger module never hard-fails in minimal environments.
"""

import sys
from typing import Any

try:
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text

    _RICH_AVAILABLE = True
except ImportError:
    Console = Group = Panel = Rule = Table = Text = None

    _RICH_AVAILABLE = False

from rlm.core.types import CodeBlock, RLMIteration, RLMMetadata

# ============================================================================
# Tokyo Night Color Theme
# ============================================================================
COLORS = {
    "primary": "#7AA2F7",  # Soft blue - headers, titles
    "secondary": "#BB9AF7",  # Soft purple - emphasis
    "success": "#9ECE6A",  # Soft green - success, code
    "warning": "#E0AF68",  # Soft amber - warnings
    "error": "#F7768E",  # Soft red/pink - errors
    "text": "#A9B1D6",  # Soft gray-blue - regular text
    "muted": "#565F89",  # Muted gray - less important
    "accent": "#7DCFFF",  # Bright cyan - accents
    "bg_subtle": "#1A1B26",  # Dark background
    "border": "#3B4261",  # Border color
    "code_bg": "#24283B",  # Code background
}

# Rich styles
STYLE_PRIMARY = f"bold {COLORS['primary']}"
STYLE_SECONDARY = COLORS["secondary"]
STYLE_SUCCESS = COLORS["success"]
STYLE_SUCCESS_BOLD = f"bold {COLORS['success']}"
STYLE_WARNING = COLORS["warning"]
STYLE_WARNING_BOLD = f"bold {COLORS['warning']}"
STYLE_ERROR = COLORS["error"]
STYLE_TEXT = COLORS["text"]
STYLE_MUTED = COLORS["muted"]
STYLE_ACCENT = f"bold {COLORS['accent']}"


def _require_rich() -> tuple[Any, Any, Any, Any, Any, Any]:
    """Retorna os componentes do Rich após narrowing explícito para o type checker."""
    assert Console is not None
    assert Group is not None
    assert Panel is not None
    assert Rule is not None
    assert Table is not None
    assert Text is not None
    return Console, Group, Panel, Rule, Table, Text


def _to_str(value: Any) -> str:
    """Convert any value to string safely."""
    if isinstance(value, str):
        return value
    return str(value)


class VerbosePrinter:
    """
    Renderizador de saída verbosa do RLM para humanos.

    Quando ``rich`` está disponível, usa painéis, regras e tabelas para exibir:
    - cabeçalho de configuração;
    - iterações e respostas do LLM;
    - execução de código com stdout/stderr;
    - subchamadas para outros modelos;
    - resposta final e resumo.

    Quando ``rich`` não está instalado, o comportamento degrada para texto puro
    em stderr. Isso preserva observabilidade básica sem tornar ``rich`` uma
    dependência obrigatória para importar o pacote.
    """

    def __init__(self, enabled: bool = True):
        """
        Initialize the verbose printer.

        Args:
            enabled: Whether verbose printing is enabled. If False, all methods are no-ops.
        """
        self.enabled = enabled
        if enabled and _RICH_AVAILABLE:
            console_class, _, _, _, _, _ = _require_rich()
            self.console = console_class(stderr=True)
        else:
            self.console = None
        self._iteration_count = 0

    def _plain(self, message: str) -> None:
        """Emite uma linha simples em stderr no modo fallback."""
        if self.enabled:
            print(message, file=sys.stderr, flush=True)

    def print_header(
        self,
        backend: str,
        model: str,
        environment: str,
        max_iterations: int,
        max_depth: int,
        other_backends: list[str] | None = None,
    ) -> None:
        """Print the initial RLM configuration header."""
        if not self.enabled:
            return

        if not _RICH_AVAILABLE:
            other = f" sub-models={', '.join(other_backends)}" if other_backends else ""
            self._plain(
                f"RLM backend={backend} model={model} environment={environment} "
                f"max_iterations={max_iterations} max_depth={max_depth}{other}"
            )
            return

        assert self.console is not None
        _, _, panel_class, _, table_class, text_class = _require_rich()

        # Main title
        title = text_class()
        title.append("◆ ", style=STYLE_ACCENT)
        title.append("RLM", style=STYLE_PRIMARY)
        title.append(" ━ Recursive Language Model", style=STYLE_MUTED)

        # Configuration table
        config_table = table_class(
            show_header=False,
            show_edge=False,
            box=None,
            padding=(0, 2),
            expand=True,
        )
        config_table.add_column("key", style=STYLE_MUTED, width=16)
        config_table.add_column("value", style=STYLE_TEXT)
        config_table.add_column("key2", style=STYLE_MUTED, width=16)
        config_table.add_column("value2", style=STYLE_TEXT)

        config_table.add_row(
            "Backend",
            text_class(backend, style=STYLE_SECONDARY),
            "Environment",
            text_class(environment, style=STYLE_SECONDARY),
        )
        config_table.add_row(
            "Model",
            text_class(model, style=STYLE_ACCENT),
            "Max Iterations",
            text_class(str(max_iterations), style=STYLE_WARNING),
        )

        if other_backends:
            backends_text = text_class(", ".join(other_backends), style=STYLE_SECONDARY)
            config_table.add_row(
                "Sub-models",
                backends_text,
                "Max Depth",
                text_class(str(max_depth), style=STYLE_WARNING),
            )
        else:
            config_table.add_row(
                "Max Depth",
                text_class(str(max_depth), style=STYLE_WARNING),
                "",
                "",
            )

        # Wrap in panel
        panel = panel_class(
            config_table,
            title=title,
            title_align="left",
            border_style=COLORS["border"],
            padding=(1, 2),
        )

        self.console.print()
        self.console.print(panel)
        self.console.print()

    def print_metadata(self, metadata: RLMMetadata) -> None:
        """Deriva do metadata os campos exibidos no cabeçalho."""
        if not self.enabled:
            return

        model = metadata.backend_kwargs.get("model_name", "unknown")
        other = list(metadata.other_backends) if metadata.other_backends else None

        self.print_header(
            backend=metadata.backend,
            model=model,
            environment=metadata.environment_type,
            max_iterations=metadata.max_iterations,
            max_depth=metadata.max_depth,
            other_backends=other,
        )

    def print_iteration_start(self, iteration: int) -> None:
        """Print the start of a new iteration."""
        if not self.enabled:
            return

        self._iteration_count = iteration

        if not _RICH_AVAILABLE:
            self._plain(f"[Iteration {iteration}]")
            return

        assert self.console is not None
        _, _, _, rule_class, _, text_class = _require_rich()

        rule = rule_class(
            text_class(f" Iteration {iteration} ", style=STYLE_PRIMARY),
            style=COLORS["border"],
            characters="─",
        )
        self.console.print(rule)

    def print_completion(self, response: Any, iteration_time: float | None = None) -> None:
        """Print a completion response."""
        if not self.enabled:
            return

        if not _RICH_AVAILABLE:
            suffix = f" ({iteration_time:.2f}s)" if iteration_time else ""
            self._plain(f"LLM Response{suffix}: {_to_str(response)}")
            return

        assert self.console is not None
        _, group_class, panel_class, _, _, text_class = _require_rich()

        # Header with timing
        header = text_class()
        header.append("◇ ", style=STYLE_ACCENT)
        header.append("LLM Response", style=STYLE_PRIMARY)
        if iteration_time:
            header.append(f"  ({iteration_time:.2f}s)", style=STYLE_MUTED)

        # Response content
        response_str = _to_str(response)
        response_text = text_class(response_str, style=STYLE_TEXT)

        # Count words roughly
        word_count = len(response_str.split())
        footer = text_class(f"~{word_count} words", style=STYLE_MUTED)

        panel = panel_class(
            group_class(response_text, text_class(), footer),
            title=header,
            title_align="left",
            border_style=COLORS["muted"],
            padding=(0, 1),
        )
        self.console.print(panel)

    def print_code_execution(self, code_block: CodeBlock) -> None:
        """Print code execution details."""
        if not self.enabled:
            return

        result = code_block.result

        if not _RICH_AVAILABLE:
            summary = [f"Code Execution ({result.execution_time or 0:.3f}s)", _to_str(code_block.code)]
            if result.stdout:
                summary.append(f"stdout: {_to_str(result.stdout)}")
            if result.stderr:
                summary.append(f"stderr: {_to_str(result.stderr)}")
            if result.rlm_calls:
                summary.append(f"subcalls: {len(result.rlm_calls)}")
            self._plain("\n".join(summary))
            return

        assert self.console is not None
        _, group_class, panel_class, _, _, text_class = _require_rich()

        # Header
        header = text_class()
        header.append("▸ ", style=STYLE_SUCCESS)
        header.append("Code Execution", style=STYLE_SUCCESS_BOLD)
        if result.execution_time:
            header.append(f"  ({result.execution_time:.3f}s)", style=STYLE_MUTED)

        # Build content
        content_parts = []

        # Code snippet
        code_text = text_class()
        code_text.append("Code:\n", style=STYLE_MUTED)
        code_text.append(_to_str(code_block.code), style=STYLE_TEXT)
        content_parts.append(code_text)

        # Stdout if present
        stdout_str = _to_str(result.stdout) if result.stdout else ""
        if stdout_str.strip():
            stdout_text = text_class()
            stdout_text.append("\nOutput:\n", style=STYLE_MUTED)
            stdout_text.append(stdout_str, style=STYLE_SUCCESS)
            content_parts.append(stdout_text)

        # Stderr if present (error)
        stderr_str = _to_str(result.stderr) if result.stderr else ""
        if stderr_str.strip():
            stderr_text = text_class()
            stderr_text.append("\nError:\n", style=STYLE_MUTED)
            stderr_text.append(stderr_str, style=STYLE_ERROR)
            content_parts.append(stderr_text)

        # Sub-calls summary
        if result.rlm_calls:
            calls_text = text_class()
            calls_text.append(f"\n↳ {len(result.rlm_calls)} sub-call(s)", style=STYLE_SECONDARY)
            content_parts.append(calls_text)

        panel = panel_class(
            group_class(*content_parts),
            title=header,
            title_align="left",
            border_style=COLORS["success"],
            padding=(0, 1),
        )
        self.console.print(panel)

    def print_subcall(
        self,
        model: str,
        prompt_preview: str,
        response_preview: str,
        execution_time: float | None = None,
    ) -> None:
        """Print a sub-call to another model."""
        if not self.enabled:
            return

        if not _RICH_AVAILABLE:
            suffix = f" ({execution_time:.2f}s)" if execution_time else ""
            self._plain(
                f"Sub-call {model}{suffix}: prompt={_to_str(prompt_preview)} response={_to_str(response_preview)}"
            )
            return

        assert self.console is not None
        _, _, panel_class, _, _, text_class = _require_rich()

        # Header
        header = text_class()
        header.append("  ↳ ", style=STYLE_SECONDARY)
        header.append("Sub-call: ", style=STYLE_SECONDARY)
        header.append(_to_str(model), style=STYLE_ACCENT)
        if execution_time:
            header.append(f"  ({execution_time:.2f}s)", style=STYLE_MUTED)

        # Content
        content = text_class()
        content.append("Prompt: ", style=STYLE_MUTED)
        content.append(_to_str(prompt_preview), style=STYLE_TEXT)
        content.append("\nResponse: ", style=STYLE_MUTED)
        content.append(_to_str(response_preview), style=STYLE_TEXT)

        panel = panel_class(
            content,
            title=header,
            title_align="left",
            border_style=COLORS["secondary"],
            padding=(0, 1),
        )
        self.console.print(panel)

    def print_iteration(self, iteration: RLMIteration, iteration_num: int) -> None:
        """
        Imprime uma iteração completa.

        Este é o ponto de entrada principal para renderizar uma iteração depois
        que ela foi consolidada pelo loop do RLM.
        """
        if not self.enabled:
            return

        # Print iteration header
        self.print_iteration_start(iteration_num)

        # Print the LLM response
        self.print_completion(iteration.response, iteration.iteration_time)

        # Print each code block execution
        for code_block in iteration.code_blocks:
            self.print_code_execution(code_block)

            # Print any sub-calls made during this code block
            for call in code_block.result.rlm_calls:
                self.print_subcall(
                    model=call.root_model,
                    prompt_preview=_to_str(call.prompt) if call.prompt else "",
                    response_preview=_to_str(call.response) if call.response else "",
                    execution_time=call.execution_time,
                )

    def print_final_answer(self, answer: Any) -> None:
        """Renderiza a resposta final produzida pelo agente."""
        if not self.enabled:
            return

        if not _RICH_AVAILABLE:
            self._plain(f"Final Answer: {_to_str(answer)}")
            return

        assert self.console is not None
        _, _, panel_class, _, _, text_class = _require_rich()

        # Title
        title = text_class()
        title.append("★ ", style=STYLE_WARNING)
        title.append("Final Answer", style=STYLE_WARNING_BOLD)

        # Answer content
        answer_text = text_class(_to_str(answer), style=STYLE_TEXT)

        panel = panel_class(
            answer_text,
            title=title,
            title_align="left",
            border_style=COLORS["warning"],
            padding=(1, 2),
        )

        self.console.print()
        self.console.print(panel)
        self.console.print()

    def print_summary(
        self,
        total_iterations: int,
        total_time: float,
        usage_summary: dict[str, Any] | None = None,
    ) -> None:
        """Renderiza o resumo final da execução.

        Se ``usage_summary`` estiver disponível, agrega tokens de entrada e saída
        por backend para exibição compacta.
        """
        if not self.enabled:
            return

        if not _RICH_AVAILABLE:
            parts = [f"Summary: iterations={total_iterations}", f"total_time={total_time:.2f}s"]
            if usage_summary:
                total_input = sum(
                    m.get("total_input_tokens", 0)
                    for m in usage_summary.get("model_usage_summaries", {}).values()
                )
                total_output = sum(
                    m.get("total_output_tokens", 0)
                    for m in usage_summary.get("model_usage_summaries", {}).values()
                )
                parts.append(f"input_tokens={total_input}")
                parts.append(f"output_tokens={total_output}")
            self._plain(" ".join(parts))
            return

        assert self.console is not None
        _, _, _, rule_class, table_class, _ = _require_rich()

        # Summary table
        summary_table = table_class(
            show_header=False,
            show_edge=False,
            box=None,
            padding=(0, 2),
        )
        summary_table.add_column("metric", style=STYLE_MUTED)
        summary_table.add_column("value", style=STYLE_ACCENT)

        summary_table.add_row("Iterations", str(total_iterations))
        summary_table.add_row("Total Time", f"{total_time:.2f}s")

        if usage_summary:
            total_input = sum(
                m.get("total_input_tokens", 0)
                for m in usage_summary.get("model_usage_summaries", {}).values()
            )
            total_output = sum(
                m.get("total_output_tokens", 0)
                for m in usage_summary.get("model_usage_summaries", {}).values()
            )
            if total_input or total_output:
                summary_table.add_row("Input Tokens", f"{total_input:,}")
                summary_table.add_row("Output Tokens", f"{total_output:,}")

        # Wrap in rule
        self.console.print()
        self.console.print(rule_class(style=COLORS["border"], characters="═"))
        self.console.print(summary_table, justify="center")
        self.console.print(rule_class(style=COLORS["border"], characters="═"))
        self.console.print()
