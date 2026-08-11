"""Shared httpx plumbing for the self-hosted backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from autolean.llm.base import BaseBackend, LLMError

CONNECT_TIMEOUT = 10.0


def as_int(value: object) -> int:
    """Coerce a JSON number to int; anything else counts as zero.

    Token counts are optional in several server implementations, and a
    missing count must not fail a completion that otherwise succeeded.
    """
    return value if isinstance(value, int) else 0


def as_object(value: object, context: str) -> dict[str, Any]:
    """Require a JSON object at a provider contract boundary."""
    if not isinstance(value, dict):
        raise LLMError(f"{context} must be a JSON object")
    return value


def as_list(value: object, context: str) -> list[Any]:
    """Require a JSON array at a provider contract boundary."""
    if not isinstance(value, list):
        raise LLMError(f"{context} must be a JSON array")
    return value


def as_text(value: object, context: str) -> str:
    """Require non-empty text at a provider contract boundary."""
    if not isinstance(value, str) or not value.strip():
        raise LLMError(f"{context} must be non-empty text")
    return value.strip()


@dataclass
class HttpBackend(BaseBackend):
    """A backend that talks to an HTTP server over a pooled connection."""

    _client: httpx.Client | None = field(default=None, repr=False)

    #: Used when `config.base_url` is unset.
    default_base_url: str = ""

    @property
    def base_url(self) -> str:
        return self.config.base_url or self.default_base_url

    def request_headers(self) -> dict[str, str]:
        """Headers applied to every request made by this backend."""
        return {}

    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.config.timeout, connect=CONNECT_TIMEOUT),
                headers=self.request_headers(),
            )
        return self._client

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST `payload` and return the decoded body, or raise `LLMError`."""
        try:
            resp = self.client().post(path, json=payload)
            resp.raise_for_status()
            return as_object(resp.json(), f"{self.base_url}{path} response")
        except httpx.HTTPStatusError as e:
            detail = e.response.text[:500]
            raise LLMError(f"{self.base_url}{path} returned {e.response.status_code}: {detail}") from e
        except httpx.HTTPError as e:
            raise LLMError(f"{self.base_url}{path} unreachable: {e}") from e
        except ValueError as e:  # malformed JSON body
            raise LLMError(f"{self.base_url}{path} returned invalid JSON: {e}") from e

    def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            self._client.close()
        self._client = None
