"""Contribute the `openrouter` model provider (OpenRouter's OpenAI-compatible
completions API, https://openrouter.ai/api/v1).

All specifics of this provider live here in the plugin: the fixed endpoint URL
and the adapter wiring. Unlike `openai_compatible` (a tenant-configurable
gateway), OpenRouter is a single well-known service, so there is no base_url
field to enter -- same shape as the core's `openai_chatgpt` provider, whose
endpoint is likewise fixed. The key is resolved by the core's unified
credentials framework -- a `Credential` row of this plugin's own
`openrouter_api_key` type (credential_types/openrouter_api_key.toml), the
exact same `resolve_model_key` path a core provider's `{canonical}_api_key`
type goes through (oc8.modelrouter.keys) -- and handed to the factory as
`api_key`.
"""

from __future__ import annotations

from typing import Any

from oc8.modelrouter.adapters.openai_compatible import OpenAICompatibleAdapter

BASE_URL = "https://openrouter.ai/api/v1"


def register(contrib: Any) -> None:
    contrib.add_model_provider(
        canonical="openrouter",
        locality="cloud",
        factory=lambda _settings, _base_url, api_key: OpenAICompatibleAdapter(
            BASE_URL, api_key or ""
        ),
        # Available once a tenant has stored its key (BYOK) -- no env-wide
        # fallback, same as opaas_ai_provider: there is no sane shared default.
        available=lambda _settings, key: bool(key),
    )
