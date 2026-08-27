from __future__ import annotations

from oc8.sandbox.types import ExecResult, SandboxError, SandboxHandle, SandboxSpec


def test_spec_defaults() -> None:
    spec = SandboxSpec(image="alpine:latest")
    assert spec.workdir == "/workspace"
    assert spec.command == ["sleep", "3600"]
    assert spec.env == {}


def test_spec_is_frozen() -> None:
    spec = SandboxSpec(image="alpine:latest")
    try:
        spec.image = "other"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("SandboxSpec should be frozen")


def test_exec_result_and_error() -> None:
    r = ExecResult(exit_code=0, output="ok")
    assert r.exit_code == 0 and r.output == "ok"
    assert issubclass(SandboxError, RuntimeError)
    h = SandboxHandle(container_id="abc", image="alpine:latest")
    assert h.container_id == "abc"
