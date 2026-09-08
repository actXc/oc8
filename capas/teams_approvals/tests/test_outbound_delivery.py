"""`TeamsChannel.deliver`/`withdraw`/`say` and its token minting/caching --
the Connector API half. All HTTP mocked via `httpx.MockTransport`, the same
pattern `capas/microsoft365/tests/test_graph_client.py` already uses; no
live Azure call, no respx (not a project dependency)."""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from oc8.channels.notice import ApprovalNotice

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _evict() -> None:
    for _stale in [n for n in sys.modules if n == "channel" or n.startswith("channel.")]:
        del sys.modules[_stale]


@pytest.fixture(autouse=True)
def _plugin_path() -> Iterator[None]:
    _evict()
    sys.path.insert(0, str(PLUGIN_ROOT))
    yield
    sys.path.remove(str(PLUGIN_ROOT))
    _evict()


_Handler = Callable[[httpx.Request], httpx.Response]


def _install(monkeypatch: pytest.MonkeyPatch, handler: _Handler) -> list[httpx.Request]:
    hops: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        hops.append(request)
        return handler(request)

    real = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(recording)
        return real(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return hops


def _token_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok-abc", "expires_in": 3600})


_APPROVAL_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _notice(**overrides: object) -> ApprovalNotice:
    defaults: dict[str, object] = {
        "approval_id": _APPROVAL_ID,
        "tenant_id": _TENANT_ID,
        "title": "Rechnung freigeben?",
        "detail": "Rechnung #123",
    }
    defaults.update(overrides)
    return ApprovalNotice(**defaults)  # type: ignore[arg-type]


#: Exactly what `_conversation_reference` now produces: IDs only, no display
#: names -- those were dropped because this dict is also the binding key, and
#: a renamed user would otherwise orphan their own binding.
_CONV_REF = json.dumps(
    {
        "serviceUrl": "https://smba.trafficmanager.net/teams/",
        "channelId": "msteams",
        "conversationId": "conv-1",
        "botId": "bot-1",
        "userId": "user-1",
    },
    sort_keys=True,
)

#: A reference persisted BEFORE the names were dropped. Bindings created by
#: the older code are still in the database, so `_activity_base` has to keep
#: reading these back rather than tripping over the extra keys.
_LEGACY_CONV_REF = json.dumps(
    {**json.loads(_CONV_REF), "botName": "oc8 bot", "userName": "Rico"}, sort_keys=True
)


@pytest.mark.asyncio
async def test_mint_access_token_posts_client_credentials_to_the_default_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel.channel import mint_access_token

    hops = _install(monkeypatch, _token_response)
    body = await mint_access_token(app_id="app-1", app_password="pw", tenant="")

    assert body["access_token"] == "tok-abc"
    assert len(hops) == 1
    assert "botframework.com/oauth2/v2.0/token" in str(hops[0].url)
    form = dict(x.split("=") for x in hops[0].content.decode().split("&"))
    assert form["grant_type"] == "client_credentials"
    assert form["client_id"] == "app-1"


@pytest.mark.asyncio
async def test_mint_access_token_uses_a_configured_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.channel import mint_access_token

    hops = _install(monkeypatch, _token_response)
    await mint_access_token(app_id="app-1", app_password="pw", tenant="contoso.onmicrosoft.com")

    assert "contoso.onmicrosoft.com/oauth2/v2.0/token" in str(hops[0].url)


def _deliver_handler(captured: list[httpx.Request]) -> _Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if "login.microsoftonline.com" in str(request.url):
            return _token_response(request)
        return httpx.Response(200, json={"id": "activity-1"})

    return handler


@pytest.mark.asyncio
async def test_deliver_posts_an_adaptive_card_to_the_conversation_from_its_external_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel.channel import TeamsChannel

    captured: list[httpx.Request] = []
    _install(monkeypatch, _deliver_handler(captured))
    channel = TeamsChannel(app_id="app-1", app_password="pw")

    handle = await channel.deliver(_notice(), external_id=_CONV_REF)

    assert handle == "activity-1"
    send_calls = [r for r in captured if "smba.trafficmanager.net" in str(r.url)]
    assert len(send_calls) == 1
    assert str(send_calls[0].url) == (
        "https://smba.trafficmanager.net/teams/v3/conversations/conv-1/activities"
    )
    assert send_calls[0].headers["authorization"] == "Bearer tok-abc"
    payload = json.loads(send_calls[0].content)
    assert payload["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert payload["from"] == {"id": "bot-1", "name": ""}
    assert payload["recipient"] == {"id": "user-1", "name": ""}


@pytest.mark.asyncio
async def test_deliver_still_reads_a_conversation_reference_stored_with_display_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bindings written before the display names were dropped from the
    reference are still in the database, and must keep delivering."""
    from channel.channel import TeamsChannel

    captured: list[httpx.Request] = []
    _install(monkeypatch, _deliver_handler(captured))
    channel = TeamsChannel(app_id="app-1", app_password="pw")

    assert await channel.deliver(_notice(), external_id=_LEGACY_CONV_REF) == "activity-1"

    send_calls = [r for r in captured if "smba.trafficmanager.net" in str(r.url)]
    payload = json.loads(send_calls[0].content)
    assert payload["from"] == {"id": "bot-1", "name": "oc8 bot"}
    assert payload["recipient"] == {"id": "user-1", "name": "Rico"}


@pytest.mark.asyncio
async def test_a_second_deliver_within_the_token_ttl_does_not_mint_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel.channel import TeamsChannel

    captured: list[httpx.Request] = []
    _install(monkeypatch, _deliver_handler(captured))
    channel = TeamsChannel(app_id="app-1", app_password="pw")

    await channel.deliver(_notice(), external_id=_CONV_REF)
    await channel.deliver(_notice(), external_id=_CONV_REF)

    mint_calls = [r for r in captured if "login.microsoftonline.com" in str(r.url)]
    assert len(mint_calls) == 1


@pytest.mark.asyncio
async def test_withdraw_edits_the_original_card_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.channel import TeamsChannel

    def handler(request: httpx.Request) -> httpx.Response:
        if "login.microsoftonline.com" in str(request.url):
            return _token_response(request)
        assert request.method == "PUT"
        assert str(request.url).endswith("/activities/activity-1")
        return httpx.Response(200, json={"id": "activity-1"})

    captured: list[httpx.Request] = []
    _install(monkeypatch, lambda r: (captured.append(r), handler(r))[1])
    channel = TeamsChannel(app_id="app-1", app_password="pw")

    await channel.withdraw(
        _notice(), external_id=_CONV_REF, handle="activity-1", outcome="approved"
    )

    put_calls = [r for r in captured if r.method == "PUT"]
    assert len(put_calls) == 1
    payload = json.loads(put_calls[0].content)
    assert payload["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"


@pytest.mark.asyncio
async def test_withdraw_falls_back_to_plain_text_when_the_edit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel.channel import TeamsChannel

    def handler(request: httpx.Request) -> httpx.Response:
        if "login.microsoftonline.com" in str(request.url):
            return _token_response(request)
        if request.method == "PUT":
            return httpx.Response(404, json={"error": "gone"})
        return httpx.Response(200, json={"id": "activity-2"})

    captured: list[httpx.Request] = []
    _install(monkeypatch, lambda r: (captured.append(r), handler(r))[1])
    channel = TeamsChannel(app_id="app-1", app_password="pw")

    await channel.withdraw(
        _notice(), external_id=_CONV_REF, handle="activity-1", outcome="rejected"
    )

    post_calls = [
        r for r in captured if r.method == "POST" and "smba.trafficmanager.net" in str(r.url)
    ]
    assert len(post_calls) == 1
    payload = json.loads(post_calls[0].content)
    assert "abgelehnt" in payload["text"]


@pytest.mark.asyncio
async def test_withdraw_with_no_handle_does_nothing() -> None:
    from channel.channel import TeamsChannel

    channel = TeamsChannel(app_id="app-1", app_password="pw")
    await channel.withdraw(_notice(), external_id=_CONV_REF, handle=None, outcome="approved")


@pytest.mark.asyncio
async def test_withdraw_never_raises_when_every_send_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.channel import TeamsChannel

    def handler(request: httpx.Request) -> httpx.Response:
        if "login.microsoftonline.com" in str(request.url):
            return _token_response(request)
        return httpx.Response(500, json={"error": "down"})

    _install(monkeypatch, handler)
    channel = TeamsChannel(app_id="app-1", app_password="pw")

    await channel.withdraw(
        _notice(), external_id=_CONV_REF, handle="activity-1", outcome="approved"
    )


@pytest.mark.asyncio
async def test_say_sends_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.channel import TeamsChannel

    captured: list[httpx.Request] = []
    _install(monkeypatch, _deliver_handler(captured))
    channel = TeamsChannel(app_id="app-1", app_password="pw")

    await channel.say(_CONV_REF, "Bin dran, melde mich gleich.")

    send_calls = [r for r in captured if "smba.trafficmanager.net" in str(r.url)]
    assert len(send_calls) == 1
    payload = json.loads(send_calls[0].content)
    assert payload["text"] == "Bin dran, melde mich gleich."
    assert "attachments" not in payload


@pytest.mark.asyncio
async def test_say_never_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.channel import TeamsChannel

    def handler(request: httpx.Request) -> httpx.Response:
        if "login.microsoftonline.com" in str(request.url):
            return _token_response(request)
        return httpx.Response(500, json={"error": "down"})

    _install(monkeypatch, handler)
    channel = TeamsChannel(app_id="app-1", app_password="pw")

    await channel.say(_CONV_REF, "hello")


def test_build_reads_bot_token_app_id_and_tenant_id_from_the_resolved_config() -> None:
    from channel.channel import TeamsChannel, build

    channel = build(
        {
            "bot_token": "pw",
            "app_id": "app-1",
            "tenant_id": "contoso",
            "max_classification": "internal",
        }
    )
    assert isinstance(channel, TeamsChannel)
    assert channel.capabilities().max_classification == "internal"


def test_build_without_a_bot_token_raises() -> None:
    from channel.channel import build

    with pytest.raises(ValueError, match="bot token"):
        build({"app_id": "app-1"})


def test_build_without_an_app_id_raises() -> None:
    from channel.channel import build

    with pytest.raises(ValueError, match="app_id"):
        build({"bot_token": "pw"})


def test_register_adds_the_teams_channel() -> None:
    from channel.channel import CHANNEL_ID, build, register

    added: dict[str, Any] = {}

    class _Contrib:
        def add_channel(self, channel_id: str, factory: Any) -> None:
            added[channel_id] = factory

    register(_Contrib())
    assert added[CHANNEL_ID] is build
