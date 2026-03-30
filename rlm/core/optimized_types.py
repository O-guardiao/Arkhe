from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rlm.core.types import RLMChatCompletion


PromptPayload = str | dict[str, Any]


@dataclass
class LMRequest:
    """Request message sent to the LM Handler."""

    prompt: PromptPayload | None = None
    prompts: list[PromptPayload] | None = None
    model: str | None = None
    depth: int = 0

    @property
    def is_batched(self) -> bool:
        return self.prompts is not None and len(self.prompts) > 0

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"depth": self.depth}
        if self.prompt is not None:
            payload["prompt"] = self.prompt
        if self.prompts is not None:
            payload["prompts"] = self.prompts
        if self.model is not None:
            payload["model"] = self.model
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LMRequest":
        return cls(
            prompt=data.get("prompt"),
            prompts=data.get("prompts"),
            model=data.get("model"),
            depth=int(data.get("depth", 0)),
        )


@dataclass
class LMResponse:
    """Response message from the LM Handler."""

    error: str | None = None
    chat_completion: RLMChatCompletion | None = None
    chat_completions: list[RLMChatCompletion] | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def is_batched(self) -> bool:
        return self.chat_completions is not None

    def to_dict(self) -> dict[str, Any]:
        if self.error is not None:
            return {"error": self.error, "chat_completion": None, "chat_completions": None}
        if self.chat_completions is not None:
            return {
                "chat_completions": [
                    completion.to_dict() if hasattr(completion, "to_dict") else completion
                    for completion in self.chat_completions
                ],
                "chat_completion": None,
                "error": None,
            }
        if self.chat_completion is not None:
            completion = self.chat_completion
            return {
                "chat_completion": completion.to_dict() if hasattr(completion, "to_dict") else completion,
                "chat_completions": None,
                "error": None,
            }
        return {"error": "No response", "chat_completion": None, "chat_completions": None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LMResponse":
        chat_completion = None
        if data.get("chat_completion"):
            raw = data["chat_completion"]
            chat_completion = RLMChatCompletion.from_dict(raw) if isinstance(raw, dict) else raw

        chat_completions = None
        if data.get("chat_completions"):
            chat_completions = [
                RLMChatCompletion.from_dict(completion) if isinstance(completion, dict) else completion
                for completion in data["chat_completions"]
            ]

        return cls(
            error=data.get("error"),
            chat_completion=chat_completion,
            chat_completions=chat_completions,
        )

    @classmethod
    def success_response(cls, chat_completion: RLMChatCompletion) -> "LMResponse":
        return cls(chat_completion=chat_completion)

    @classmethod
    def batched_success_response(cls, chat_completions: list[RLMChatCompletion]) -> "LMResponse":
        return cls(chat_completions=chat_completions)

    @classmethod
    def error_response(cls, error: str) -> "LMResponse":
        return cls(error=error)