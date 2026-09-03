"""Every real implementer of the `RuntimeAdapter` Protocol
(backend/src/oc8/runtime/adapter.py) must accept the Protocol's full
keyword-parameter set. `executor.py` dispatches through the generic
`runtime_adapter.execute(...)` call site -- if any implementer's `execute` is
missing a parameter the Protocol declares, that call raises `TypeError` for
every tenant routed through it.

This exact bug shipped once already in this task's own fix-round:
`DockerIsolatedRuntime.execute` was initially missing the new
`task_images_raw` kwarg and would have raised `TypeError` for every isolated-
runtime tenant. It was caught only by the implementer's own reasoning while
writing the code, not by any test -- this file closes that gap.

`RuntimeAdapter` is a plain `typing.Protocol` (not `@runtime_checkable`), so it
can't be structurally checked with `isinstance()`. Instead of hardcoding the
three known implementers and hoping nobody adds a fourth one silently, this
scans the two modules that define them for ANY class with its own `execute`
method, so a fourth implementer added later to either module is picked up
automatically and checked the same way -- forgetting a parameter on it fails
this test loudly, rather than the gap being found by luck (or a production
TypeError) again.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from oc8.runtime import adapter as adapter_module
from oc8.runtime import isolated as isolated_module
from oc8.runtime.adapter import RuntimeAdapter

# Classes that are part of the Protocol machinery itself, not implementers.
_NOT_IMPLEMENTERS = {"RuntimeAdapter", "EvidenceProducingRuntime"}


def _protocol_execute_param_names() -> list[str]:
    sig = inspect.signature(RuntimeAdapter.execute)
    return [name for name in sig.parameters if name != "self"]


def _discover_implementers() -> dict[str, type[Any]]:
    """Every class defined (not merely imported) in adapter.py/isolated.py
    that has its own `execute` method, minus the Protocol classes themselves."""
    found: dict[str, type[Any]] = {}
    for module in (adapter_module, isolated_module):
        for name, obj in vars(module).items():
            if not inspect.isclass(obj):
                continue
            if obj.__module__ != module.__name__:
                continue  # imported from elsewhere, not defined in this module
            if name in _NOT_IMPLEMENTERS:
                continue
            if "execute" not in obj.__dict__:
                continue
            found[name] = obj
    return found


PROTOCOL_PARAMS = _protocol_execute_param_names()
IMPLEMENTERS = _discover_implementers()


def test_known_implementers_are_exactly_the_expected_three() -> None:
    """Pins the implementer set this task's fix-round knows about. A change
    here means either a new `execute`-defining class was added to
    adapter.py/isolated.py (good -- it will also be checked below
    automatically) or one of the three known ones was renamed/removed."""
    assert set(IMPLEMENTERS) == {"Oc8AgentRuntime", "EchoRuntimeStub", "DockerIsolatedRuntime"}


def test_protocol_declares_the_parameters_this_test_expects() -> None:
    """Sanity check on the Protocol itself, so a failure below can't be
    blamed on this test having read the wrong signature."""
    assert "task_images_raw" in PROTOCOL_PARAMS
    assert "task_text" in PROTOCOL_PARAMS
    assert "tenant_id" in PROTOCOL_PARAMS


@pytest.mark.parametrize("name", sorted(IMPLEMENTERS))
def test_implementer_execute_accepts_every_protocol_parameter(name: str) -> None:
    impl = IMPLEMENTERS[name]
    impl_sig = inspect.signature(impl.execute)
    impl_params = {p for p in impl_sig.parameters if p != "self"}
    has_var_keyword = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in impl_sig.parameters.values()
    )

    missing = [p for p in PROTOCOL_PARAMS if p not in impl_params]
    assert not missing or has_var_keyword, (
        f"{name}.execute is missing RuntimeAdapter Protocol parameter(s) {missing}. "
        f"The generic `runtime_adapter.execute(...)` dispatch in "
        f"backend/src/oc8/runtime/executor.py would raise TypeError for every "
        f"run routed through {name}."
    )
