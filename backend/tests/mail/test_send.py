"""`active_smtp_credential` + `send_mail`.

The socket is mocked at `oc8.credentials.smtp.smtplib` on purpose -- that
is the ONE module allowed to open an SMTP connection, and patching there
(rather than inside `oc8.mail.send`) is what makes these tests fail if the
send path ever grows a second, private copy of the dialling logic. The
"parity" tests below assert the send path makes the same implicit-TLS and
certificate-verification decisions as the credential's own Test button.
"""

from __future__ import annotations

import base64
import logging
import ssl
import uuid
from unittest.mock import MagicMock, patch

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.credentials.service import create_credential
from oc8.credentials.smtp import validate_smtp
from oc8.mail.send import active_smtp_credential, send_mail
from oc8.secrets.keyprovider import SecretStoreUnavailable
from oc8.secrets.service import SecretNotFound
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    """`create_credential`/`resolve_credential_field` put the secret-kind
    `password` field through the real vault, which needs a KEK to encrypt or
    decrypt anything (mirrors tests/credentials/test_service.py)."""
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


def _smtp_client(smtp_cls: MagicMock) -> MagicMock:
    """Wire a patched SMTP class up so `with SMTP(...)` yields a mock."""
    client = MagicMock()
    smtp_cls.return_value.__enter__.return_value = client
    return client


async def _tenant_with_smtp(db: AsyncSession, tenant: uuid.UUID, **overrides: str) -> uuid.UUID:
    """An Organization whose active SMTP pointer is a real credential."""
    fields = {"host": "smtp.example.com", "port": "587", "from_address": "a@b.com"}
    fields.update(overrides)
    db.add(m.Organization(id=tenant, slug=str(tenant), name="t", settings={}))
    await db.flush()
    credential = await create_credential(
        db,
        tenant_id=tenant,
        name="smtp",
        credential_type="smtp_server",
        field_values=fields,
    )
    org = await db.get(m.Organization, tenant)
    assert org is not None
    org.settings = {**org.settings, "active_smtp_credential_id": str(credential.id)}
    await db.flush()
    return credential.id


# --- the pointer ---------------------------------------------------------


async def test_no_active_credential_returns_false(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(m.Organization(id=tenant, slug=str(tenant), name="t", settings={}))
        await db.flush()
        result = await send_mail(db, tenant_id=tenant, to="a@b.com", subject="s", body="b")
        assert result is False


async def test_active_smtp_credential_resolves_the_pointer(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        cred_id = uuid.uuid4()
        db.add(
            m.Organization(
                id=tenant,
                slug=str(tenant),
                name="t",
                settings={"active_smtp_credential_id": str(cred_id)},
            )
        )
        await db.flush()
        resolved = await active_smtp_credential(db, tenant_id=tenant)
        assert resolved == cred_id


async def test_a_missing_organization_has_no_active_credential(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        assert await active_smtp_credential(db, tenant_id=tenant) is None


async def test_a_non_uuid_pointer_is_treated_as_unset(app_session: AppSessionFactory) -> None:
    """Hand-edited settings JSON must not turn into a 500 on the login page."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(
            m.Organization(
                id=tenant,
                slug=str(tenant),
                name="t",
                settings={"active_smtp_credential_id": "not-a-uuid"},
            )
        )
        await db.flush()
        assert await active_smtp_credential(db, tenant_id=tenant) is None
        assert await send_mail(db, tenant_id=tenant, to="a@b.com", subject="s", body="b") is False


async def test_a_dangling_pointer_returns_false(app_session: AppSessionFactory) -> None:
    """The credential was deleted after being selected -- same outcome as
    never having configured one, and still no exception."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(
            m.Organization(
                id=tenant,
                slug=str(tenant),
                name="t",
                settings={"active_smtp_credential_id": str(uuid.uuid4())},
            )
        )
        await db.flush()
        assert await send_mail(db, tenant_id=tenant, to="a@b.com", subject="s", body="b") is False


# --- sending -------------------------------------------------------------


async def test_send_mail_with_a_real_credential(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _tenant_with_smtp(db, tenant)
        with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
            client = _smtp_client(smtp_cls)
            result = await send_mail(db, tenant_id=tenant, to="x@y.com", subject="s", body="b")
        assert result is True
        client.send_message.assert_called_once()
        smtp_cls.assert_called_once()
        assert smtp_cls.call_args.args[:2] == ("smtp.example.com", 587)


async def test_the_message_carries_the_credentials_from_address(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _tenant_with_smtp(db, tenant, from_address="noreply@corp.example")
        with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
            client = _smtp_client(smtp_cls)
            await send_mail(db, tenant_id=tenant, to="x@y.com", subject="Reset", body="link")
        msg = client.send_message.call_args.args[0]
        assert msg["From"] == "noreply@corp.example"
        assert msg["To"] == "x@y.com"
        assert msg["Subject"] == "Reset"
        assert msg.get_content().strip() == "link"


async def test_credentials_are_used_when_the_credential_has_them(
    app_session: AppSessionFactory,
) -> None:
    """`password` is a secret-kind field, so this also proves the send path
    resolves it back out of the secret store rather than reading a blank."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _tenant_with_smtp(db, tenant, username="mailer", password="s3cret")
        with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
            client = _smtp_client(smtp_cls)
            assert await send_mail(db, tenant_id=tenant, to="x@y.com", subject="s", body="b")
        client.login.assert_called_once_with("mailer", "s3cret")


async def test_use_tls_false_sends_without_starttls(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _tenant_with_smtp(db, tenant, port="25", use_tls="false")
        with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
            client = _smtp_client(smtp_cls)
            assert await send_mail(db, tenant_id=tenant, to="x@y.com", subject="s", body="b")
        client.starttls.assert_not_called()


async def test_a_send_failure_returns_false_instead_of_raising(
    app_session: AppSessionFactory,
) -> None:
    """Forgot-password must answer identically whether the mail left or not,
    so nothing from smtplib is allowed to escape."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _tenant_with_smtp(db, tenant)
        with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
            smtp_cls.side_effect = OSError("connection refused")
            result = await send_mail(db, tenant_id=tenant, to="x@y.com", subject="s", body="b")
        assert result is False


async def test_an_unparseable_port_returns_false(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _tenant_with_smtp(db, tenant, port="five-eight-seven")
        with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
            result = await send_mail(db, tenant_id=tenant, to="x@y.com", subject="s", body="b")
        assert result is False
        smtp_cls.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        SecretNotFound("credential/x/password"),
        SecretStoreUnavailable("secret_kek is not configured"),
        InvalidTag(),
    ],
    ids=["secret-row-gone", "kek-misconfigured", "bad-decrypt"],
)
async def test_a_vault_failure_returns_false_instead_of_raising(
    app_session: AppSessionFactory, error: Exception
) -> None:
    """Resolving the secret-kind `password` field reaches into the vault,
    whose failures are neither credential errors nor `ValueError`s -- a
    rotated KEK or a deleted secret row must still be "no usable mail
    server", not an exception surfacing in a forgot-password handler."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _tenant_with_smtp(db, tenant, username="mailer", password="s3cret")
        with (
            patch("oc8.credentials.service.resolve_secret", side_effect=error),
            patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls,
        ):
            result = await send_mail(db, tenant_id=tenant, to="x@y.com", subject="s", body="b")
        assert result is False
        smtp_cls.assert_not_called()


# --- server-side evidence ------------------------------------------------


async def test_a_resolution_failure_is_logged(
    app_session: AppSessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _tenant_with_smtp(db, tenant, username="mailer", password="s3cret")
        with (
            caplog.at_level(logging.WARNING, logger="oc8.mail.send"),
            patch(
                "oc8.credentials.service.resolve_secret",
                side_effect=SecretStoreUnavailable("secret_kek is not configured"),
            ),
        ):
            assert (
                await send_mail(db, tenant_id=tenant, to="x@y.com", subject="s", body="b") is False
            )
    record = next(r for r in caplog.records if r.name == "oc8.mail.send")
    assert record.levelno == logging.WARNING
    assert "could not be resolved" in record.getMessage()
    # The traceback is the whole point: without it a rotated KEK and a
    # never-set host are indistinguishable in the log.
    assert record.exc_info is not None


async def test_a_send_failure_is_logged(
    app_session: AppSessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """A relay that rejects our certificate or our password otherwise
    produces a password-reset flow that looks fine from every angle a user
    or an admin can see, with no server-side evidence at all."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _tenant_with_smtp(db, tenant)
        with (
            caplog.at_level(logging.WARNING, logger="oc8.mail.send"),
            patch("oc8.credentials.smtp.smtplib.SMTP", side_effect=OSError("connection refused")),
        ):
            assert (
                await send_mail(db, tenant_id=tenant, to="x@y.com", subject="s", body="b") is False
            )
    record = next(r for r in caplog.records if r.name == "oc8.mail.send")
    assert record.levelno == logging.WARNING
    assert "x@y.com" in record.getMessage()
    assert record.exc_info is not None


async def test_an_unconfigured_mail_server_is_logged(
    app_session: AppSessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(m.Organization(id=tenant, slug=str(tenant), name="t", settings={}))
        await db.flush()
        with caplog.at_level(logging.WARNING, logger="oc8.mail.send"):
            assert (
                await send_mail(db, tenant_id=tenant, to="x@y.com", subject="s", body="b") is False
            )
    record = next(r for r in caplog.records if r.name == "oc8.mail.send")
    assert "no active SMTP credential" in record.getMessage()


# --- parity with the credential's own "Test" button ----------------------


async def test_port_465_sends_over_implicit_tls_just_like_test_does(
    app_session: AppSessionFactory,
) -> None:
    """A 465 credential that tests green must SEND the same way. If these two
    ever diverge, an admin gets a green tick from a connection shape that the
    real password-reset mail never uses."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _tenant_with_smtp(db, tenant, port="465")
        with (
            patch("oc8.credentials.smtp.smtplib.SMTP_SSL") as ssl_cls,
            patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls,
        ):
            client = _smtp_client(ssl_cls)
            assert await send_mail(db, tenant_id=tenant, to="x@y.com", subject="s", body="b")
            send_factory_calls = ssl_cls.call_count, smtp_cls.call_count
            client.starttls.assert_not_called()
            client.send_message.assert_called_once()

            ssl_cls.reset_mock()
            smtp_cls.reset_mock()
            _smtp_client(ssl_cls)
            await validate_smtp(
                {"host": "smtp.example.com", "port": "465", "from_address": "a@b.com"}
            )
            test_factory_calls = ssl_cls.call_count, smtp_cls.call_count

    assert send_factory_calls == test_factory_calls == (1, 0)


@pytest.mark.parametrize(
    ("port", "patched"),
    [
        ("587", "oc8.credentials.smtp.smtplib.SMTP"),
        ("465", "oc8.credentials.smtp.smtplib.SMTP_SSL"),
    ],
)
async def test_both_tls_paths_verify_the_servers_certificate(
    app_session: AppSessionFactory, port: str, patched: str
) -> None:
    """One TLS decision, applied on both the implicit-TLS and the STARTTLS
    branch: smtplib's own default (`check_hostname=False`, `CERT_NONE`)
    would encrypt without proving who is on the other end."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _tenant_with_smtp(db, tenant, port=port)
        with patch(patched) as smtp_cls:
            client = _smtp_client(smtp_cls)
            assert await send_mail(db, tenant_id=tenant, to="x@y.com", subject="s", body="b")
        if port == "465":
            context = smtp_cls.call_args.kwargs["context"]
        else:
            context = client.starttls.call_args.kwargs["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED
