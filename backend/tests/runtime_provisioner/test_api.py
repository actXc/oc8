from __future__ import annotations

import base64

import httpx
import pytest
from fastapi import FastAPI

from oc8.runtime_provisioner.app import create_provisioner_app
from oc8.runtime_provisioner.policy import ProvisionerPolicy
from oc8.sandbox.types import ExecResult, SandboxHandle, SandboxSpec

pytestmark = pytest.mark.asyncio


class FakeDriver:
    def __init__(self) -> None:
        self.provisioned: list[SandboxSpec] = []
        self.removed: list[str] = []
        self.reap_calls = 0

    async def provision(self, spec: SandboxSpec) -> SandboxHandle:
        self.provisioned.append(spec)
        return SandboxHandle("docker-id", spec.image)

    async def exec(
        self, handle: SandboxHandle, command: list[str], **_kwargs: object
    ) -> ExecResult:
        return ExecResult(0, "ok")

    async def fs_write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        assert content == b"payload"

    async def fs_read(self, handle: SandboxHandle, path: str) -> bytes:
        return b"content"

    async def teardown(self, handle: SandboxHandle) -> None:
        self.removed.append(handle.container_id)

    async def wait(self, handle: SandboxHandle, timeout_s: float = 600.0) -> int:
        return 0

    async def logs(self, handle: SandboxHandle) -> str:
        return "logs"

    async def reap_orphans(self) -> int:
        self.reap_calls += 1
        return 2


def _app(driver: FakeDriver) -> FastAPI:
    return create_provisioner_app(
        token="provisioner-token",
        driver=driver,
        policy=ProvisionerPolicy(
            allowed_images={"oc8-runtime:1"},
            agent_network="oc8_agents",
            session_root="/var/lib/oc8/sessions",
            platform_mounts={},
        ),
    )


async def test_api_requires_bearer_token_and_returns_opaque_handle() -> None:
    driver = FakeDriver()
    transport = httpx.ASGITransport(app=_app(driver))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (
            await client.post("/v1/sandboxes", json={"image": "oc8-runtime:1"})
        ).status_code == 401
        response = await client.post(
            "/v1/sandboxes",
            headers={"Authorization": "Bearer provisioner-token"},
            json={"image": "oc8-runtime:1"},
        )

    assert response.status_code == 201
    assert response.json()["container_id"] != "docker-id"
    assert driver.provisioned[0].cap_drop == ["ALL"]


async def test_api_reaps_only_through_the_fixed_driver_operation() -> None:
    driver = FakeDriver()
    transport = httpx.ASGITransport(app=_app(driver))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.post("/v1/reap-orphans")
        reaped = await client.post(
            "/v1/reap-orphans", headers={"Authorization": "Bearer provisioner-token"}
        )

    assert denied.status_code == 401
    assert reaped.json() == {"count": 2}
    assert driver.reap_calls == 1


async def test_api_rejects_unknown_fields_and_policy_violations() -> None:
    transport = httpx.ASGITransport(app=_app(FakeDriver()))
    headers = {"Authorization": "Bearer provisioner-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unknown_field = await client.post(
            "/v1/sandboxes",
            headers=headers,
            json={"image": "oc8-runtime:1", "privileged": True},
        )
        forbidden_image = await client.post(
            "/v1/sandboxes",
            headers=headers,
            json={"image": "attacker:latest"},
        )

    assert unknown_field.status_code == 422
    assert forbidden_image.status_code == 422


async def test_api_serves_only_the_typed_driver_endpoints() -> None:
    driver = FakeDriver()
    transport = httpx.ASGITransport(app=_app(driver))
    headers = {"Authorization": "Bearer provisioner-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/sandboxes", headers=headers, json={"image": "oc8-runtime:1"}
        )
        handle = created.json()["container_id"]
        assert (
            await client.post(
                f"/v1/sandboxes/{handle}/exec", headers=headers, json={"command": ["echo", "ok"]}
            )
        ).json() == {"exit_code": 0, "output": "ok"}
        assert (
            await client.post(
                f"/v1/sandboxes/{handle}/fs/write",
                headers=headers,
                json={"path": "/x", "content_base64": base64.b64encode(b"payload").decode()},
            )
        ).status_code == 204
        assert (
            await client.post(
                f"/v1/sandboxes/{handle}/fs/read", headers=headers, json={"path": "/x"}
            )
        ).json() == {"content_base64": base64.b64encode(b"content").decode()}
        assert (await client.delete(f"/v1/sandboxes/{handle}", headers=headers)).status_code == 204

    assert driver.removed == ["docker-id"]


async def test_api_rejects_invalid_base64_and_unknown_handles() -> None:
    transport = httpx.ASGITransport(app=_app(FakeDriver()))
    headers = {"Authorization": "Bearer provisioner-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/v1/sandboxes/not-a-handle/logs", headers=headers)
        created = await client.post(
            "/v1/sandboxes",
            headers=headers,
            json={"image": "oc8-runtime:1"},
        )
        invalid_base64 = await client.post(
            f"/v1/sandboxes/{created.json()['container_id']}/fs/write",
            headers=headers,
            json={"path": "/x", "content_base64": "not base64!"},
        )

    assert missing.status_code == 404
    assert invalid_base64.status_code == 422
