# backend/src/oc8/modelrouter/adapters/openai_compatible.py
"""Generic OpenAI-compatible adapter — same wire format as OpenAI's Chat
Completions API, but with a configurable base URL. Covers Mistral (which
exposes an OpenAI-compatible chat endpoint) and any other third-party or
self-hosted OpenAI-API-compatible server, without a near-duplicate
dedicated adapter per provider."""

from __future__ import annotations

from collections.abc import AsyncIterator

from oc8.modelrouter.adapters._openai_common import build_payload, parse_response, post_completion
from oc8.modelrouter.streaming import stream_openai_sse
from oc8.modelrouter.types import CompletionChunk, CompletionRequest, CompletionResult


class OpenAICompatibleAdapter:
    provider = "openai_compatible"

    def __init__(self, base_url: str, api_key: str = "") -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def api_key(self) -> str:
        return self._api_key

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        data = await post_completion(
            f"{self._base_url}/chat/completions", build_payload(req), self._headers()
        )
        return parse_response(data, provider=self.provider, model=req.model)

    async def stream(self, req: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        async for chunk in stream_openai_sse(
            f"{self._base_url}/chat/completions", build_payload(req), self._headers()
        ):
            yield chunk
