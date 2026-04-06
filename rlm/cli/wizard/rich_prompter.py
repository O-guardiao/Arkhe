"""RichPrompter — Implementação CLI com Rich + aliases de compat."""

from __future__ import annotations

from typing import Any

from rlm.cli.wizard.prompter import (
    WizardCancelledError,
    WizardPrompter,
    _ProgressHandle,
)

# ── Rich imports (fallback gracioso) ────────────────────────────────────── #

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.rule import Rule
    from rich.table import Table

    HAS_RICH = True
except ImportError:  # pragma: no cover
    Console = None  # type: ignore[assignment,misc]
    Panel = None  # type: ignore[assignment,misc]
    Confirm = None  # type: ignore[assignment,misc]
    Prompt = None  # type: ignore[assignment,misc]
    Rule = None  # type: ignore[assignment,misc]
    Table = None  # type: ignore[assignment,misc]
    HAS_RICH = False


# ── Progress handles ────────────────────────────────────────────────────── #


class _RichProgressHandle(_ProgressHandle):
    """Spinner usando rich.status."""

    def __init__(self, console: Any, label: str) -> None:
        self._console = console
        self._status = console.status(label, spinner="dots")
        self._status.start()

    def update(self, msg: str) -> None:
        self._status.update(msg)

    def stop(self, msg: str = "") -> None:
        self._status.stop()
        if msg:
            self._console.print(msg)


class _PlainProgressHandle(_ProgressHandle):
    """Fallback sem rich."""

    def __init__(self, label: str) -> None:
        print(f"  ⏳ {label}")

    def update(self, msg: str) -> None:
        print(f"  … {msg}")

    def stop(self, msg: str = "") -> None:
        if msg:
            import re
            print(re.sub(r"\[/?[^\]]+\]", "", msg))


# ── Helpers ─────────────────────────────────────────────────────────────── #


def _clean_markup(text: str) -> str:
    """Remove tags de markup Rich para fallback plain."""
    import re
    return re.sub(r"\[/?[^\]]+\]", "", text)


# ── RichPrompter ────────────────────────────────────────────────────────── #


class RichPrompter(WizardPrompter):
    """Implementação do WizardPrompter usando Rich."""

    def __init__(self) -> None:
        self._console: Any = None
        if HAS_RICH:
            assert Console is not None
            self._console = Console()

    # --- output ---

    def _print(self, msg: str = "") -> None:
        if self._console:
            self._console.print(msg)
        else:
            print(_clean_markup(msg))

    def intro(self, title: str) -> None:
        if self._console:
            assert Panel is not None
            self._console.print(Panel(title, border_style="green", expand=False))
        else:
            print(f"\n{'═' * 60}")
            print(f"  {_clean_markup(title)}")
            print(f"{'═' * 60}")
        self._print()

    def outro(self, message: str) -> None:
        if self._console:
            assert Rule is not None
            self._console.print()
            self._console.print(Rule(style="green"))
            self._console.print(f"  {message}")
            self._console.print()
        else:
            print(f"\n{'─' * 60}")
            print(f"  {_clean_markup(message)}")

    def note(self, message: str, title: str = "") -> None:
        if self._console:
            assert Panel is not None
            self._console.print(Panel(message, title=title or None, expand=False))
        else:
            if title:
                print(f"\n[{_clean_markup(title)}]")
            print(_clean_markup(message))

    # --- input ---

    def select(
        self,
        message: str,
        options: list[dict[str, Any]],
        initial_value: Any = None,
    ) -> Any:
        """Menu de seleção única. options: [{"value": X, "label": "...", "hint": "..."}]."""
        self._print()
        self._print(f"[bold]{message}[/]" if self._console else _clean_markup(message))
        for i, opt in enumerate(options, 1):
            hint = f"  [dim]({opt['hint']})[/]" if opt.get("hint") else ""
            if self._console:
                self._print(f"  [cyan]{i}[/]) {opt['label']}{hint}")
            else:
                h = f"  ({opt.get('hint', '')})" if opt.get("hint") else ""
                print(f"  {i}) {_clean_markup(opt['label'])}{h}")

        # Encontrar default numérico
        default_idx = "1"
        if initial_value is not None:
            for i, opt in enumerate(options, 1):
                if opt["value"] == initial_value:
                    default_idx = str(i)
                    break

        while True:
            try:
                if self._console:
                    assert Prompt is not None
                    raw = Prompt.ask("Escolha", default=default_idx, console=self._console)
                else:
                    raw = input(f"  Escolha [{default_idx}]: ").strip() or default_idx
            except (KeyboardInterrupt, EOFError):
                raise WizardCancelledError()

            try:
                idx = int(raw)
                if 1 <= idx <= len(options):
                    return options[idx - 1]["value"]
            except ValueError:
                pass
            self._print("[yellow]  Opção inválida. Tente novamente.[/]" if self._console
                        else "  Opção inválida. Tente novamente.")

    def text(
        self,
        message: str,
        default: str = "",
        placeholder: str = "",
        password: bool = False,
        validate: Any = None,
    ) -> str:
        hint = f" ({placeholder})" if placeholder and not default else ""
        while True:
            try:
                if self._console:
                    assert Prompt is not None
                    raw = Prompt.ask(
                        f"{message}{hint}",
                        default=default or None,
                        password=password,
                        console=self._console,
                    )
                else:
                    suffix = f" [{default}]" if default else ""
                    raw = input(f"{_clean_markup(message)}{suffix}: ").strip()
                    if not raw:
                        raw = default
            except (KeyboardInterrupt, EOFError):
                raise WizardCancelledError()

            raw = (raw or "").strip()
            if validate:
                err = validate(raw)
                if err:
                    self._print(f"[yellow]  {err}[/]" if self._console else f"  {err}")
                    continue
            return raw

    def confirm(self, message: str, default: bool = True) -> bool:
        try:
            if self._console:
                assert Confirm is not None
                return Confirm.ask(message, default=default, console=self._console)
            else:
                hint = "S/n" if default else "s/N"
                raw = input(f"{_clean_markup(message)} [{hint}]: ").strip().lower()
                if not raw:
                    return default
                return raw in ("s", "sim", "y", "yes")
        except (KeyboardInterrupt, EOFError):
            raise WizardCancelledError()

    def progress(self, label: str) -> _ProgressHandle:
        if self._console:
            return _RichProgressHandle(self._console, label)
        return _PlainProgressHandle(label)


# ── Compat aliases ──────────────────────────────────────────────────────── #

_default_prompter = RichPrompter()
_console = getattr(_default_prompter, "_console", None)


def _print(msg: str = "", markup: bool = True) -> None:  # noqa: ARG001
    _default_prompter._print(msg)


def _ask(question: str, default: str = "", password: bool = False) -> str:
    return _default_prompter.text(question, default=default, password=password)


def _confirm(question: str, default: bool = True) -> bool:
    return _default_prompter.confirm(question, default=default)


def _rule(title: str = "") -> None:
    if _console:
        assert Rule is not None
        _console.print(Rule(title, style="dim"))
    else:
        print(f"\n{'─' * 50} {title}")


def _panel(body: str, title: str = "") -> None:
    _default_prompter.note(body, title=title)
