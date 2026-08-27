from __future__ import annotations

from oc8.config import Settings
from oc8.modelrouter.adapters.ollama import OllamaAdapter
from oc8.modelrouter.adapters.openai import OpenAIAdapter
from oc8.modelrouter.adapters.openai_compatible import OpenAICompatibleAdapter
from oc8.modelrouter.router import ModelRouter


def test_resolve_openai_with_key_stays_openai() -> None:
    router = ModelRouter(Settings(openai_api_key="sk-test"))
    canonical, model = router.resolve("openai", "gpt-4o")
    assert canonical == "openai"
    assert model == "gpt-4o"


def test_resolve_openai_without_key_falls_back_to_ollama() -> None:
    router = ModelRouter(Settings(openai_api_key=""))
    canonical, _model = router.resolve("gpt", "gpt-4o")
    assert canonical == "ollama"


def test_resolve_mistral_maps_to_openai_compatible() -> None:
    router = ModelRouter(Settings())
    canonical, model = router.resolve("mistral", "mistral-large-latest")
    assert canonical == "openai_compatible"
    assert model == "mistral-large-latest"


def test_adapter_selects_openai_adapter() -> None:
    router = ModelRouter(Settings(openai_api_key="sk-test"))
    adapter = router._adapter("openai")
    assert isinstance(adapter, OpenAIAdapter)


def test_adapter_selects_openai_compatible_with_request_base_url() -> None:
    router = ModelRouter(Settings())
    adapter = router._adapter("openai_compatible", base_url="https://custom.example.com/v1")
    assert isinstance(adapter, OpenAICompatibleAdapter)


def test_adapter_falls_back_to_settings_mistral_base_url_when_none_given() -> None:
    router = ModelRouter(Settings(mistral_base_url="https://api.mistral.ai/v1"))
    adapter = router._adapter("openai_compatible")
    assert isinstance(adapter, OpenAICompatibleAdapter)


def test_adapter_still_selects_ollama_by_default() -> None:
    router = ModelRouter(Settings())
    adapter = router._adapter("ollama")
    assert isinstance(adapter, OllamaAdapter)
