"""Unit tests for the OpenAI client."""

from unittest.mock import MagicMock, patch

from rlm.clients.openai import DEFAULT_PRIME_INTELLECT_BASE_URL, OpenAIClient


def _mock_chat_response(content: str = "ok") -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    response.usage.prompt_tokens = 10
    response.usage.completion_tokens = 5
    response.usage.total_tokens = 15
    return response


class TestOpenAIClientUnit:
    def test_init_passes_client_timeout_to_sdk_client(self):
        with patch("rlm.clients.openai.openai.OpenAI") as mock_sync_class, patch(
            "rlm.clients.openai.openai.AsyncOpenAI"
        ) as mock_async_class:
            mock_sync_class.return_value = MagicMock()
            mock_async_class.return_value = MagicMock()

            OpenAIClient(
                api_key="test-key",
                model_name="gpt-5.4-mini",
                client_timeout=12.5,
                client_max_retries=1,
            )

            mock_sync_class.assert_called_once_with(
                api_key="test-key",
                base_url=None,
                timeout=12.5,
                max_retries=1,
            )
            mock_async_class.assert_called_once_with(
                api_key="test-key",
                base_url=None,
                timeout=12.5,
                max_retries=1,
            )

    def test_completion_passes_extra_request_kwargs(self):
        mock_response = _mock_chat_response("hello")

        with patch("rlm.clients.openai.openai.OpenAI") as mock_sync_class, patch(
            "rlm.clients.openai.openai.AsyncOpenAI"
        ) as mock_async_class:
            mock_sync = MagicMock()
            mock_async = MagicMock()
            mock_sync.chat.completions.create.return_value = mock_response
            mock_sync_class.return_value = mock_sync
            mock_async_class.return_value = mock_async

            client = OpenAIClient(
                api_key="test-key",
                model_name="gpt-5.4-mini",
                reasoning={"effort": "high"},
                temperature=0,
            )

            result = client.completion("Test prompt")

            assert result == "hello"
            mock_sync.chat.completions.create.assert_called_once_with(
                model="gpt-5.4-mini",
                messages=[{"role": "user", "content": "Test prompt"}],
                temperature=0,
                extra_body={"reasoning": {"effort": "high"}},
            )

    def test_prime_intellect_merges_usage_into_existing_extra_body(self):
        mock_response = _mock_chat_response("merged")

        with patch("rlm.clients.openai.openai.OpenAI") as mock_sync_class, patch(
            "rlm.clients.openai.openai.AsyncOpenAI"
        ) as mock_async_class:
            mock_sync = MagicMock()
            mock_async = MagicMock()
            mock_sync.base_url = DEFAULT_PRIME_INTELLECT_BASE_URL
            mock_sync.chat.completions.create.return_value = mock_response
            mock_sync_class.return_value = mock_sync
            mock_async_class.return_value = mock_async

            client = OpenAIClient(
                api_key="test-key",
                model_name="gpt-5.4-mini",
                base_url=DEFAULT_PRIME_INTELLECT_BASE_URL,
                extra_body={"metadata": {"experiment": "riemann"}},
            )

            client.completion("Test prompt")

            mock_sync.chat.completions.create.assert_called_once_with(
                model="gpt-5.4-mini",
                messages=[{"role": "user", "content": "Test prompt"}],
                extra_body={
                    "metadata": {"experiment": "riemann"},
                    "usage": {"include": True},
                },
            )

    def test_completion_retries_without_unsupported_extra_body_parameter(self):
        class DummyBadRequestError(Exception):
            def __init__(self, param: str):
                super().__init__(f"Unknown parameter: '{param}'.")
                self.body = {
                    "error": {
                        "message": f"Unknown parameter: '{param}'.",
                        "param": param,
                    }
                }

        mock_response = _mock_chat_response("fallback-ok")

        with patch("rlm.clients.openai.openai.OpenAI") as mock_sync_class, patch(
            "rlm.clients.openai.openai.AsyncOpenAI"
        ) as mock_async_class, patch(
            "rlm.clients.openai.openai.BadRequestError", DummyBadRequestError
        ):
            mock_sync = MagicMock()
            mock_async = MagicMock()
            mock_sync.chat.completions.create.side_effect = [
                DummyBadRequestError("reasoning"),
                mock_response,
            ]
            mock_sync_class.return_value = mock_sync
            mock_async_class.return_value = mock_async

            client = OpenAIClient(
                api_key="test-key",
                model_name="gpt-5.4-mini",
                reasoning={"effort": "high"},
                temperature=0,
            )

            result = client.completion("Test prompt")

            assert result == "fallback-ok"
            assert mock_sync.chat.completions.create.call_count == 2
            first_call = mock_sync.chat.completions.create.call_args_list[0].kwargs
            second_call = mock_sync.chat.completions.create.call_args_list[1].kwargs
            assert first_call["extra_body"] == {"reasoning": {"effort": "high"}}
            assert "extra_body" not in second_call
            assert second_call["temperature"] == 0