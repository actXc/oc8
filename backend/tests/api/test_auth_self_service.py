"""The three `/auth/me/...` routes a person may run on THEMSELVES.

What every test here is really guarding is the absence of a `{member_id}`
path parameter: `api/v1/members.py` already has admin-gated routes that set
anybody's password and rename anybody's sign-in identity, and the only thing
separating those from these is that these resolve the row from the caller's
own token. So each test signs in as one person and checks the row that moved
was that person's -- and, for the two identity-moving routes, that it did not
move at all without the current password.

The email route's two branches are the interesting part. Whether a change is
applied at once or only after a mailed confirmation link is a fact about the
DEPLOYMENT (is an SMTP credential configured?), not a preference, so both
branches are exercised against the same request body.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.auth.password import hash_password, verify_password
from oc8.authz.permissions import MEMBER_ROLE
from oc8.authz.scope import subject_uuid_for
from oc8.credentials.service import create_credential
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

OLD_PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SMTP-configured branch resolves a secret-kind `password` field
    through the real vault, which needs a KEK (mirrors tests/mail/test_send.py)."""
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


def _headers(tenant: uuid.UUID, subject: str) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=MEMBER_ROLE)
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


def _member(tenant: uuid.UUID, email: str, *, password: str | None = OLD_PASSWORD) -> m.OrgMember:
    return m.OrgMember(
        tenant_id=tenant,
        subject=email,
        subject_uuid=subject_uuid_for(email),
        display_name=email,
        password_hash=None if password is None else hash_password(password),
    )


async def _row(db: AsyncSession, tenant: uuid.UUID, email: str) -> m.OrgMember:
    row = (
        await db.execute(
            select(m.OrgMember).where(m.OrgMember.tenant_id == tenant, m.OrgMember.subject == email)
        )
    ).scalar_one()
    return row


async def _actions(db: AsyncSession, tenant: uuid.UUID) -> list[str]:
    return list(
        (
            await db.execute(select(m.AuditEvent.action).where(m.AuditEvent.tenant_id == tenant))
        ).scalars()
    )


async def _configure_smtp(db: AsyncSession, tenant: uuid.UUID) -> None:
    """Point the tenant's `active_smtp_credential_id` at a real credential."""
    credential = await create_credential(
        db,
        tenant_id=tenant,
        name="smtp",
        credential_type="smtp_server",
        field_values={"host": "smtp.example.com", "port": "587", "from_address": "a@b.com"},
    )
    org = await db.get(m.Organization, tenant)
    assert org is not None
    org.settings = {**org.settings, "active_smtp_credential_id": str(credential.id)}
    await db.flush()


# --- display name --------------------------------------------------------


async def test_display_name_changes_the_callers_own_row(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    email = "ada@example.com"
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))

    async with _http() as http:
        r = await http.put(
            "/api/v1/auth/me/display-name",
            json={"displayName": "Ada Lovelace"},
            headers=_headers(tenant, email),
        )
    assert r.status_code == 200, r.text
    assert r.json()["displayName"] == "Ada Lovelace"

    async with app_session(tenant) as db:
        assert (await _row(db, tenant, email)).display_name == "Ada Lovelace"
        assert "member.display_name_changed" in await _actions(db, tenant)


async def test_display_name_does_not_touch_anybody_else(app_session: AppSessionFactory) -> None:
    """There is no `{member_id}` here to point at somebody else -- this is the
    test that fails if one is ever added."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(_member(tenant, "ada@example.com"))
        db.add(_member(tenant, "grace@example.com"))

    async with _http() as http:
        r = await http.put(
            "/api/v1/auth/me/display-name",
            json={"displayName": "Renamed"},
            headers=_headers(tenant, "ada@example.com"),
        )
    assert r.status_code == 200, r.text

    async with app_session(tenant) as db:
        assert (await _row(db, tenant, "grace@example.com")).display_name == "grace@example.com"


# --- password ------------------------------------------------------------


async def test_password_change_needs_the_current_password(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    email = "ada@example.com"
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))

    async with _http() as http:
        r = await http.put(
            "/api/v1/auth/me/password",
            json={"currentPassword": "not-it", "newPassword": "brand-new-password"},
            headers=_headers(tenant, email),
        )
    assert r.status_code == 401, r.text

    async with app_session(tenant) as db:
        stored = (await _row(db, tenant, email)).password_hash
    assert stored is not None
    assert verify_password(OLD_PASSWORD, stored), (
        "a wrong current password answered 401 but still replaced the hash -- "
        "that is a locked-out account"
    )


async def test_password_change_replaces_the_hash(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    email = "ada@example.com"
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))

    async with _http() as http:
        r = await http.put(
            "/api/v1/auth/me/password",
            json={"currentPassword": OLD_PASSWORD, "newPassword": "brand-new-password"},
            headers=_headers(tenant, email),
        )
    assert r.status_code == 204, r.text
    assert r.text == "", "a password route must echo nothing at all"

    async with app_session(tenant) as db:
        stored = (await _row(db, tenant, email)).password_hash
        assert "member.password_changed" in await _actions(db, tenant)
    assert stored is not None
    assert verify_password("brand-new-password", stored)
    assert not verify_password(OLD_PASSWORD, stored)


# --- email: the no-mail-server branch ------------------------------------


async def test_email_change_applies_immediately_without_a_mail_server(
    app_session: AppSessionFactory,
) -> None:
    """No mail server means no way to confirm and no way to warn, so refusing
    would leave a self-hosted instance unable to fix a typo'd login at all."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))

    async with _http() as http:
        r = await http.put(
            "/api/v1/auth/me/email",
            json={"currentPassword": OLD_PASSWORD, "newEmail": "ada@newmail.com"},
            headers=_headers(tenant, email),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verificationRequired"] is False
    assert body["member"]["subject"] == "ada@newmail.com"

    async with app_session(tenant) as db:
        row = await _row(db, tenant, "ada@newmail.com")
        # Both columns move together: `subject_uuid` is a pure function of
        # `subject`, and the messenger door resolves a person by it alone.
        assert row.subject_uuid == subject_uuid_for("ada@newmail.com")
        assert "member.subject_renamed" in await _actions(db, tenant)
        tokens = (
            (
                await db.execute(
                    select(m.AccountVerificationToken).where(
                        m.AccountVerificationToken.tenant_id == tenant
                    )
                )
            )
            .scalars()
            .all()
        )
    assert tokens == [], "nothing to confirm, so nothing should have been stored"


async def test_email_change_needs_the_current_password(app_session: AppSessionFactory) -> None:
    """The email IS the login: without this gate, a borrowed session moves
    somebody's account to an address the borrower controls."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))

    async with _http() as http:
        r = await http.put(
            "/api/v1/auth/me/email",
            json={"currentPassword": "not-it", "newEmail": "attacker@evil.example"},
            headers=_headers(tenant, email),
        )
    assert r.status_code == 401, r.text

    async with app_session(tenant) as db:
        assert (await _row(db, tenant, email)).subject == email


async def test_email_change_refuses_an_address_somebody_else_signs_in_with(
    app_session: AppSessionFactory,
) -> None:
    """`(tenant_id, subject)` is unique -- the row that lost this race would
    otherwise look like it had simply vanished."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(_member(tenant, "ada@example.com"))
        db.add(_member(tenant, "grace@example.com"))

    async with _http() as http:
        r = await http.put(
            "/api/v1/auth/me/email",
            json={"currentPassword": OLD_PASSWORD, "newEmail": "grace@example.com"},
            headers=_headers(tenant, "ada@example.com"),
        )
    assert r.status_code == 409, r.text

    async with app_session(tenant) as db:
        assert (await _row(db, tenant, "ada@example.com")).subject == "ada@example.com"


# --- email: the mail-server branch ---------------------------------------


async def test_email_change_with_a_mail_server_waits_for_a_confirmation(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    email = "ada@example.com"
    async with app_session(tenant) as db:
        db.add(m.Organization(id=tenant, slug=str(tenant), name="t", settings={}))
        await db.flush()
        db.add(_member(tenant, email))
        await _configure_smtp(db, tenant)

    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = MagicMock()
        smtp_cls.return_value.__enter__.return_value = client
        async with _http() as http:
            r = await http.put(
                "/api/v1/auth/me/email",
                json={"currentPassword": OLD_PASSWORD, "newEmail": "ada@newmail.com"},
                headers=_headers(tenant, email),
            )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verificationRequired"] is True
    assert body["sentTo"] == "ada@newmail.com"
    assert body["member"] is None

    async with app_session(tenant) as db:
        # The sign-in identity has NOT moved yet -- that is the whole point.
        assert (await _row(db, tenant, email)).subject == email
        token_row = (
            await db.execute(
                select(m.AccountVerificationToken).where(
                    m.AccountVerificationToken.tenant_id == tenant
                )
            )
        ).scalar_one()
        assert token_row.purpose == "email_change"
        assert token_row.new_email == "ada@newmail.com"
        assert token_row.used_at is None
        assert "member.email_change_requested" in await _actions(db, tenant)

    # The link was mailed to the NEW address, and the plaintext token in it is
    # the sha256 preimage of the stored hash -- i.e. the row is confirmable.
    client.send_message.assert_called_once()
    message = client.send_message.call_args.args[0]
    assert message["To"] == "ada@newmail.com"
    link_token = message.get_content().split("token=")[1].split()[0]
    assert hashlib.sha256(link_token.encode()).hexdigest() == token_row.token_hash


async def test_the_confirmation_link_points_at_the_configured_frontend(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`frontend_base_url` is the one setting that says where the browser
    lives (`oauth/providers.py` already builds its callback return off it).
    A link built off anything else lands on a host the person cannot reach."""
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(), "frontend_base_url", "https://oc8.example.test/", raising=False
    )
    tenant = uuid.uuid4()
    email = "ada@example.com"
    async with app_session(tenant) as db:
        db.add(m.Organization(id=tenant, slug=str(tenant), name="t", settings={}))
        await db.flush()
        db.add(_member(tenant, email))
        await _configure_smtp(db, tenant)

    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = MagicMock()
        smtp_cls.return_value.__enter__.return_value = client
        async with _http() as http:
            r = await http.put(
                "/api/v1/auth/me/email",
                json={"currentPassword": OLD_PASSWORD, "newEmail": "ada@newmail.com"},
                headers=_headers(tenant, email),
            )
    assert r.status_code == 200, r.text
    body_text = client.send_message.call_args.args[0].get_content()
    assert "https://oc8.example.test/confirm-email?token=" in body_text


async def test_a_mail_server_that_cannot_deliver_is_not_reported_as_sent(
    app_session: AppSessionFactory,
) -> None:
    """ "Check your inbox" for mail that never left is a dead end the person
    cannot act on, and it would leave an unusable token behind."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    async with app_session(tenant) as db:
        db.add(m.Organization(id=tenant, slug=str(tenant), name="t", settings={}))
        await db.flush()
        db.add(_member(tenant, email))
        await _configure_smtp(db, tenant)

    with patch("oc8.credentials.smtp.smtplib.SMTP", side_effect=OSError("connection refused")):
        async with _http() as http:
            r = await http.put(
                "/api/v1/auth/me/email",
                json={"currentPassword": OLD_PASSWORD, "newEmail": "ada@newmail.com"},
                headers=_headers(tenant, email),
            )
    assert r.status_code == 502, r.text

    async with app_session(tenant) as db:
        assert (await _row(db, tenant, email)).subject == email
        tokens = (
            (
                await db.execute(
                    select(m.AccountVerificationToken).where(
                        m.AccountVerificationToken.tenant_id == tenant
                    )
                )
            )
            .scalars()
            .all()
        )
    assert tokens == [], "an unsendable confirmation must not leave a token nobody can use"
