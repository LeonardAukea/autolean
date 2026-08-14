"""Tests for the LLM backend layer — base types, registry, and HTTP backends."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from autolean.llm import (
    BACKENDS,
    Capabilities,
    LLMBackend,
    LLMConfig,
    LLMError,
    LLMResponse,
    create_llm_client,
)
from autolean.llm.muse_glimmer import DEFAULT_MUSE_GLIMMER_URL, MuseGlimmerClient
from autolean.llm.ollama import DEFAULT_OLLAMA_URL, OllamaClient, probe_installed_models
from autolean.llm.openai_compat import DEFAULT_OPENAI_COMPAT_URL, OpenAICompatibleClient

# ---------------------------------------------------------------------------
# Base types
# ---------------------------------------------------------------------------


class TestLLMResponse:
    def test_tokens_per_second(self) -> None:
        r = LLMResponse(text="x", model="m", output_tokens=120, duration_seconds=4.0)
        assert r.tokens_per_second == pytest.approx(30.0)

    def test_tokens_per_second_without_duration_is_zero(self) -> None:
        """A backend that reports no timing must not divide by zero."""
        r = LLMResponse(text="x", model="m", output_tokens=120)
        assert r.tokens_per_second == 0.0


class TestLLMConfig:
    def test_override_wins_over_configured_temperature(self) -> None:
        cfg = LLMConfig(model="m", temperature=0.4)
        assert cfg.resolved_temperature(0.9) == pytest.approx(0.9)

    def test_falls_back_to_configured_temperature(self) -> None:
        cfg = LLMConfig(model="m", temperature=0.4)
        assert cfg.resolved_temperature(None) == pytest.approx(0.4)

    def test_none_temperature_stays_none(self) -> None:
        cfg = LLMConfig(model="m", temperature=None)
        assert cfg.resolved_temperature(None) is None

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"model": ""}, "model"),
            ({"model": "m", "max_output_tokens": 0}, "max_output_tokens"),
            ({"model": "m", "timeout": 0}, "timeout"),
            ({"model": "m", "temperature": -0.1}, "temperature"),
            ({"model": "m", "base_url": "http://user:secret@localhost:8000"}, "credentials"),
            ({"model": "m", "seed": -1}, "seed"),
            ({"model": "m", "model_revision": ""}, "model_revision"),
            ({"model": "m", "model_artifact_sha256": "bad"}, "model_artifact_sha256"),
        ],
    )
    def test_invalid_request_configuration_fails_early(self, kwargs: dict[str, object], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            LLMConfig(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    @pytest.mark.parametrize("name", sorted(BACKENDS))
    def test_every_registered_backend_constructs(self, name: str) -> None:
        client = create_llm_client(LLMConfig(model="test-model", backend=name))
        assert isinstance(client, LLMBackend)
        assert client.config.backend == name

    @pytest.mark.parametrize("name", sorted(BACKENDS))
    def test_every_backend_declares_capabilities(self, name: str) -> None:
        client = create_llm_client(LLMConfig(model="test-model", backend=name))
        assert isinstance(client.capabilities, Capabilities)

    def test_unknown_backend_names_the_alternatives(self) -> None:
        with pytest.raises(LLMError, match="Unknown backend 'nope'"):
            create_llm_client(LLMConfig(model="m", backend="nope"))

    def test_backend_specs_cover_every_loader(self) -> None:
        from autolean.llm.registry import BACKENDS

        assert all(callable(spec.loader) for spec in BACKENDS.values())

    @pytest.mark.parametrize(
        ("backend", "effort"),
        [
            ("anthropic", "max"),
            ("claude_cli", "xhigh"),
            ("openai", "none"),
            ("codex_cli", "max"),
            ("muse_glimmer", "low"),
        ],
    )
    def test_backend_accepts_only_declared_reasoning_effort(
        self,
        backend: str,
        effort: str,
    ) -> None:
        client = create_llm_client(LLMConfig(model="m", backend=backend, effort=effort))
        assert effort in client.capabilities.effort_values

    @pytest.mark.parametrize(
        ("backend", "effort"),
        [("ollama", "high"), ("anthropic", "none"), ("muse_glimmer", "max")],
    )
    def test_backend_rejects_unsupported_reasoning_effort(
        self,
        backend: str,
        effort: str,
    ) -> None:
        with pytest.raises(LLMError, match="reasoning effort"):
            create_llm_client(LLMConfig(model="m", backend=backend, effort=effort))

    def test_context_manager_closes(self) -> None:
        with create_llm_client(LLMConfig(model="m", backend="ollama")) as llm:
            assert llm.config.model == "m"


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


@pytest.fixture()
def ollama() -> OllamaClient:
    return OllamaClient(config=LLMConfig(model="gemma4:26b", backend="ollama"))


class TestOllamaPing:
    @respx.mock
    def test_ping_true_when_model_is_pulled(self, ollama: OllamaClient) -> None:
        respx.get(f"{DEFAULT_OLLAMA_URL}/api/tags").mock(
            return_value=httpx.Response(200, json={"models": [{"name": "gemma4:26b"}]})
        )
        assert ollama.ping() is True

    @respx.mock
    def test_ping_false_when_model_is_missing(self, ollama: OllamaClient) -> None:
        respx.get(f"{DEFAULT_OLLAMA_URL}/api/tags").mock(
            return_value=httpx.Response(200, json={"models": [{"name": "llama3"}]})
        )
        assert ollama.ping() is False

    @respx.mock
    def test_ping_false_when_server_is_down(self, ollama: OllamaClient) -> None:
        respx.get(f"{DEFAULT_OLLAMA_URL}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
        assert ollama.ping() is False

    @respx.mock
    def test_ping_false_for_malformed_envelope(self, ollama: OllamaClient) -> None:
        respx.get(f"{DEFAULT_OLLAMA_URL}/api/tags").mock(return_value=httpx.Response(200, json=[]))
        assert ollama.ping() is False


class TestOllamaGenerate:
    @respx.mock
    def test_parses_text_and_token_counts(self, ollama: OllamaClient) -> None:
        respx.post(f"{DEFAULT_OLLAMA_URL}/api/chat").mock(
            return_value=httpx.Response(
                200,
                json={
                    "model": "gemma4:26b",
                    "message": {"content": "  rfl  "},
                    "prompt_eval_count": 11,
                    "eval_count": 3,
                },
            )
        )
        resp = ollama.generate("sys", "user")
        assert resp.text == "rfl"
        assert (resp.input_tokens, resp.output_tokens) == (11, 3)
        assert resp.model == "gemma4:26b"

    @respx.mock
    def test_sends_temperature_and_stop(self, ollama: OllamaClient) -> None:
        route = respx.post(f"{DEFAULT_OLLAMA_URL}/api/chat").mock(
            return_value=httpx.Response(200, json={"message": {"content": "ok"}})
        )
        ollama.generate("sys", "user", temperature=0.9, stop=["\n\n"])
        sent = json.loads(route.calls.last.request.read())
        assert sent["options"]["temperature"] == pytest.approx(0.9)
        assert sent["options"]["stop"] == ["\n\n"]
        assert sent["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
        ]

    @respx.mock
    def test_missing_token_counts_default_to_zero(self, ollama: OllamaClient) -> None:
        """Some builds omit the counters; a completion must still succeed."""
        respx.post(f"{DEFAULT_OLLAMA_URL}/api/chat").mock(
            return_value=httpx.Response(200, json={"message": {"content": "simp"}})
        )
        resp = ollama.generate("sys", "user")
        assert (resp.text, resp.input_tokens, resp.output_tokens) == ("simp", 0, 0)

    @respx.mock
    def test_http_error_becomes_llm_error(self, ollama: OllamaClient) -> None:
        respx.post(f"{DEFAULT_OLLAMA_URL}/api/chat").mock(return_value=httpx.Response(500, text="boom"))
        with pytest.raises(LLMError, match="returned 500"):
            ollama.generate("sys", "user")

    @respx.mock
    def test_connection_error_becomes_llm_error(self, ollama: OllamaClient) -> None:
        respx.post(f"{DEFAULT_OLLAMA_URL}/api/chat").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(LLMError, match="unreachable"):
            ollama.generate("sys", "user")

    @pytest.mark.parametrize(
        "body",
        [[], {"message": []}, {"message": {}}, {"message": {"content": ""}}],
    )
    @respx.mock
    def test_malformed_payload_becomes_llm_error(self, ollama: OllamaClient, body: object) -> None:
        respx.post(f"{DEFAULT_OLLAMA_URL}/api/chat").mock(return_value=httpx.Response(200, json=body))
        with pytest.raises(LLMError):
            ollama.generate("sys", "user")


class TestProbeInstalledModels:
    @respx.mock
    def test_returns_pulled_model_names(self) -> None:
        respx.get(f"{DEFAULT_OLLAMA_URL}/api/tags").mock(
            return_value=httpx.Response(200, json={"models": [{"name": "a"}, {"name": "b"}]})
        )
        assert probe_installed_models() == {"a", "b"}

    @respx.mock
    def test_returns_empty_set_when_ollama_is_down(self) -> None:
        respx.get(f"{DEFAULT_OLLAMA_URL}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
        assert probe_installed_models() == set()


# ---------------------------------------------------------------------------
# OpenAI-compatible
# ---------------------------------------------------------------------------


@pytest.fixture()
def compat() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(config=LLMConfig(model="local-model", backend="openai_compat"))


class TestOpenAICompatible:
    @respx.mock
    def test_parses_choices_and_usage(self, compat: OpenAICompatibleClient) -> None:
        respx.post(f"{DEFAULT_OPENAI_COMPAT_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "model": "local-model",
                    "choices": [{"message": {"content": "omega"}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 2},
                },
            )
        )
        resp = compat.generate("sys", "user")
        assert resp.text == "omega"
        assert (resp.input_tokens, resp.output_tokens) == (7, 2)

    @respx.mock
    def test_empty_choices_are_rejected(self, compat: OpenAICompatibleClient) -> None:
        respx.post(f"{DEFAULT_OPENAI_COMPAT_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": []})
        )
        with pytest.raises(LLMError, match="no choices"):
            compat.generate("sys", "user")

    @respx.mock
    def test_ping_accepts_a_server_listing_no_models(self, compat: OpenAICompatibleClient) -> None:
        """An empty list can represent a reachable single-model server."""
        respx.get(f"{DEFAULT_OPENAI_COMPAT_URL}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        assert compat.ping() is True

    @respx.mock
    def test_ping_false_when_unreachable(self, compat: OpenAICompatibleClient) -> None:
        respx.get(f"{DEFAULT_OPENAI_COMPAT_URL}/v1/models").mock(side_effect=httpx.ConnectError("refused"))
        assert compat.ping() is False

    @respx.mock
    def test_ping_false_when_configured_model_is_absent(self, compat: OpenAICompatibleClient) -> None:
        respx.get(f"{DEFAULT_OPENAI_COMPAT_URL}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "other"}]})
        )
        assert compat.ping() is False

    @pytest.mark.parametrize(
        "body",
        [
            [],
            {"choices": "bad"},
            {"choices": ["bad"]},
            {"choices": [{"message": []}]},
            {"choices": [{"message": {"content": ""}}]},
            {"choices": [{"message": {"content": "rfl"}}], "usage": []},
        ],
    )
    @respx.mock
    def test_malformed_completion_becomes_llm_error(
        self, compat: OpenAICompatibleClient, body: object
    ) -> None:
        respx.post(f"{DEFAULT_OPENAI_COMPAT_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=body)
        )
        with pytest.raises(LLMError):
            compat.generate("sys", "user")


# ---------------------------------------------------------------------------
# Muse Glimmer
# ---------------------------------------------------------------------------


@pytest.fixture()
def muse_glimmer() -> MuseGlimmerClient:
    return MuseGlimmerClient(
        config=LLMConfig(
            model="muse-glimmer",
            backend="muse_glimmer",
            temperature=0.0,
            effort="high",
            seed=0,
        )
    )


class TestMuseGlimmer:
    @respx.mock
    def test_sends_deterministic_reasoning_request(
        self,
        muse_glimmer: MuseGlimmerClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MUSE_GLIMMER_API_KEY", "local-secret")
        route = respx.post(f"{DEFAULT_MUSE_GLIMMER_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "model": "muse-glimmer",
                    "choices": [{"message": {"content": "rfl"}}],
                    "usage": {"prompt_tokens": 21, "completion_tokens": 1},
                },
            )
        )

        response = muse_glimmer.generate("Lean system", "prove it")

        payload = json.loads(route.calls.last.request.read())
        assert route.calls.last.request.headers["Authorization"] == "Bearer local-secret"
        assert payload["temperature"] == pytest.approx(0.0)
        assert payload["seed"] == 0
        assert payload["chat_template_kwargs"] == {"reasoning_strength": "high"}
        assert payload["messages"] == [
            {"role": "system", "content": "Lean system"},
            {"role": "user", "content": "prove it"},
        ]
        assert response.text == "rfl"
        assert (response.input_tokens, response.output_tokens) == (21, 1)

    def test_rejects_end_of_message_as_stop_token(self, muse_glimmer: MuseGlimmerClient) -> None:
        with pytest.raises(LLMError, match="cannot be a stop token"):
            muse_glimmer.generate("system", "user", stop=["<|eom|>"])

    def test_rejects_unsupported_reasoning_effort(self) -> None:
        client = MuseGlimmerClient(
            config=LLMConfig(model="muse-glimmer", backend="muse_glimmer", effort="max")
        )

        with pytest.raises(LLMError, match="reasoning effort"):
            client.generate("system", "user")
