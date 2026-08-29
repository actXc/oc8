"""Synchronous transactional-email sending via whichever credential is
marked as the tenant's active SMTP server (design:
docs/superpowers/specs/2026-08-28-account-self-service-design.md §4.2-4.3).
Two email types only (a reset link, a confirmation link) -- plain text,
no templates, no queue. Neither function here raises: a caller that must not
leak "does this address exist" (forgot-password) returns the same response
whether the send actually happened or not.

**Sending is deliberately two calls, and the split is the whole point.**

`resolve_smtp_config` reads the active credential and its vaulted fields, so
it MUST run inside the caller's open, tenant-bound transaction: RLS binds
`app.tenant_id` transaction-locally (`set_config(..., is_local=true)`), and a
commit before this read would unbind it and hand back an empty credential.

`deliver` opens the socket, and it takes NO database argument at all, so it
cannot be called with a transaction open by accident. That is not tidiness:
`smtp_connection` applies its 10-second timeout PER SOCKET OPERATION
(connect, STARTTLS, EHLO, auth, send), so a blackholed relay holds its caller
for up to ~40 seconds. Held inside the transaction -- which is what a single
combined `send_mail(db, ...)` forced -- that is a pooled DB connection held
for 40 seconds by an UNAUTHENTICATED request: with the engine's default
`pool_size=5, max_overflow=10`, roughly 15 concurrent calls to
`/auth/password/forgot` exhaust the pool for the entire process and every
other request then blocks `pool_timeout=30` and fails. So the two halves are
separate functions: resolve while bound, let the transaction close, then dial.

The socket itself is opened by `oc8.credentials.smtp.smtp_connection`, the
same helper the credential's "Test" button runs. That is on purpose: a
credential that tests green is dialled here in exactly the same way (same
implicit-TLS-on-465 branch, same certificate-verification policy), so
"Test" can never bless a configuration that "Send" would then treat
differently.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from email.message import EmailMessage

from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.credentials.service import CredentialFieldNotSet, resolve_credential_field
from oc8.credentials.smtp import smtp_connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmtpConfig:
    """Everything one send needs, with nothing left to look up.

    Frozen and plain: once this exists the database is out of the picture, which
    is exactly what lets `deliver` run with no connection checked out. It
    carries `tenant_id`/`credential_id` only so a failed delivery can still name
    them in the server-side log line -- that log is the only evidence an
    operator gets that a reset link never left the building.
    """

    tenant_id: uuid.UUID
    credential_id: uuid.UUID
    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    from_address: str


async def active_smtp_credential(db: AsyncSession, *, tenant_id: uuid.UUID) -> uuid.UUID | None:
    """The credential id `Organization.settings["active_smtp_credential_id"]`
    points at, or `None` if unset. Does NOT validate the credential still
    exists -- callers already tolerate a stale/deleted pointer by treating
    any resolution failure the same as "no mail server configured"."""
    organization = await db.get(m.Organization, tenant_id)
    if organization is None:
        return None
    raw = organization.settings.get("active_smtp_credential_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def _send(
    host: str,
    port: int,
    username: str,
    password: str,
    use_tls: bool,
    from_address: str,
    to: str,
    subject: str,
    body: str,
) -> None:
    """Blocking send. Run via `asyncio.to_thread` -- smtplib is synchronous
    and a slow relay must not stall every other request this process serves."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = to
    msg.set_content(body)
    with smtp_connection(
        host=host, port=port, username=username, password=password, use_tls=use_tls
    ) as client:
        client.send_message(msg)


async def resolve_smtp_config(db: AsyncSession, *, tenant_id: uuid.UUID) -> SmtpConfig | None:
    """The tenant's usable mail server, or `None` with the reason logged.

    Runs inside the caller's OPEN, tenant-bound transaction -- see this
    module's docstring: the credential and its vaulted password are RLS-scoped
    reads, and `app.tenant_id` dies at commit.

    `None` covers every way this can fail to produce a working configuration
    (no pointer, dangling pointer, unset field, unparseable port, vault
    unreachable). They all mean the same thing to a caller -- "this tenant has
    no usable mail server" -- and the caller is deliberately told nothing more.
    """
    credential_id = await active_smtp_credential(db, tenant_id=tenant_id)
    if credential_id is None:
        logger.warning(
            "mail not sent for tenant %s: no active SMTP credential is configured", tenant_id
        )
        return None
    # Blanket `except Exception` rather than an enumerated tuple, on purpose.
    # Resolving these fields reaches through `resolve_credential_field` into
    # the secret vault, whose failure modes (`SecretNotFound`,
    # `SecretStoreUnavailable`, `cryptography`'s `InvalidTag` on a bad
    # decrypt) share no base class with the credential errors and are not
    # `ValueError`s -- an enumerated list silently drifts out of date every
    # time a layer below grows a new one, and the cost of that drift is this
    # function breaking its "never raises" contract inside a forgot-password
    # handler that must answer identically either way. Every one of them means
    # the same thing to a caller ("this tenant has no usable mail server"), so
    # they get the same handling: log it server-side, return None.
    try:
        host = await resolve_credential_field(
            db, tenant_id=tenant_id, credential_id=credential_id, field_key="host"
        )
        # int() on a port typed as "five-eight-seven" lands in the same place.
        port = int(
            await resolve_credential_field(
                db, tenant_id=tenant_id, credential_id=credential_id, field_key="port"
            )
        )
        from_address = await resolve_credential_field(
            db, tenant_id=tenant_id, credential_id=credential_id, field_key="from_address"
        )
        try:
            username = await resolve_credential_field(
                db, tenant_id=tenant_id, credential_id=credential_id, field_key="username"
            )
        except CredentialFieldNotSet:
            username = ""
        try:
            password = await resolve_credential_field(
                db, tenant_id=tenant_id, credential_id=credential_id, field_key="password"
            )
        except CredentialFieldNotSet:
            password = ""
        try:
            use_tls_raw = await resolve_credential_field(
                db, tenant_id=tenant_id, credential_id=credential_id, field_key="use_tls"
            )
        except CredentialFieldNotSet:
            use_tls_raw = "true"
        # Same rule as `validate_smtp`: only an explicit "false" turns TLS
        # off, so a blank or missing value still encrypts.
        use_tls = use_tls_raw.strip().lower() != "false"
    except Exception:
        # `exc_info=True` at WARNING, not `logger.exception` (which is ERROR):
        # without the traceback an InvalidTag from a rotated KEK and a
        # never-set `host` are the same log line, and an operator has nothing
        # else to go on -- the caller is deliberately told nothing.
        logger.warning(
            "mail not sent for tenant %s: SMTP credential %s could not be resolved",
            tenant_id,
            credential_id,
            exc_info=True,
        )
        return None
    return SmtpConfig(
        tenant_id=tenant_id,
        credential_id=credential_id,
        host=host,
        port=port,
        username=username,
        password=password,
        use_tls=use_tls,
        from_address=from_address,
    )


async def deliver(config: SmtpConfig, *, to: str, subject: str, body: str) -> bool:
    """Open the socket and send. NEVER raises, and takes no database session.

    The missing `db` parameter is the safety property (module docstring): this
    is the part that can block for ~40 seconds on a bad relay, and it must not
    be able to do that while a pooled connection is checked out. Call it AFTER
    the transaction that produced `config` has committed and closed.
    """
    try:
        await asyncio.to_thread(
            _send,
            config.host,
            config.port,
            config.username,
            config.password,
            config.use_tls,
            config.from_address,
            to,
            subject,
            body,
        )
    except Exception:
        # The whole point of this log line: a relay that rejects our
        # certificate, our password, or the connection itself otherwise
        # produces a password-reset flow that looks fine from every angle a
        # user or an admin can see, with no server-side evidence at all.
        logger.warning(
            "mail not sent for tenant %s: SMTP delivery to %s via credential %s failed",
            config.tenant_id,
            to,
            config.credential_id,
            exc_info=True,
        )
        return False
    return True
