"""Rendering an approval for Telegram and reading a button press back.

Pure functions only — no network, for most of this file. What is worth
testing there is what Telegram does silently: a callback payload over 64
bytes produces a button that never fires, and a redacted notice that still
carries option buttons defeats the point of redacting it.

The end-to-end class at the bottom is the exception -- it goes through the
real HTTP setup endpoint against the real, on-disk telegram_approvals
plugin, covering the Task 13 migration of its one setup field
(`bot_token`) from kind="password" to kind="credential".
"""

from __future__ import annotations

import base64
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.capas.discovery import find_plugin
from oc8.capas.lifecycle import enable_plugin
from oc8.capas.service import install_plugin
from oc8.channels.notice import ApprovalNotice, NoticeOption
from oc8.credentials.service import create_credential
from oc8.main import create_app
from oc8.secrets.service import resolve_secret
from tests.conftest import AppSessionFactory

#: telegram_approvals's channel package is named `channel` after the
#: restructure -- a name shared with whatsapp_approvals and every other
#: retrofitted plugin -- so sys.modules (keyed by NAME, not path) can hand
#: this import a sibling's cached copy. Both this file and test_whatsapp.py
#: import at COLLECTION time (module top, not inside a test function), so a
#: function-scoped autouse fixture cannot help; the eviction has to run in
#: the module body itself, unconditionally, before its own import, since
#: pytest's collection order between the two files is not guaranteed.
PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "capas" / "telegram_approvals"


def _evict_generic_plugin_packages() -> None:
    for _stale in [n for n in sys.modules if n == "channel" or n.startswith("channel.")]:
        del sys.modules[_stale]


_evict_generic_plugin_packages()
sys.path.insert(0, str(PLUGIN_ROOT))

from channel.channel import (  # noqa: E402
    build,
    decision_from_update,
    keyboard,
    link_code_from_update,
    render,
)

# Symmetric with the insert above: the names this import needed are already
# bound into this module's own namespace, so nothing later re-resolves
# `channel` for this file -- but leaving it cached would let whichever of
# this file / test_whatsapp.py collects SECOND silently receive THIS
# plugin's module for its own `channel.channel` import.
_evict_generic_plugin_packages()
sys.path.remove(str(PLUGIN_ROOT))


def _notice(**kw: object) -> ApprovalNotice:
    base: dict[str, object] = {
        "approval_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "title": "Erstattung freigeben?",
        "detail": "Ticket #42, 249 EUR doppelt abgebucht.",
        "amount_text": "249,00 EUR",
    }
    base.update(kw)
    return ApprovalNotice(**base)  # type: ignore[arg-type]


def test_a_withheld_notice_carries_no_buttons() -> None:
    """A button labelled "Voll erstatten" tells you what the request is about
    even with the text removed — which is exactly what withholding it was for."""
    notice = _notice(options=(NoticeOption("full", "Voll erstatten"),)).redacted()
    assert keyboard(notice) is None
    body = render(notice)
    assert "249" not in body
    assert "oc8" in body, "it must say where the decision can be taken"


def test_the_options_become_buttons_and_a_refusal_is_always_offered() -> None:
    notice = _notice(
        options=(NoticeOption("full", "Voll erstatten"), NoticeOption("half", "Teilweise"))
    )
    rows = keyboard(notice)
    assert rows is not None
    labels = [b["text"] for row in rows["inline_keyboard"] for b in row]
    assert labels[:2] == ["Voll erstatten", "Teilweise"]
    assert "✖️ Ablehnen" in labels, "saying no must never require leaving the chat"


def test_an_option_too_long_for_telegram_is_dropped_not_shipped_broken() -> None:
    """Telegram rejects callback_data over 64 bytes silently: the button simply
    never fires, and the approver taps a dead control. Dropping it is honest —
    the option is still there in oc8."""
    notice = _notice(options=(NoticeOption("x" * 60, "Sehr lange Option"),))
    rows = keyboard(notice)
    assert rows is not None
    labels = [b["text"] for row in rows["inline_keyboard"] for b in row]
    assert "Sehr lange Option" not in labels
    assert "✅ Freigeben" in labels, "a plain yes/no remains"


def test_html_in_an_approval_cannot_break_out_of_the_message() -> None:
    notice = _notice(detail="<b>nicht fett</b> & <script>")
    body = render(notice)
    assert "&lt;b&gt;" in body and "&amp;" in body
    assert "<script>" not in body


def test_a_button_press_becomes_a_decision_attributed_to_its_sender() -> None:
    approval = uuid.uuid4()
    parsed = decision_from_update(
        {
            "callback_query": {
                "id": "cb-1",
                "from": {"id": 4242},
                "data": f"{approval}:approve:full",
            }
        }
    )
    assert parsed is not None
    decision, callback_id = parsed
    assert decision.approval_id == approval
    assert decision.verdict == "approve"
    assert decision.option_key == "full"
    assert decision.external_id == "4242", "core resolves this to a binding, not to a claim"
    assert callback_id == "cb-1"


@pytest.mark.parametrize(
    "update",
    [
        {},
        {"callback_query": {"id": "1", "from": {"id": 1}, "data": "not-a-uuid:approve"}},
        {"callback_query": {"id": "1", "from": {"id": 1}, "data": f"{uuid.uuid4()}:delete"}},
        {"callback_query": {"id": "1", "data": f"{uuid.uuid4()}:approve"}},
    ],
)
def test_anything_that_is_not_one_of_our_buttons_is_ignored(update: dict[str, object]) -> None:
    """Including a press with no sender: a message that cannot be attributed to
    an account cannot be attributed to a person."""
    assert decision_from_update(update) is None


def test_a_start_deeplink_and_a_pasted_code_both_bind() -> None:
    """`/start <payload>` is how Telegram's own deep links work, so an operator
    can hand out a link instead of asking somebody to retype a code."""
    assert link_code_from_update(
        {"message": {"from": {"id": 7}, "text": "/start abc123"}}
    ) == ("abc123", "7")
    assert link_code_from_update({"message": {"from": {"id": 7}, "text": "abc123"}}) == (
        "abc123",
        "7",
    )
    chatter = {"message": {"from": {"id": 7}, "text": "hallo, wie geht"}}
    assert link_code_from_update(chatter) is None


def test_a_channel_without_a_token_refuses_to_exist() -> None:
    """Raised at build time, not at send time: an approval quietly not being
    delivered is the failure this whole feature exists to prevent."""
    with pytest.raises(ValueError, match="bot token"):
        build({})
    assert build({"bot_token": "t"}).capabilities().max_classification == "public"
    assert (
        build({"bot_token": "t", "max_classification": "internal"})
        .capabilities()
        .max_classification
        == "internal"
    )


# --- Task 13: the real telegram_approvals setup form, end to end -------
#
# Before this migration, `setup/fields.toml`'s `bot_token` field was
# kind="password": the browser posted the raw token straight to
# /capas/{id}/setup. After it, the field is kind="credential" naming the
# new `credential_types/telegram_bot.toml` -- the browser posts a
# credential id, and configure_plugin (Task 7) resolves it server-side
# before storing the plaintext under the plugin's own fixed
# `telegram/bot_token` secret_ref, same as before. `channel/channel.py`'s
# own runtime code is completely unaware of the difference; it still reads
# one fixed ref.
_REPO_PLUGINS_DIR = Path(__file__).resolve().parents[3] / "capas"


@pytest.fixture(autouse=True)
def _real_plugins_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from oc8.config import get_settings

    monkeypatch.setenv("OC8_CAPAS_PATH", str(_REPO_PLUGINS_DIR))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch, _real_plugins_path: None) -> Iterator[None]:
    # Set through the environment, not onto the cached Settings instance --
    # symmetric with microsoft365/test_setup_flow.py's own fixture, for the
    # same reason: whichever of the two plugins-path/KEK fixtures ran second
    # would otherwise decide whether the app process sees a KEK at all.
    from oc8.config import get_settings

    monkeypatch.setenv("OC8_SECRET_KEK", base64.b64encode(bytes(range(32))).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _headers(tenant: uuid.UUID) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")
    return {"Authorization": f"Bearer {token}"}


async def test_submitting_a_credential_id_resolves_and_stores_the_bot_token(
    app_session: AppSessionFactory,
) -> None:
    """The credential is created (and, separately, could be tested) BEFORE
    the setup form is ever submitted -- exactly what <CredentialPicker>'s
    create flow does (Task 9: useCreateCredential, not useTestCredential).
    Submitting the form must resolve that credential's own secret field and
    land the RESOLVED plaintext under the plugin's fixed secret_ref, so
    channel/channel.py needs zero changes to keep working."""
    tenant = uuid.uuid4()
    discovered = find_plugin("telegram_approvals")
    assert discovered is not None and discovered.valid, (
        discovered.error if discovered else "not found"
    )
    assert discovered.manifest is not None

    async with app_session(tenant) as db:
        # A credential_type is only reachable via get_credential_type once
        # its owning plugin is ENABLED for this tenant (registry.py's
        # _capa_credential_types reads enabled_plugin_names) -- so, same as
        # a real <CredentialPicker> would only ever be shown after Telegram
        # is already enabled, install+enable happens first here.
        version = await install_plugin(db, tenant_id=tenant, manifest_data=discovered.manifest)
        plugin_id = version.capa_id
        await enable_plugin(
            db,
            tenant_id=tenant,
            capa_id=plugin_id,
            granted_permissions=list(version.permissions),
        )

        cred = await create_credential(
            db,
            tenant_id=tenant,
            name="Approvals bot",
            credential_type="telegram_bot",
            field_values={"bot_token": "123456:ABC-DEF-real-token"},
        )
        cred_id = str(cred.id)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            configured = await client.post(
                f"/api/v1/capas/{plugin_id}/setup",
                json={"values": {"bot_token": cred_id}},
                headers=_headers(tenant),
            )
            assert configured.status_code == 200, configured.text
            # An approval_channel plugin has no `setup.mcp` block, so there is
            # never a connection to report back.
            assert configured.json()["connectionId"] is None

    async with app_session(tenant) as db:
        stored = await resolve_secret(db, tenant_id=tenant, ref="telegram/bot_token")
        assert stored == "123456:ABC-DEF-real-token"

        installation = (
            await db.execute(
                select(m.CapaInstallation).where(m.CapaInstallation.tenant_id == tenant)
            )
        ).scalar_one()
        # Task 13's `_find_references` decision, exercised for real: the
        # credential id itself is nowhere in the installation's own config.
        assert cred_id not in str(installation.config or {})


async def test_a_non_uuid_value_is_rejected_the_same_way_task_7s_own_test_covers(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    discovered = find_plugin("telegram_approvals")
    assert discovered is not None and discovered.valid and discovered.manifest is not None

    async with app_session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=discovered.manifest)
        plugin_id = version.capa_id
        await enable_plugin(
            db,
            tenant_id=tenant,
            capa_id=plugin_id,
            granted_permissions=list(version.permissions),
        )

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            configured = await client.post(
                f"/api/v1/capas/{plugin_id}/setup",
                json={"values": {"bot_token": "not-a-real-token-nor-a-uuid"}},
                headers=_headers(tenant),
            )
            assert configured.status_code == 422, configured.text
