"""validate_odoo_login is the odoo_login credential type's real
validate_entry_point (§ credential.py's own docstring): a live-observed bug
had it entirely unwired, so the Credentials page's "Test" button reported
success unconditionally without ever asking Odoo. A second live-observed
bug had it call `/web/session/authenticate`, which Odoo refuses for an API
key by design -- so it reported "Access Denied" for credentials that work
fine in real use. These tests exercise the function directly against a
fake httpx transport -- no real Odoo needed."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _evict() -> None:
    for _stale in [n for n in sys.modules if n == "credential" or n.startswith("credential.")]:
        del sys.modules[_stale]


_evict()
sys.path.insert(0, str(PLUGIN_ROOT))

from credential import validate_odoo_login  # noqa: E402

sys.path.remove(str(PLUGIN_ROOT))
_evict()

pytestmark = pytest.mark.asyncio

_VALUES = {
    "url": "http://host.docker.internal:1003",
    "database": "oc8",
    "username": "admin",
    "password": "sk-odoo-test",
}


def _patch_post(monkeypatch: pytest.MonkeyPatch, json_body: dict[str, object]) -> None:
    async def fake_post(
        self: httpx.AsyncClient, url: str, json: dict[str, object] | None = None, **kw: object
    ) -> httpx.Response:
        return httpx.Response(200, json=json_body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


async def test_a_truthy_uid_result_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_post(monkeypatch, {"jsonrpc": "2.0", "id": None, "result": 2})
    await validate_odoo_login(_VALUES)  # must not raise


async def test_an_access_denied_error_body_is_rejected_with_the_real_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JSON-RPC `error` envelope (rare for common.authenticate, but the
    transport can still surface one, e.g. a malformed db name)."""
    _patch_post(
        monkeypatch,
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": 0,
                "message": "Odoo Server Error",
                "data": {"name": "odoo.exceptions.AccessDenied", "message": "Access Denied"},
            },
        },
    )
    with pytest.raises(ValueError, match="Access Denied"):
        await validate_odoo_login(_VALUES)


async def test_a_false_result_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The live-observed shape for bad credentials: HTTP 200, no error
    envelope, `result` is the bare literal `false` -- common.authenticate's
    documented failure return, not an exception."""
    _patch_post(monkeypatch, {"jsonrpc": "2.0", "id": None, "result": False})
    with pytest.raises(ValueError, match="invalid username, password, or API key"):
        await validate_odoo_login(_VALUES)


async def test_unreachable_host_raises_a_clean_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(self: httpx.AsyncClient, *a: object, **kw: object) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", boom)
    with pytest.raises(ValueError, match="could not reach"):
        await validate_odoo_login(_VALUES)


@pytest.mark.parametrize("missing", ["url", "database", "username", "password"])
async def test_each_required_field_is_checked_before_any_network_call(missing: str) -> None:
    values = dict(_VALUES)
    values[missing] = ""
    with pytest.raises(ValueError, match=missing):
        await validate_odoo_login(values)
