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

The second half of this file covers the three PUBLIC routes that carry no
bearer token at all -- `POST /auth/email/confirm`, `POST
/auth/password/forgot`, `POST /auth/password/reset`. What those tests guard is
narrower and sharper than "does it work": a mailed link is a bearer credential
for somebody's account, so each one is spendable exactly once, every way of
being broken answers with the same sentence, and forgot-password answers a
real address and a fake one identically.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
from asgi_lifespan import LifespanManager
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from oc8 import models as m
from oc8.api.v1.auth import FORGOT_PASSWORD_MESSAGE, INVALID_LINK_MESSAGE, _redeem_token
from oc8.auth import get_identity_provider
from oc8.auth.password import hash_password, verify_password
from oc8.authz.permissions import MEMBER_ROLE
from oc8.authz.scope import subject_uuid_for
from oc8.config import get_settings
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


async def test_a_blank_display_name_is_refused(app_session: AppSessionFactory) -> None:
    """`min_length=1` lets a string of spaces through, and a blank display
    name is what the nav bar and every approval row name this person by."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))

    async with _http() as http:
        r = await http.put(
            "/api/v1/auth/me/display-name",
            json={"displayName": "   "},
            headers=_headers(tenant, email),
        )
    assert r.status_code == 422, r.text

    async with app_session(tenant) as db:
        assert (await _row(db, tenant, email)).display_name == email


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
    assert body["reauthRequired"] is True, (
        "the caller's stateless token still carries the OLD subject and nothing "
        "here can revoke it -- without this flag the frontend leaves them signed "
        "in, and their next request mints a ghost member row under the old subject"
    )

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


async def test_a_blank_email_is_refused(app_session: AppSessionFactory) -> None:
    """Whitespace-only, specifically: `Field(min_length=1)` already blocks ""
    but passes " ", which strips to "" here.

    `org_member.subject` has no CHECK constraint and `POST /auth/login`
    matches it exactly against a non-empty `email` field, so committing a
    blank subject locks the account out for good -- no login, and no
    self-service route left to reach, since every one of them resolves the
    caller through the very subject that is now gone.
    """
    tenant = uuid.uuid4()
    email = "ada@example.com"
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))

    async with _http() as http:
        r = await http.put(
            "/api/v1/auth/me/email",
            json={"currentPassword": OLD_PASSWORD, "newEmail": "   "},
            headers=_headers(tenant, email),
        )
    assert r.status_code == 422, r.text

    async with app_session(tenant) as db:
        row = await _row(db, tenant, email)
    assert row.subject == email
    assert row.subject_uuid == subject_uuid_for(email)


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


async def test_submitting_the_address_you_already_use_changes_nothing(
    app_session: AppSessionFactory,
) -> None:
    """A no-op must not write an audit row claiming a rename -- and on the
    mail-server branch it must not mail a link for the current address."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    async with app_session(tenant) as db:
        db.add(m.Organization(id=tenant, slug=str(tenant), name="t", settings={}))
        await db.flush()
        db.add(_member(tenant, email))
        await _configure_smtp(db, tenant)

    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        async with _http() as http:
            r = await http.put(
                "/api/v1/auth/me/email",
                json={"currentPassword": OLD_PASSWORD, "newEmail": email},
                headers=_headers(tenant, email),
            )
    assert r.status_code == 200, r.text
    assert r.json()["verificationRequired"] is False
    # Nothing moved, so the session is untouched -- a no-op must not sign
    # somebody out.
    assert r.json()["reauthRequired"] is False
    smtp_cls.assert_not_called()

    async with app_session(tenant) as db:
        actions = await _actions(db, tenant)
    assert "member.subject_renamed" not in actions
    assert "member.email_change_requested" not in actions


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
    # Nothing has moved yet, so the session is still perfectly valid --
    # signing the caller out here would be a pointless interruption.
    assert body["reauthRequired"] is False

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
    # The row is written and committed BEFORE the socket is opened -- that is
    # what returns the DB connection to the pool for the length of the send --
    # so it cannot be rolled back by a failure that happens afterwards. It is
    # spent instead, which leaves exactly the same thing behind that a rollback
    # did: nothing anybody can use. Spending it (rather than leaving it live)
    # is also what lets the person retry AT ONCE -- an unused row inside the
    # cooldown window would otherwise answer their retry with "check your
    # inbox" for mail that never left.
    assert [t.used_at is not None for t in tokens] == [True], (
        "an unsendable confirmation must not leave a token anybody can use"
    )


async def test_the_confirmation_socket_is_opened_with_no_db_connection_held(
    app_session: AppSessionFactory,
) -> None:
    """See `_watch_the_pool`: a slow relay must not be able to hold a pooled DB
    connection for the ~40 seconds `smtp_connection`'s per-operation timeouts
    allow it."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    async with app_session(tenant) as db:
        db.add(m.Organization(id=tenant, slug=str(tenant), name="t", settings={}))
        await db.flush()
        db.add(_member(tenant, email))
        await _configure_smtp(db, tenant)

    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        checkouts = _watch_the_pool(smtp_cls)
        async with _http() as http:
            r = await http.put(
                "/api/v1/auth/me/email",
                json={"currentPassword": OLD_PASSWORD, "newEmail": "ada@newmail.com"},
                headers=_headers(tenant, email),
            )
    assert r.status_code == 200, r.text
    assert checkouts == [0], (
        f"the SMTP socket was opened with {checkouts} DB connection(s) checked out"
    )


async def test_a_second_email_change_inside_the_cooldown_does_not_mail_again(
    app_session: AppSessionFactory,
) -> None:
    """Same bombing gap as forgot-password, on the other minting route: without
    a cooldown, a held session mails the same inbox once per click."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    async with app_session(tenant) as db:
        db.add(m.Organization(id=tenant, slug=str(tenant), name="t", settings={}))
        await db.flush()
        db.add(_member(tenant, email))
        await _configure_smtp(db, tenant)

    body = {"currentPassword": OLD_PASSWORD, "newEmail": "ada@newmail.com"}
    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = MagicMock()
        smtp_cls.return_value.__enter__.return_value = client
        async with _http() as http:
            first = await http.put(
                "/api/v1/auth/me/email", json=body, headers=_headers(tenant, email)
            )
            second = await http.put(
                "/api/v1/auth/me/email", json=body, headers=_headers(tenant, email)
            )

    # The caller is told the same true thing both times: there is a live link
    # in that inbox. Nothing new was minted or mailed for the second one.
    assert first.status_code == second.status_code == 200, second.text
    assert first.json() == second.json()
    assert second.json()["verificationRequired"] is True
    assert len(_mailed_links(client)) == 1
    async with app_session(tenant) as db:
        assert len(await _tokens(db, tenant)) == 1


async def test_a_different_address_is_not_held_back_by_the_cooldown(
    app_session: AppSessionFactory,
) -> None:
    """The cooldown is per target address, so correcting a typo'd new address
    is not answered with "check your inbox" pointing at the typo."""
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
            await http.put(
                "/api/v1/auth/me/email",
                json={"currentPassword": OLD_PASSWORD, "newEmail": "ada@nemail.com"},
                headers=_headers(tenant, email),
            )
            corrected = await http.put(
                "/api/v1/auth/me/email",
                json={"currentPassword": OLD_PASSWORD, "newEmail": "ada@newmail.com"},
                headers=_headers(tenant, email),
            )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["sentTo"] == "ada@newmail.com"
    assert len(_mailed_links(client)) == 2


async def test_an_email_that_is_not_an_address_is_refused(
    app_session: AppSessionFactory,
) -> None:
    """`org_member.subject` IS the sign-in identity, and on the no-mail-server
    branch it is written straight through. A length-checked `str` let somebody
    set their own login to "nonsense" and commit it."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))

    async with _http() as http:
        r = await http.put(
            "/api/v1/auth/me/email",
            json={"currentPassword": OLD_PASSWORD, "newEmail": "not-an-address"},
            headers=_headers(tenant, email),
        )
    assert r.status_code == 422, r.text

    async with app_session(tenant) as db:
        assert (await _row(db, tenant, email)).subject == email


async def test_a_mixed_case_address_is_stored_lowercased(
    app_session: AppSessionFactory,
) -> None:
    """Casing is significant in every comparison downstream (the no-op check,
    the uniqueness check, `subject_uuid_for`, the login lookup), so
    `Ada@X.com` and `ada@x.com` would otherwise be two different logins for
    one person -- and only one of them can hold the unique row."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))

    async with _http() as http:
        r = await http.put(
            "/api/v1/auth/me/email",
            json={"currentPassword": OLD_PASSWORD, "newEmail": "Foo@Bar.com"},
            headers=_headers(tenant, email),
        )
        assert r.status_code == 200, r.text
        assert r.json()["member"]["subject"] == "foo@bar.com"

        # ...and the person can sign in with it, typed either way.
        for typed in ("foo@bar.com", "Foo@Bar.com"):
            login = await http.post(
                "/api/v1/auth/login", json={"email": typed, "password": OLD_PASSWORD}
            )
            assert login.status_code == 200, f"{typed}: {login.text}"
            # The token must name the STORED subject -- a token carrying
            # `Foo@Bar.com` would mint a ghost member row under that spelling
            # on the caller's very next request.
            assert login.json()["principal"]["subject"] == "foo@bar.com"

    async with app_session(tenant) as db:
        row = await _row(db, tenant, "foo@bar.com")
        assert row.subject_uuid == subject_uuid_for("foo@bar.com")


# =========================================================================
# The public routes: POST /auth/email/confirm, /auth/password/forgot,
# /auth/password/reset
# =========================================================================
# None of these carries a bearer token. All three resolve WHICH organization
# through `_get_singleton_organization` -- the same fail-closed singleton
# lookup `POST /auth/login` already uses -- so every test below first makes
# its own tenant the one Organization on the instance.


async def _sole_organization(app_session: AppSessionFactory, tenant: uuid.UUID) -> None:
    """Leave `tenant` as the instance's ONLY Organization.

    `_get_singleton_organization` answers 409 on a second row, and every test
    above (plus most of the suite) leaves its own random-tenant Organization
    behind. The wipe runs on an owner-role, RLS-exempt connection because an
    app-role session bound to one tenant cannot see -- let alone delete --
    another tenant's row, so an app-role DELETE would silently no-op on
    exactly the rows that break the lookup. Same pattern and same reason as
    `tests/auth/test_password_auth.py::org_in_db`.
    """
    owner_engine = create_async_engine(get_settings().migration_async_url, poolclass=NullPool)
    try:
        async with AsyncSession(owner_engine, expire_on_commit=False) as owner:
            await owner.execute(text("DELETE FROM organization"))
            await owner.commit()
    finally:
        await owner_engine.dispose()
    async with app_session(tenant) as db:
        db.add(m.Organization(id=tenant, slug=str(tenant), name="t", settings={}))


def _mint_token(
    db: AsyncSession,
    tenant: uuid.UUID,
    member_id: uuid.UUID,
    *,
    purpose: str,
    plaintext: str,
    new_email: str | None = None,
    expires_in: dt.timedelta = dt.timedelta(hours=1),
    used: bool = False,
) -> None:
    """Store a verification token the way the mailing routes do: only the
    sha256 of the plaintext, never the plaintext itself."""
    now = dt.datetime.now(tz=dt.UTC)
    db.add(
        m.AccountVerificationToken(
            tenant_id=tenant,
            member_id=member_id,
            purpose=purpose,
            token_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
            new_email=new_email,
            expires_at=now + expires_in,
            used_at=now if used else None,
        )
    )


def _mailed_links(client: MagicMock) -> list[str]:
    """The `?token=` value out of every message handed to the SMTP client."""
    return [
        call.args[0].get_content().split("token=")[1].split()[0]
        for call in client.send_message.call_args_list
    ]


def _watch_the_pool(smtp_cls: MagicMock) -> list[int]:
    """Record how many DB connections the app's pool has checked out at the
    moment the SMTP socket is opened.

    Zero is the property under test, and it is not a style point.
    `smtp_connection` uses a 10-second timeout PER SOCKET OPERATION (connect,
    STARTTLS, EHLO, auth, send), so a blackholed relay holds whatever it is
    called with for up to ~40 seconds. Held INSIDE the transaction, that is a
    pooled DB connection: the engine's default `pool_size=5, max_overflow=10`
    means ~15 concurrent requests to the UNAUTHENTICATED forgot-password route
    exhaust the pool for the whole process, and every other request then blocks
    `pool_timeout=30` seconds and fails. Resolving the credential still needs
    the open transaction (RLS binds `app.tenant_id` transaction-locally), so the
    fix is to split resolve from deliver -- and this is how that split is
    proved rather than asserted.
    """
    from sqlalchemy.pool import QueuePool

    from oc8.db.engine import get_engine

    checkouts: list[int] = []

    def _record(*_args: object, **_kwargs: object) -> MagicMock:
        # AsyncAdaptedQueuePool (what an async engine builds by default) is a
        # QueuePool; only that kind counts checkouts, and only that kind is
        # exhaustible -- which is the whole hazard being measured.
        pool = get_engine().sync_engine.pool
        assert isinstance(pool, QueuePool), pool
        checkouts.append(pool.checkedout())
        return MagicMock()

    smtp_cls.side_effect = _record
    return checkouts


async def _age_the_tokens(
    app_session: AppSessionFactory, tenant: uuid.UUID, *, seconds: int
) -> None:
    """Backdate every token's `created_at`, so the next request is outside the
    cooldown window.

    The same technique the TTL cases use (`_mint_token(expires_in=...)`):
    this module has no injectable clock, and the one it would need here belongs
    to PostgreSQL -- `created_at` carries a `server_default` of `now()`.
    """
    async with app_session(tenant) as db:
        await db.execute(
            update(m.AccountVerificationToken)
            .where(m.AccountVerificationToken.tenant_id == tenant)
            .values(created_at=dt.datetime.now(tz=dt.UTC) - dt.timedelta(seconds=seconds))
        )


async def _tokens(db: AsyncSession, tenant: uuid.UUID) -> list[m.AccountVerificationToken]:
    return list(
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


# --- POST /auth/email/confirm --------------------------------------------


async def test_confirming_a_link_moves_the_sign_in_identity(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        member = _member(tenant, email)
        db.add(member)
        await db.flush()
        _mint_token(
            db,
            tenant,
            member.id,
            purpose="email_change",
            plaintext="link-abc",
            new_email="ada@newmail.com",
        )

    async with _http() as http:
        r = await http.post("/api/v1/auth/email/confirm", json={"token": "link-abc"})
    assert r.status_code == 200, r.text
    assert r.json()["subject"] == "ada@newmail.com"

    async with app_session(tenant) as db:
        row = await _row(db, tenant, "ada@newmail.com")
        # Both identity columns move together -- the messenger door resolves a
        # person by `subject_uuid` alone.
        assert row.subject_uuid == subject_uuid_for("ada@newmail.com")
        assert "member.subject_renamed" in await _actions(db, tenant)
        spent = (await _tokens(db, tenant))[0]
        assert spent.used_at is not None, "the link was applied but never marked spent"


async def test_a_confirmation_link_works_exactly_once(app_session: AppSessionFactory) -> None:
    """The link is a bearer credential for somebody's login address. A second
    click must not be able to re-apply it -- nor to tell its holder that it
    once worked."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        member = _member(tenant, email)
        db.add(member)
        await db.flush()
        _mint_token(
            db,
            tenant,
            member.id,
            purpose="email_change",
            plaintext="link-abc",
            new_email="ada@newmail.com",
        )

    async with _http() as http:
        first = await http.post("/api/v1/auth/email/confirm", json={"token": "link-abc"})
        second = await http.post("/api/v1/auth/email/confirm", json={"token": "link-abc"})
    assert first.status_code == 200, first.text
    assert second.status_code == 400, second.text
    assert second.json() == {"detail": INVALID_LINK_MESSAGE}


async def test_every_broken_confirmation_link_says_the_same_sentence(
    app_session: AppSessionFactory,
) -> None:
    """Unknown, expired, already spent, and minted for the OTHER purpose --
    four different facts about somebody else's account, one answer.

    The wrong-purpose case is the one the table's `purpose` CHECK constraint
    was written for and nothing exercised until now: a `password_reset` link
    must not be spendable as an email confirmation. It is refused because
    `purpose` is part of the lookup's WHERE clause, so the row is never even
    found -- not spent, not reported.
    """
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        member = _member(tenant, email)
        db.add(member)
        await db.flush()
        _mint_token(
            db,
            tenant,
            member.id,
            purpose="email_change",
            plaintext="expired",
            new_email="ada@newmail.com",
            expires_in=-dt.timedelta(hours=1),
        )
        _mint_token(
            db,
            tenant,
            member.id,
            purpose="email_change",
            plaintext="spent",
            new_email="ada@newmail.com",
            used=True,
        )
        _mint_token(db, tenant, member.id, purpose="password_reset", plaintext="wrong-purpose")

    answers = []
    async with _http() as http:
        for token in ("never-existed", "expired", "spent", "wrong-purpose"):
            r = await http.post("/api/v1/auth/email/confirm", json={"token": token})
            answers.append((token, r.status_code, r.json()))

    for token, code, payload in answers:
        assert code == 400, f"{token} answered {code}"
        assert payload == {"detail": INVALID_LINK_MESSAGE}, f"{token} answered {payload}"

    async with app_session(tenant) as db:
        assert (await _row(db, tenant, email)).subject == email
        # The wrong-purpose reset link is still unspent: a refused lookup must
        # not consume the link it refused.
        reset = [t for t in await _tokens(db, tenant) if t.purpose == "password_reset"]
        assert [t.used_at for t in reset] == [None]


async def test_confirming_an_address_somebody_else_claimed_meanwhile_conflicts(
    app_session: AppSessionFactory,
) -> None:
    """An hour is long enough for the target address to be taken, and
    `(tenant_id, subject)` is unique -- the loser of that race would otherwise
    look like it had simply vanished. The link survives, so the person can
    free the address and click it again."""
    tenant = uuid.uuid4()
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        ada = _member(tenant, "ada@example.com")
        db.add(ada)
        db.add(_member(tenant, "grace@example.com"))
        await db.flush()
        _mint_token(
            db,
            tenant,
            ada.id,
            purpose="email_change",
            plaintext="link-abc",
            new_email="grace@example.com",
        )

    async with _http() as http:
        r = await http.post("/api/v1/auth/email/confirm", json={"token": "link-abc"})
    assert r.status_code == 409, r.text

    async with app_session(tenant) as db:
        assert (await _row(db, tenant, "ada@example.com")).subject == "ada@example.com"
        assert [t.used_at for t in await _tokens(db, tenant)] == [None], (
            "a refused confirmation must not burn the link it refused"
        )


# --- POST /auth/password/forgot ------------------------------------------


async def test_forgot_password_answers_a_real_and_an_unknown_address_identically(
    app_session: AppSessionFactory,
) -> None:
    """The property the whole endpoint exists to hold: the response is the
    same object, so it cannot be used to test whether somebody has an account
    here -- which is precisely what the login form is careful not to be."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))
        await _configure_smtp(db, tenant)

    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = MagicMock()
        smtp_cls.return_value.__enter__.return_value = client
        async with _http() as http:
            real = await http.post("/api/v1/auth/password/forgot", json={"email": email})
            fake = await http.post(
                "/api/v1/auth/password/forgot", json={"email": "nobody@example.com"}
            )

    assert real.status_code == fake.status_code == 202
    assert real.json() == fake.json() == {"message": FORGOT_PASSWORD_MESSAGE}
    assert real.content == fake.content, "byte-identical, not merely equal once parsed"

    # ...and behind that identical answer, exactly one link was mailed, to the
    # address that actually has an account.
    assert len(_mailed_links(client)) == 1
    async with app_session(tenant) as db:
        rows = await _tokens(db, tenant)
    assert [r.purpose for r in rows] == ["password_reset"]


async def test_forgot_password_answers_identically_with_no_mail_server(
    app_session: AppSessionFactory,
) -> None:
    """"Not configured" must not be distinguishable from "no such account"
    either -- an instance's mail setup is not the caller's business."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))

    async with _http() as http:
        r = await http.post("/api/v1/auth/password/forgot", json={"email": email})
    assert r.status_code == 202, r.text
    assert r.json() == {"message": FORGOT_PASSWORD_MESSAGE}

    async with app_session(tenant) as db:
        assert await _tokens(db, tenant) == []


async def test_forgot_password_answers_identically_when_the_relay_refuses(
    app_session: AppSessionFactory,
) -> None:
    """Unlike `PUT /auth/me/email`, which owes an authenticated caller a 502,
    this route must swallow a failed send: a 502 here says "that address
    exists" as loudly as a 200 would."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))
        await _configure_smtp(db, tenant)

    with patch("oc8.credentials.smtp.smtplib.SMTP", side_effect=OSError("connection refused")):
        async with _http() as http:
            r = await http.post("/api/v1/auth/password/forgot", json={"email": email})
    assert r.status_code == 202, r.text
    assert r.json() == {"message": FORGOT_PASSWORD_MESSAGE}


async def test_forgot_password_says_nothing_different_about_a_member_with_no_password(
    app_session: AppSessionFactory,
) -> None:
    """An SSO/dev-token identity has no password for a reset to reset. Same
    answer, and no link mailed to an account that could not use one."""
    tenant = uuid.uuid4()
    email = "sso@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        db.add(_member(tenant, email, password=None))
        await _configure_smtp(db, tenant)

    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = MagicMock()
        smtp_cls.return_value.__enter__.return_value = client
        async with _http() as http:
            r = await http.post("/api/v1/auth/password/forgot", json={"email": email})
    assert r.status_code == 202, r.text
    assert r.json() == {"message": FORGOT_PASSWORD_MESSAGE}
    client.send_message.assert_not_called()
    async with app_session(tenant) as db:
        assert await _tokens(db, tenant) == []


async def test_a_second_reset_request_spends_the_first_link(
    app_session: AppSessionFactory,
) -> None:
    """Once the cooldown has passed, a repeat request collapses the flow back
    to ONE usable link: the older one is spent as the newer one is minted, so
    a link somebody was already mailed stops working."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))
        await _configure_smtp(db, tenant)

    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = MagicMock()
        smtp_cls.return_value.__enter__.return_value = client
        async with _http() as http:
            await http.post("/api/v1/auth/password/forgot", json={"email": email})
            # Past the cooldown -- a second request inside it is the subject of
            # its own test below and deliberately mints nothing.
            await _age_the_tokens(app_session, tenant, seconds=600)
            await http.post("/api/v1/auth/password/forgot", json={"email": email})

            first, second = _mailed_links(client)
            stale = await http.post(
                "/api/v1/auth/password/reset",
                json={"token": first, "newPassword": "from-the-stale-link"},
            )
            fresh = await http.post(
                "/api/v1/auth/password/reset",
                json={"token": second, "newPassword": "from-the-fresh-link"},
            )

    assert stale.status_code == 400, stale.text
    assert stale.json() == {"detail": INVALID_LINK_MESSAGE}
    assert fresh.status_code == 204, fresh.text

    async with app_session(tenant) as db:
        stored = (await _row(db, tenant, email)).password_hash
    assert stored is not None
    assert verify_password("from-the-fresh-link", stored)
    assert not verify_password("from-the-stale-link", stored)


async def test_a_second_reset_request_inside_the_cooldown_mints_nothing(
    app_session: AppSessionFactory,
) -> None:
    """The cooldown §3 of the design asks for, and the gap the token-spending
    above does NOT close: spending the old link bounds how many links are
    USABLE (one), not how many emails are SENT. Without this, anybody who
    knows a member's address can mail that inbox in a loop, unauthenticated,
    for ever -- and each of those loops also costs an SMTP round trip."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))
        await _configure_smtp(db, tenant)

    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = MagicMock()
        smtp_cls.return_value.__enter__.return_value = client
        async with _http() as http:
            first = await http.post("/api/v1/auth/password/forgot", json={"email": email})
            second = await http.post("/api/v1/auth/password/forgot", json={"email": email})

    # Byte-identical, like every other pair of answers this route gives: a
    # caller must not be able to tell a throttled request from a sent one
    # either, or the cooldown becomes the oracle the endpoint exists to deny.
    assert first.status_code == second.status_code == 202
    assert first.content == second.content
    assert len(_mailed_links(client)) == 1, "the second request mailed a second link"
    async with app_session(tenant) as db:
        assert len(await _tokens(db, tenant)) == 1


async def test_a_reset_request_after_the_cooldown_mints_again(
    app_session: AppSessionFactory,
) -> None:
    """The other half: the cooldown is a cooldown, not a lockout. Somebody who
    never received the first mail must be able to ask again."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))
        await _configure_smtp(db, tenant)

    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = MagicMock()
        smtp_cls.return_value.__enter__.return_value = client
        async with _http() as http:
            await http.post("/api/v1/auth/password/forgot", json={"email": email})
            await _age_the_tokens(app_session, tenant, seconds=600)
            r = await http.post("/api/v1/auth/password/forgot", json={"email": email})

    assert r.status_code == 202, r.text
    assert len(_mailed_links(client)) == 2
    async with app_session(tenant) as db:
        rows = await _tokens(db, tenant)
    assert len(rows) == 2
    # ...and only the newest of them is still usable.
    assert sorted(t.used_at is None for t in rows) == [False, True]


async def test_an_unused_link_does_not_hold_back_a_request_for_ever(
    app_session: AppSessionFactory,
) -> None:
    """The window is measured from when the link was MINTED, not from whether
    it was ever spent -- an hour-old unused link must not throttle anything."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        member = _member(tenant, email)
        db.add(member)
        await _configure_smtp(db, tenant)
        await db.flush()
        _mint_token(db, tenant, member.id, purpose="password_reset", plaintext="older")
    await _age_the_tokens(app_session, tenant, seconds=3600)

    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = MagicMock()
        smtp_cls.return_value.__enter__.return_value = client
        async with _http() as http:
            r = await http.post("/api/v1/auth/password/forgot", json={"email": email})
    assert r.status_code == 202, r.text
    assert len(_mailed_links(client)) == 1


async def test_the_reset_socket_is_opened_with_no_db_connection_held(
    app_session: AppSessionFactory,
) -> None:
    """The same property as the confirmation mail, on the route where it
    actually matters: this one is UNAUTHENTICATED, so holding a pooled DB
    connection for the length of an SMTP dial is a one-command outage of every
    other request the process serves. See `_watch_the_pool`."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))
        await _configure_smtp(db, tenant)

    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        checkouts = _watch_the_pool(smtp_cls)
        async with _http() as http:
            r = await http.post("/api/v1/auth/password/forgot", json={"email": email})
    assert r.status_code == 202, r.text
    assert checkouts == [0], (
        f"the SMTP socket was opened with {checkouts} DB connection(s) checked out"
    )


async def test_forgot_password_refuses_something_that_is_not_an_address(
    app_session: AppSessionFactory,
) -> None:
    """A 422 on a malformed body says nothing about who has an account -- it is
    a fact about the request, not about the instance."""
    tenant = uuid.uuid4()
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        db.add(_member(tenant, "ada@example.com"))
        await _configure_smtp(db, tenant)

    async with _http() as http:
        r = await http.post("/api/v1/auth/password/forgot", json={"email": "not-an-address"})
    assert r.status_code == 422, r.text


async def test_forgot_password_finds_the_member_whatever_the_casing(
    app_session: AppSessionFactory,
) -> None:
    """The address is normalized to lowercase before it is compared, so a
    member whose stored subject was written in another casing must still be
    reachable -- otherwise normalizing would lock exactly those people out of
    the only route they have left."""
    tenant = uuid.uuid4()
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        db.add(_member(tenant, "Ada@Example.com"))
        await _configure_smtp(db, tenant)

    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = MagicMock()
        smtp_cls.return_value.__enter__.return_value = client
        async with _http() as http:
            r = await http.post("/api/v1/auth/password/forgot", json={"email": "ADA@example.com"})
    assert r.status_code == 202, r.text
    assert len(_mailed_links(client)) == 1
    assert client.send_message.call_args.args[0]["To"] == "Ada@Example.com", (
        "the link must be mailed to the address on the account, not to the casing typed"
    )


async def test_the_reset_link_points_at_the_configured_frontend(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same setting the confirmation link is built off (`frontend_base_url`),
    for the same reason: a link built off anything else lands on a host the
    person cannot reach."""
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(), "frontend_base_url", "https://oc8.example.test/", raising=False
    )
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))
        await _configure_smtp(db, tenant)

    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = MagicMock()
        smtp_cls.return_value.__enter__.return_value = client
        async with _http() as http:
            r = await http.post("/api/v1/auth/password/forgot", json={"email": email})
    assert r.status_code == 202, r.text
    body_text = client.send_message.call_args.args[0].get_content()
    assert "https://oc8.example.test/reset-password?token=" in body_text


# --- POST /auth/password/reset -------------------------------------------


async def test_a_reset_link_replaces_the_hash_and_the_new_password_logs_in(
    app_session: AppSessionFactory,
) -> None:
    """The end of the flow, checked where it matters: not that a column
    changed, but that the person can now actually get in."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        member = _member(tenant, email)
        db.add(member)
        await db.flush()
        _mint_token(db, tenant, member.id, purpose="password_reset", plaintext="reset-abc")

    async with _http() as http:
        r = await http.post(
            "/api/v1/auth/password/reset",
            json={"token": "reset-abc", "newPassword": "brand-new-password"},
        )
        assert r.status_code == 204, r.text
        assert r.text == "", "a password route must echo nothing at all"

        login = await http.post(
            "/api/v1/auth/login", json={"email": email, "password": "brand-new-password"}
        )
    assert login.status_code == 200, login.text

    async with app_session(tenant) as db:
        stored = (await _row(db, tenant, email)).password_hash
        assert "member.password_reset" in await _actions(db, tenant)
        assert [t.used_at for t in await _tokens(db, tenant)] != [None]
    assert stored is not None
    assert not verify_password(OLD_PASSWORD, stored)


async def test_an_invite_link_also_sets_a_password(app_session: AppSessionFactory) -> None:
    """`purpose="invite"` (minted by `POST /members` for a member created with
    no password, see `test_member_administration.py`) is redeemed by this
    SAME route, not a second one -- `_redeem_token` now accepts either
    purpose because both authorize the identical action, "set a new
    password". Mirrors `test_a_reset_link_replaces_the_hash_and_the_new_password_logs_in`
    exactly, minting an `invite`-purpose token instead of `password_reset`."""
    tenant = uuid.uuid4()
    email = "newhire@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        member = _member(tenant, email, password=None)
        db.add(member)
        await db.flush()
        _mint_token(db, tenant, member.id, purpose="invite", plaintext="invite-abc")

    async with _http() as http:
        r = await http.post(
            "/api/v1/auth/password/reset",
            json={"token": "invite-abc", "newPassword": "brand-new-password"},
        )
        assert r.status_code == 204, r.text

        login = await http.post(
            "/api/v1/auth/login", json={"email": email, "password": "brand-new-password"}
        )
    assert login.status_code == 200, login.text

    async with app_session(tenant) as db:
        stored = (await _row(db, tenant, email)).password_hash
        assert "member.password_reset" in await _actions(db, tenant)
        assert [t.used_at for t in await _tokens(db, tenant)] != [None]
    assert stored is not None


async def test_a_reset_link_works_exactly_once(app_session: AppSessionFactory) -> None:
    """A spent link left live is a permanent skeleton key sitting in an inbox:
    anybody who later reads that mailbox owns the account."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        member = _member(tenant, email)
        db.add(member)
        await db.flush()
        _mint_token(db, tenant, member.id, purpose="password_reset", plaintext="reset-abc")

    async with _http() as http:
        first = await http.post(
            "/api/v1/auth/password/reset",
            json={"token": "reset-abc", "newPassword": "the-owners-password"},
        )
        second = await http.post(
            "/api/v1/auth/password/reset",
            json={"token": "reset-abc", "newPassword": "the-thiefs-password"},
        )
    assert first.status_code == 204, first.text
    assert second.status_code == 400, second.text
    assert second.json() == {"detail": INVALID_LINK_MESSAGE}

    async with app_session(tenant) as db:
        stored = (await _row(db, tenant, email)).password_hash
    assert stored is not None
    assert verify_password("the-owners-password", stored)
    assert not verify_password("the-thiefs-password", stored)


async def test_every_broken_reset_link_says_the_same_sentence(
    app_session: AppSessionFactory,
) -> None:
    """The mirror of the confirm-side test, and the other half of the
    wrong-purpose case: an `email_change` link must not be spendable as a
    password reset. If it were, the mail that says "confirm your new address"
    would silently double as a password-change authorization."""
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        member = _member(tenant, email)
        db.add(member)
        await db.flush()
        _mint_token(
            db,
            tenant,
            member.id,
            purpose="password_reset",
            plaintext="expired",
            expires_in=-dt.timedelta(hours=1),
        )
        _mint_token(db, tenant, member.id, purpose="password_reset", plaintext="spent", used=True)
        _mint_token(
            db,
            tenant,
            member.id,
            purpose="email_change",
            plaintext="wrong-purpose",
            new_email="ada@newmail.com",
        )

    answers = []
    async with _http() as http:
        for token in ("never-existed", "expired", "spent", "wrong-purpose"):
            r = await http.post(
                "/api/v1/auth/password/reset",
                json={"token": token, "newPassword": "attacker-chosen-password"},
            )
            answers.append((token, r.status_code, r.json()))

    for token, code, payload in answers:
        assert code == 400, f"{token} answered {code}"
        assert payload == {"detail": INVALID_LINK_MESSAGE}, f"{token} answered {payload}"

    async with app_session(tenant) as db:
        row = await _row(db, tenant, email)
        # Neither the password nor the email-change link moved.
        assert row.subject == email
        pending = [t for t in await _tokens(db, tenant) if t.purpose == "email_change"]
        assert [t.used_at for t in pending] == [None]
    assert row.password_hash is not None
    assert verify_password(OLD_PASSWORD, row.password_hash)
    assert not verify_password("attacker-chosen-password", row.password_hash)


async def test_a_link_from_another_instance_is_not_usable_here(
    app_session: AppSessionFactory,
) -> None:
    """The token lookup is scoped to the resolved singleton organization, not
    to whatever tenant the row happens to name. A row belonging to some other
    tenant is invisible under RLS and answers like any other dead link."""
    tenant = uuid.uuid4()
    stranger = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        db.add(_member(tenant, email))
    async with app_session(stranger) as db:
        foreign = _member(stranger, "eve@example.com")
        db.add(foreign)
        await db.flush()
        _mint_token(db, stranger, foreign.id, purpose="password_reset", plaintext="foreign")

    async with _http() as http:
        r = await http.post(
            "/api/v1/auth/password/reset",
            json={"token": "foreign", "newPassword": "brand-new-password"},
        )
    assert r.status_code == 400, r.text
    assert r.json() == {"detail": INVALID_LINK_MESSAGE}


async def test_two_requests_racing_on_one_link_spend_it_exactly_once(
    app_session: AppSessionFactory,
) -> None:
    """The property `with_for_update()` is there for, proved rather than argued.

    Without the row lock this is a read-modify-write with an `await` in the
    middle: both transactions see `used_at IS NULL`, both write, the second
    simply queues on the row lock and then applies on top -- and one link has
    authorized two actions. With it, the loser blocks, PostgreSQL re-checks
    the WHERE clause against the row it finds when the lock is released
    (READ COMMITTED EvalPlanQual), the row no longer qualifies, and the loser
    gets the ordinary generic refusal.

    Driven at `_redeem_token` rather than through HTTP because the two
    attempts have to be genuinely concurrent ON THE SAME ROW, with the winner
    still holding its transaction open when the loser arrives -- which is
    exactly what the `await` inside the block arranges.
    """
    tenant = uuid.uuid4()
    email = "ada@example.com"
    await _sole_organization(app_session, tenant)
    async with app_session(tenant) as db:
        member = _member(tenant, email)
        db.add(member)
        await db.flush()
        _mint_token(db, tenant, member.id, purpose="password_reset", plaintext="race")

    async def _attempt() -> bool:
        async with app_session(tenant) as db:
            try:
                await _redeem_token(db, tenant_id=tenant, token="race", purpose="password_reset")
            except HTTPException:
                return False
            # Still inside the transaction, still holding the row lock: this
            # is the window the other attempt has to survive.
            await asyncio.sleep(0.25)
            return True

    outcomes = await asyncio.gather(_attempt(), _attempt())
    assert sorted(outcomes) == [False, True], (
        f"one link, two concurrent redemptions, outcomes {outcomes} -- "
        "a link that can be spent twice authorizes two actions"
    )

    async with app_session(tenant) as db:
        assert [t.used_at is not None for t in await _tokens(db, tenant)] == [True]
