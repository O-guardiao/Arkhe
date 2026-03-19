import os
import re
from collections import defaultdict
from typing import Any

import openai
from dotenv import load_dotenv

from rlm.clients.base_lm import BaseLM
from rlm.core.types import ModelUsageSummary, UsageSummary

load_dotenv()

# Load API keys from environment variables
DEFAULT_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEFAULT_OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
DEFAULT_VERCEL_API_KEY = os.getenv("AI_GATEWAY_API_KEY")
DEFAULT_PRIME_INTELLECT_BASE_URL = "https://api.pinference.ai/api/v1/"
OPENAI_CHAT_COMPLETIONS_TOP_LEVEL_KWARGS = {
    "audio",
    "frequency_penalty",
    "function_call",
    "functions",
    "logit_bias",
    "logprobs",
    "max_completion_tokens",
    "max_tokens",
    "metadata",
    "modalities",
    "n",
    "parallel_tool_calls",
    "prediction",
    "presence_penalty",
    "response_format",
    "seed",
    "service_tier",
    "stop",
    "store",
    "stream",
    "stream_options",
    "temperature",
    "tool_choice",
    "tools",
    "top_logprobs",
    "top_p",
    "user",
    "web_search_options",
}


class OpenAIClient(BaseLM):
    """
    LM Client for running models with the OpenAI API. Works with vLLM as well.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
        **kwargs,
    ):
        super().__init__(model_name=model_name, **kwargs)

        client_timeout = kwargs.pop("client_timeout", None)
        client_max_retries = kwargs.pop("client_max_retries", None)

        if api_key is None:
            if base_url == "https://api.openai.com/v1" or base_url is None:
                api_key = DEFAULT_OPENAI_API_KEY
            elif base_url == "https://openrouter.ai/api/v1":
                api_key = DEFAULT_OPENROUTER_API_KEY
            elif base_url == "https://ai-gateway.vercel.sh/v1":
                api_key = DEFAULT_VERCEL_API_KEY

        # For vLLM, set base_url to local vLLM server address.
        client_init_kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url}
        if client_timeout is not None:
            client_init_kwargs["timeout"] = client_timeout
        if client_max_retries is not None:
            client_init_kwargs["max_retries"] = client_max_retries

        self.client = openai.OpenAI(**client_init_kwargs)
        self.async_client = openai.AsyncOpenAI(**client_init_kwargs)
        self.model_name = model_name
        self.request_kwargs = dict(kwargs)

        # Per-model usage tracking
        self.model_call_counts: dict[str, int] = defaultdict(int)
        self.model_input_tokens: dict[str, int] = defaultdict(int)
        self.model_output_tokens: dict[str, int] = defaultdict(int)
        self.model_total_tokens: dict[str, int] = defaultdict(int)

    def _build_request_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }

        extra_body = dict(self.request_kwargs.get("extra_body", {}) or {})
        for key, value in self.request_kwargs.items():
            if key == "extra_body":
                continue
            if key in OPENAI_CHAT_COMPLETIONS_TOP_LEVEL_KWARGS:
                request_kwargs[key] = value
            else:
                extra_body[key] = value

        if self.client.base_url == DEFAULT_PRIME_INTELLECT_BASE_URL:
            extra_body.setdefault("usage", {"include": True})
        if extra_body:
            request_kwargs["extra_body"] = extra_body

        return request_kwargs

    @staticmethod
    def _extract_unknown_parameter(exc: Exception) -> str | None:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                param = error.get("param")
                if isinstance(param, str) and param.strip():
                    return param.split(".", 1)[0]

        match = re.search(r"Unknown parameter: '([^']+)'", str(exc))
        if match:
            return match.group(1).split(".", 1)[0]
        return None

    @staticmethod
    def _remove_unsupported_parameter(request_kwargs: dict[str, Any], param: str) -> bool:
        removed = False

        if param in request_kwargs:
            request_kwargs.pop(param, None)
            removed = True

        extra_body = request_kwargs.get("extra_body")
        if isinstance(extra_body, dict) and param in extra_body:
            extra_body.pop(param, None)
            removed = True
            if not extra_body:
                request_kwargs.pop("extra_body", None)

        return removed

    @staticmethod
    def _clone_request_kwargs(request_kwargs: dict[str, Any]) -> dict[str, Any]:
        cloned = dict(request_kwargs)
        extra_body = cloned.get("extra_body")
        if isinstance(extra_body, dict):
            cloned["extra_body"] = dict(extra_body)
        return cloned

    def _create_completion_with_fallback(self, request_kwargs: dict[str, Any]) -> Any:
        current_kwargs = self._clone_request_kwargs(request_kwargs)

        while True:
            try:
                return self.client.chat.completions.create(**self._clone_request_kwargs(current_kwargs))
            except openai.BadRequestError as exc:
                unknown_param = self._extract_unknown_parameter(exc)
                if not unknown_param or not self._remove_unsupported_parameter(current_kwargs, unknown_param):
                    raise

    async def _acreate_completion_with_fallback(self, request_kwargs: dict[str, Any]) -> Any:
        current_kwargs = self._clone_request_kwargs(request_kwargs)

        while True:
            try:
                return await self.async_client.chat.completions.create(**self._clone_request_kwargs(current_kwargs))
            except openai.BadRequestError as exc:
                unknown_param = self._extract_unknown_parameter(exc)
                if not unknown_param or not self._remove_unsupported_parameter(current_kwargs, unknown_param):
                    raise

    def completion(self, prompt: str | list[dict[str, Any]], model: str | None = None) -> str:
        if isinstance(prompt, str):
            messages = [{"role": "user", "content": prompt}]
        elif isinstance(prompt, list) and all(isinstance(item, dict) for item in prompt):
            messages = prompt
        else:
            raise ValueError(f"Invalid prompt type: {type(prompt)}")

        model = model or self.model_name
        if not model:
            raise ValueError("Model name is required for OpenAI client.")

        response = self._create_completion_with_fallback(
            self._build_request_kwargs(model=model, messages=messages)
        )
        self._track_cost(response, model)
        return response.choices[0].message.content

    async def acompletion(
        self, prompt: str | list[dict[str, Any]], model: str | None = None
    ) -> str:
        if isinstance(prompt, str):
            messages = [{"role": "user", "content": prompt}]
        elif isinstance(prompt, list) and all(isinstance(item, dict) for item in prompt):
            messages = prompt
        else:
            raise ValueError(f"Invalid prompt type: {type(prompt)}")

        model = model or self.model_name
        if not model:
            raise ValueError("Model name is required for OpenAI client.")

        response = await self._acreate_completion_with_fallback(
            self._build_request_kwargs(model=model, messages=messages)
        )
        self._track_cost(response, model)
        return response.choices[0].message.content

    def _track_cost(self, response: openai.ChatCompletion, model: str):
        self.model_call_counts[model] += 1

        usage = getattr(response, "usage", None)
        if usage is None:
            raise ValueError("No usage data received. Tracking tokens not possible.")

        self.model_input_tokens[model] += usage.prompt_tokens
        self.model_output_tokens[model] += usage.completion_tokens
        self.model_total_tokens[model] += usage.total_tokens

        # Track last call for handler to read
        self.last_prompt_tokens = usage.prompt_tokens
        self.last_completion_tokens = usage.completion_tokens

    def get_usage_summary(self) -> UsageSummary:
        model_summaries = {}
        for model in self.model_call_counts:
            model_summaries[model] = ModelUsageSummary(
                total_calls=self.model_call_counts[model],
                total_input_tokens=self.model_input_tokens[model],
                total_output_tokens=self.model_output_tokens[model],
            )
        return UsageSummary(model_usage_summaries=model_summaries)

    def get_last_usage(self) -> ModelUsageSummary:
        return ModelUsageSummary(
            total_calls=1,
            total_input_tokens=self.last_prompt_tokens,
            total_output_tokens=self.last_completion_tokens,
        )
