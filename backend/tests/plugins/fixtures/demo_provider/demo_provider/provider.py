"""A minimal ModelAdapter contributed by a plugin."""

from __future__ import annotations

from typing import Any

from oc8.modelrouter.types import CompletionRequest, CompletionResult, Usage


class DemoAdapter:
    async def complete(self, req: CompletionRequest) -> CompletionResult:
        return CompletionResult(
            text="hello from a plugin provider",
            tool_calls=[],
            usage=Usage(tokens_in=1, tokens_out=1),
            stop_reason="end_turn",
            provider="demo-llm",
            model=req.model,
        )


def register(contrib: Any) -> None:
    contrib.add_model_provider(
        canonical="demo-llm",
        locality="cloud",
        factory=lambda _settings, _base_url, _key: DemoAdapter(),
        available=lambda _settings, _key: True,
        aliases=("demo",),
    )
