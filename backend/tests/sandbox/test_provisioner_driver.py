from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

import oc8.sandbox as sandbox
from oc8.config import Settings, get_settings
from oc8.sandbox.provisioner_driver import ProvisionerSandboxDriver
from oc8.sandbox.types import BindMount, SandboxError, SandboxHandle, SandboxSpec


def _driver(handler: httpx.MockTransport) -> ProvisionerSandboxDriver:
    return ProvisionerSandboxDriver(
        base_url="http://provisioner",
        token="test-token",
        client=httpx.AsyncClient(transport=handler, base_url="http://provisioner"),
    )


@pytest.mark.asyncio
async def test_provision_serializes_only_the_sandbox_spec() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["authorization"]
        seen["json"] = json.loads(request.content)
        return httpx.Response(201, json={"container_id": "opaque-handle", "image": "image:1"})

    driver = _driver(httpx.MockTransport(handler))
    handle = await driver.provision(
        SandboxSpec(
            image="image:1",
            env={"RUN_TOKEN": "scoped"},
            workdir="/workspace",
            command=["sleep", "60"],
            mounts=[BindMount("/safe/session", "/workspace", readonly=False)],
        )
    )
    await driver.aclose()

    assert handle == SandboxHandle(container_id="opaque-handle", image="image:1")
    assert seen["authorization"] == "Bearer test-token"
    assert seen["json"] == {
        "image": "image:1",
        "env": {"RUN_TOKEN": "scoped"},
        "workdir": "/workspace",
        "command": ["sleep", "60"],
        "mem_limit": "1g",
        "pids_limit": 512,
        "cpu_limit": 2.0,
        "network_disabled": True,
        "cap_drop": ["ALL"],
        "runtime": None,
        "read_only": False,
        "network": None,
        "mounts": [
            {"host_path": "/safe/session", "container_path": "/workspace", "readonly": False}
        ],
        "labels": {},
        "name": None,
        "user": None,
    }


@pytest.mark.asyncio
async def test_driver_maps_non_success_response_to_sandbox_error() -> None:
    driver = _driver(httpx.MockTransport(lambda _request: httpx.Response(404, text="not found")))

    with pytest.raises(SandboxError, match="provisioner"):
        await driver.teardown(SandboxHandle("missing", "image"))
    await driver.aclose()


@pytest.mark.asyncio
async def test_driver_uses_typed_exec_and_file_endpoints() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/exec"):
            return httpx.Response(200, json={"exit_code": 7, "output": "nope"})
        if request.url.path.endswith("/fs/read"):
            return httpx.Response(
                200, json={"content_base64": base64.b64encode(b"content").decode()}
            )
        return httpx.Response(204)

    driver = _driver(httpx.MockTransport(handler))
    handle = SandboxHandle("opaque", "image")
    result = await driver.exec(handle, ["sh", "-c", "exit 7"], workdir="/work", timeout=5)
    await driver.fs_write(handle, "/work/file", b"payload")
    assert await driver.fs_read(handle, "/work/file") == b"content"
    await driver.aclose()

    assert result.exit_code == 7
    assert requests == [
        (
            "/v1/sandboxes/opaque/exec",
            {"command": ["sh", "-c", "exit 7"], "workdir": "/work", "timeout": 5},
        ),
        (
            "/v1/sandboxes/opaque/fs/write",
            {"path": "/work/file", "content_base64": base64.b64encode(b"payload").decode()},
        ),
        ("/v1/sandboxes/opaque/fs/read", {"path": "/work/file"}),
    ]


def test_factory_selects_the_provisioner_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OC8_SANDBOX_DRIVER", "provisioner")
    monkeypatch.setenv("OC8_SANDBOX_PROVISIONER_TOKEN", "test-token")
    get_settings.cache_clear()
    previous = sandbox._driver
    sandbox._driver = None
    try:
        assert isinstance(sandbox.get_sandbox_driver(), ProvisionerSandboxDriver)
    finally:
        driver = sandbox._driver
        sandbox._driver = previous
        get_settings.cache_clear()
        if isinstance(driver, ProvisionerSandboxDriver):
            asyncio.run(driver.aclose())


def test_provisioner_driver_requires_a_token() -> None:
    with pytest.raises(ValueError, match="sandbox_provisioner_token"):
        Settings(sandbox_driver="provisioner")


def test_provisioner_driver_requires_an_absolute_http_url() -> None:
    with pytest.raises(ValueError, match="sandbox_provisioner_url"):
        Settings(
            sandbox_driver="provisioner",
            sandbox_provisioner_token="test-token",
            sandbox_provisioner_url="runtime-provisioner",
        )
