"""WizardPrompter — Interface abstrata para interações do wizard."""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any, TypeVar

T = TypeVar("T")

if TYPE_CHECKING:
    from rich.console import Console as RichConsole
else:
    RichConsole = Any


class WizardCancelledError(Exception):
    """Levantada quando o usuário cancela (Ctrl+C ou ESC)."""

    def __init__(self, message: str = "wizard cancelado") -> None:
        super().__init__(message)


class WizardPrompter(abc.ABC):
    """Contrato de I/O para todas as interações do wizard.

    Permite trocar implementação (CLI, web, API remota) sem
    alterar a lógica de onboarding.
    """

    @abc.abstractmethod
    def intro(self, title: str) -> None: ...

    @abc.abstractmethod
    def outro(self, message: str) -> None: ...

    @abc.abstractmethod
    def note(self, message: str, title: str = "") -> None: ...

    @abc.abstractmethod
    def select(
        self,
        message: str,
        options: list[dict[str, Any]],
        initial_value: Any = None,
    ) -> Any: ...

    @abc.abstractmethod
    def text(
        self,
        message: str,
        default: str = "",
        placeholder: str = "",
        password: bool = False,
        validate: Any = None,
    ) -> str: ...

    @abc.abstractmethod
    def confirm(self, message: str, default: bool = True) -> bool: ...

    @abc.abstractmethod
    def progress(self, label: str) -> _ProgressHandle: ...


class _ProgressHandle:
    """Handle para spinners/progress."""

    def update(self, msg: str) -> None: ...  # pragma: no cover
    def stop(self, msg: str = "") -> None: ...  # pragma: no cover
