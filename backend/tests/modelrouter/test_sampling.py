"""Where a run's sampling parameters come from.

Both runtimes used to hardcode ModelParams(temperature=0.0, max_tokens=512) and
ModelConfig.params was read only for base_url. So an operator could not make an
agent less repetitive or give it room for a longer tool call without a code
change -- temperature 0.0 means the same task produces byte-identical output
forever, which is exactly what it looked like in the Odoo demo.
"""

from __future__ import annotations

import uuid
from typing import Any

from oc8 import models as m
from oc8.modelrouter.sampling import DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE, resolve_params


def _agent(definition: dict[str, Any] | None = None) -> m.Agent:
    return m.Agent(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), department_id=uuid.uuid4(),
        name="Nora", status="idle", definition=definition or {}, presentation={},
    )


def _config(params: dict[str, Any] | None) -> m.ModelConfig:
    return m.ModelConfig(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), provider="opaas_ai",
        model="opaas_ai:odoo-gpt", params=params if params is not None else {},
    )


def test_without_any_configuration_the_framework_default_applies() -> None:
    p = resolve_params(None, agent=_agent())
    assert p.temperature == DEFAULT_TEMPERATURE
    assert p.max_tokens == DEFAULT_MAX_TOKENS


def test_the_model_config_can_set_both() -> None:
    p = resolve_params(_config({"temperature": 0.6, "max_tokens": 2048}), agent=_agent())
    assert p.temperature == 0.6
    assert p.max_tokens == 2048


def test_base_url_in_params_is_not_mistaken_for_a_sampling_knob() -> None:
    """params also carries transport config; only the sampling keys are read."""
    p = resolve_params(_config({"base_url": "http://x:1234"}), agent=_agent())
    assert p.temperature == DEFAULT_TEMPERATURE
    assert p.max_tokens == DEFAULT_MAX_TOKENS


def test_the_agent_overrides_its_model_config() -> None:
    """One model config is shared by many agents; a single agent must be tunable
    without changing sampling for everyone else on that model."""
    p = resolve_params(
        _config({"temperature": 0.1, "max_tokens": 512}),
        agent=_agent({"model_params": {"temperature": 0.8, "max_tokens": 4096}}),
    )
    assert p.temperature == 0.8
    assert p.max_tokens == 4096


def test_a_partial_override_keeps_the_other_value() -> None:
    p = resolve_params(
        _config({"temperature": 0.3, "max_tokens": 2048}),
        agent=_agent({"model_params": {"temperature": 0.9}}),
    )
    assert p.temperature == 0.9
    assert p.max_tokens == 2048, "an unset key must not fall back past the model config"


def test_a_nonsense_value_falls_back_instead_of_failing_the_run() -> None:
    """params is free-form JSONB an operator edits. A typo there must not take a
    run down -- it degrades to the default."""
    p = resolve_params(_config({"temperature": "warm", "max_tokens": None}), agent=_agent())
    assert p.temperature == DEFAULT_TEMPERATURE
    assert p.max_tokens == DEFAULT_MAX_TOKENS


def test_out_of_range_values_are_clamped_not_passed_through() -> None:
    """Providers reject temperature > 2 (or negative) with a hard error, which
    would surface as a failed run rather than a misconfiguration."""
    hot = resolve_params(_config({"temperature": 9.5}), agent=_agent())
    cold = resolve_params(_config({"temperature": -1.0}), agent=_agent())
    assert hot.temperature == 2.0
    assert cold.temperature == 0.0


def test_a_non_positive_token_budget_falls_back() -> None:
    for bad in (0, -100):
        p = resolve_params(_config({"max_tokens": bad}), agent=_agent())
        assert p.max_tokens == DEFAULT_MAX_TOKENS


def test_an_agent_without_a_definition_is_fine() -> None:
    agent = _agent()
    agent.definition = {}
    p = resolve_params(_config({"temperature": 0.4}), agent=agent)
    assert p.temperature == 0.4


def test_no_agent_at_all_still_resolves() -> None:
    """The coding loop and other callers may not have an Agent to hand."""
    p = resolve_params(_config({"temperature": 0.5}), agent=None)
    assert p.temperature == 0.5
