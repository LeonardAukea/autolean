"""Contract tests for the official Anthropic and OpenAI SDK backends."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from autolean.llm import DocumentInput, LLMConfig, LLMError, anthropic_api, openai_api


class FakeBadRequestError(Exception):
    pass


class FakeAPIStatusError(Exception):
    status_code = 500
    message = "provider error"


class FakeAPIConnectionError(Exception):
    pass


class FakeProviderError(Exception):
    pass


ANTHROPIC_SDK = SimpleNamespace(
    BadRequestError=FakeBadRequestError,
    APIStatusError=FakeAPIStatusError,
    APIConnectionError=FakeAPIConnectionError,
    AnthropicError=FakeProviderError,
)

OPENAI_SDK = SimpleNamespace(
    APIStatusError=FakeAPIStatusError,
    APIConnectionError=FakeAPIConnectionError,
    OpenAIError=FakeProviderError,
)


def anthropic_message(*, text: str = "by exact h", stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=None,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        model="claude-fable-5",
    )


@pytest.fixture()
def anthropic_client(monkeypatch: pytest.MonkeyPatch) -> anthropic_api.AnthropicClient:
    monkeypatch.setattr(anthropic_api, "_require_sdk", lambda: ANTHROPIC_SDK)
    return anthropic_api.AnthropicClient(
        config=LLMConfig(
            model="claude-fable-5",
            backend="anthropic",
            effort="high",
        ),
        _sdk_client=object(),
    )


class TestAnthropicClient:
    def test_builds_current_messages_request(self, anthropic_client: anthropic_api.AnthropicClient) -> None:
        kwargs = anthropic_client._request_kwargs("system", "user", ["STOP"])
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["output_config"] == {"effort": "high"}
        assert kwargs["stop_sequences"] == ["STOP"]
        assert kwargs["betas"] == [anthropic_api.FALLBACK_BETA]
        assert kwargs["fallbacks"] == "default"
        assert "temperature" not in kwargs

    def test_builds_native_pdf_content_block(
        self,
        anthropic_client: anthropic_api.AnthropicClient,
    ) -> None:
        document = DocumentInput("paper.pdf", "application/pdf", b"%PDF")

        kwargs = anthropic_client._request_kwargs("system", "user", None, (document,))

        content = kwargs["messages"][0]["content"]
        assert content == [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": "JVBERg==",
                },
                "title": "paper.pdf",
            },
            {"type": "text", "text": "user"},
        ]

    def test_returns_text_and_usage(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_client: anthropic_api.AnthropicClient,
    ) -> None:
        monkeypatch.setattr(
            anthropic_api.AnthropicClient,
            "_stream",
            staticmethod(lambda _client, _kwargs: anthropic_message()),
        )
        response = anthropic_client.generate("system", "user")
        assert response.text == "by exact h"
        assert response.model == "claude-fable-5"
        assert (response.input_tokens, response.output_tokens) == (11, 7)

    def test_retries_without_an_unavailable_fallback_beta(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_client: anthropic_api.AnthropicClient,
    ) -> None:
        calls: list[dict[str, Any]] = []

        def stream(_client: object, kwargs: dict[str, Any]) -> SimpleNamespace:
            calls.append(kwargs)
            if len(calls) == 1:
                raise FakeBadRequestError("server-side fallback is unavailable")
            return anthropic_message()

        monkeypatch.setattr(anthropic_api.AnthropicClient, "_stream", staticmethod(stream))
        assert anthropic_client.generate("system", "user").text == "by exact h"
        assert "fallbacks" in calls[0]
        assert "fallbacks" not in calls[1]
        assert anthropic_client._fallbacks_enabled is False

    def test_fallback_retry_still_translates_transport_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_client: anthropic_api.AnthropicClient,
    ) -> None:
        calls = 0

        def stream(_client: object, _kwargs: dict[str, Any]) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise FakeBadRequestError("fallback beta unavailable")
            raise FakeAPIConnectionError("offline")

        monkeypatch.setattr(anthropic_api.AnthropicClient, "_stream", staticmethod(stream))
        with pytest.raises(LLMError, match="unreachable"):
            anthropic_client.generate("system", "user")

    @pytest.mark.parametrize(
        ("message", "error"),
        [
            (anthropic_message(stop_reason="refusal"), "declined"),
            (anthropic_message(stop_reason="max_tokens"), "output limit"),
            (anthropic_message(text=""), "empty completion"),
        ],
    )
    def test_rejects_non_completions(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_client: anthropic_api.AnthropicClient,
        message: SimpleNamespace,
        error: str,
    ) -> None:
        monkeypatch.setattr(
            anthropic_api.AnthropicClient,
            "_stream",
            staticmethod(lambda _client, _kwargs: message),
        )
        with pytest.raises(LLMError, match=error):
            anthropic_client.generate("system", "user")

    def test_official_sdk_serializes_the_messages_contract(self) -> None:
        anthropic = pytest.importorskip("anthropic")
        requests: list[httpx.Request] = []
        events = (
            "\n\n".join(
                [
                    "event: message_start\n"
                    'data: {"type":"message_start","message":{"id":"msg_test",'
                    '"type":"message","role":"assistant","content":[],"model":'
                    '"claude-fable-5","stop_reason":null,"stop_sequence":null,'
                    '"usage":{"input_tokens":1,"output_tokens":0}}}',
                    "event: content_block_start\n"
                    'data: {"type":"content_block_start","index":0,"content_block":'
                    '{"type":"text","text":""}}',
                    "event: content_block_delta\n"
                    'data: {"type":"content_block_delta","index":0,"delta":'
                    '{"type":"text_delta","text":"rfl"}}',
                    'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}',
                    "event: message_delta\n"
                    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn",'
                    '"stop_sequence":null},"usage":{"output_tokens":1}}',
                    'event: message_stop\ndata: {"type":"message_stop"}',
                ]
            )
            + "\n\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=events,
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        sdk = anthropic.Anthropic(
            api_key="test",
            base_url="https://api.anthropic.test",
            http_client=http_client,
        )
        client = anthropic_api.AnthropicClient(
            config=LLMConfig(
                model="claude-fable-5",
                backend="anthropic",
                effort="high",
            ),
            _sdk_client=sdk,
        )
        assert client.generate("system", "user").text == "rfl"
        body = json.loads(requests[0].read())
        assert requests[0].url.path.endswith("/v1/messages")
        assert body["thinking"] == {"type": "adaptive"}
        assert body["output_config"] == {"effort": "high"}
        assert body["fallbacks"] == "default"
        client.close()


@dataclass
class FakeResponses:
    response: SimpleNamespace
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return self.response


@dataclass
class FakeOpenAIClient:
    responses: FakeResponses
    closed: bool = False

    def close(self) -> None:
        self.closed = True


def openai_response(
    *,
    status: str | None = "completed",
    text: str = "by exact h",
    reason: str | None = None,
    error: object | None = None,
) -> SimpleNamespace:
    details = SimpleNamespace(reason=reason) if reason else None
    return SimpleNamespace(
        status=status,
        output_text=text,
        incomplete_details=details,
        error=error,
        usage=SimpleNamespace(input_tokens=13, output_tokens=5),
        model="gpt-5.6-sol",
    )


@pytest.fixture()
def openai_client(monkeypatch: pytest.MonkeyPatch) -> openai_api.OpenAIClient:
    monkeypatch.setattr(openai_api, "_require_sdk", lambda: OPENAI_SDK)
    sdk_client = FakeOpenAIClient(FakeResponses(openai_response()))
    return openai_api.OpenAIClient(
        config=LLMConfig(
            model="gpt-5.6-sol",
            backend="openai",
            effort="xhigh",
        ),
        _sdk_client=sdk_client,
    )


class TestOpenAIClient:
    def test_builds_current_responses_request(self, openai_client: openai_api.OpenAIClient) -> None:
        response = openai_client.generate("system", "user", temperature=0.7, stop=["STOP"])
        calls = openai_client._sdk_client.responses.calls
        assert response.text == "by exact h"
        assert calls == [
            {
                "model": "gpt-5.6-sol",
                "instructions": "system",
                "input": "user",
                "max_output_tokens": 32768,
                "store": False,
                "reasoning": {"effort": "xhigh"},
            }
        ]

    def test_builds_native_pdf_responses_input(
        self,
        openai_client: openai_api.OpenAIClient,
    ) -> None:
        document = DocumentInput("paper.pdf", "application/pdf", b"%PDF")

        response = openai_client.generate_with_documents(
            "system",
            "read the paper",
            (document,),
        )

        assert response.text == "by exact h"
        request_input = openai_client._sdk_client.responses.calls[0]["input"]
        assert request_input == [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_file",
                        "filename": "paper.pdf",
                        "file_data": "data:application/pdf;base64,JVBERg==",
                    },
                    {"type": "input_text", "text": "read the paper"},
                ],
            }
        ]

    @pytest.mark.parametrize(
        ("response", "error"),
        [
            (openai_response(status="incomplete", reason="content_filter"), "declined"),
            (
                openai_response(status="incomplete", reason="max_output_tokens"),
                "output limit",
            ),
            (
                openai_response(status="failed", error=SimpleNamespace(message="provider failed")),
                "provider failed",
            ),
            (openai_response(status="completed", text=""), "empty completion"),
            (openai_response(status=None), "no status"),
        ],
    )
    def test_rejects_non_completions(
        self,
        openai_client: openai_api.OpenAIClient,
        response: SimpleNamespace,
        error: str,
    ) -> None:
        openai_client._sdk_client.responses.response = response
        with pytest.raises(LLMError, match=error):
            openai_client.generate("system", "user")

    def test_close_releases_the_sdk_client(self, openai_client: openai_api.OpenAIClient) -> None:
        sdk_client = openai_client._sdk_client
        openai_client.close()
        assert sdk_client.closed is True
        assert openai_client._sdk_client is None

    def test_official_sdk_serializes_the_responses_contract(self) -> None:
        openai = pytest.importorskip("openai")
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "id": "resp_test",
                    "object": "response",
                    "created_at": 0,
                    "status": "completed",
                    "error": None,
                    "incomplete_details": None,
                    "model": "gpt-5.6-sol",
                    "output": [
                        {
                            "id": "msg_test",
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "rfl",
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    "usage": {
                        "input_tokens": 1,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens": 1,
                        "output_tokens_details": {"reasoning_tokens": 0},
                        "total_tokens": 2,
                    },
                },
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        sdk = openai.OpenAI(
            api_key="test",
            base_url="https://api.openai.test/v1",
            http_client=http_client,
        )
        client = openai_api.OpenAIClient(
            config=LLMConfig(
                model="gpt-5.6-sol",
                backend="openai",
                effort="xhigh",
            ),
            _sdk_client=sdk,
        )
        assert client.generate("system", "user").text == "rfl"
        body = json.loads(requests[0].read())
        assert requests[0].url.path.endswith("/v1/responses")
        assert body["instructions"] == "system"
        assert body["reasoning"] == {"effort": "xhigh"}
        assert body["store"] is False
        client.close()
