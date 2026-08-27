from __future__ import annotations

from oc8.config import Settings
from oc8.modelrouter.registry import build_adapter, provider_infos


def test_tenant_key_overrides_the_settings_key() -> None:
    s = Settings(anthropic_api_key="sk-platform")
    adapter = build_adapter("anthropic", s, None, "sk-tenant")
    # The adapter stores the key it was built with.
    assert adapter._api_key == "sk-tenant"  # type: ignore[attr-defined]


def test_platform_key_used_when_no_tenant_key() -> None:
    s = Settings(anthropic_api_key="sk-platform")
    adapter = build_adapter("anthropic", s, None, None)
    assert adapter._api_key == "sk-platform"  # type: ignore[attr-defined]


def test_openai_tenant_key_overrides() -> None:
    s = Settings(openai_api_key="sk-platform")
    adapter = build_adapter("openai", s, None, "sk-tenant")
    assert adapter._api_key == "sk-tenant"  # type: ignore[attr-defined]


def test_ollama_ignores_the_key() -> None:
    # No crash, key simply unused.
    adapter = build_adapter("ollama", Settings(), None, "sk-tenant")
    assert adapter is not None


def test_availability_true_with_tenant_key_and_empty_platform() -> None:
    # A tenant key makes a cloud provider available even when the platform env
    # key is empty -- decision 5.
    infos = provider_infos(Settings(anthropic_api_key=""), tenant_keys={"anthropic": "sk-t"})
    anthropic = next(i for i in infos if i["canonical"] == "anthropic")
    assert anthropic["available"] is True


def test_availability_false_with_no_key_anywhere() -> None:
    infos = provider_infos(Settings(anthropic_api_key=""), tenant_keys={})
    anthropic = next(i for i in infos if i["canonical"] == "anthropic")
    assert anthropic["available"] is False
