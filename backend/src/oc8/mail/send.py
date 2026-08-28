"""Synchronous transactional-email sending via whichever credential is
marked as the tenant's active SMTP server (design:
docs/superpowers/specs/2026-08-28-account-self-service-design.md §4.2-4.3).
Two email types only (a reset link, a confirmation link) -- plain text,
no templates, no queue. `send_mail` never raises: a caller that must not
leak "does this address exist" (forgot-password) returns the same response
whether the send actually happened or not.

The socket itself is opened by `oc8.credentials.smtp.smtp_connection`, the
same helper the credential's "Test" button runs. That is on purpose: a
credential that tests green is dialled here in exactly the same way (same
implicit-TLS-on-465 branch, same certificate-verification policy), so
"Test" can never bless a configuration that "Send" would then treat
differently.
"""

from __future__ import annotations

import asyncio
import uuid
from email.message import EmailMessage

from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.credentials.service import (
    CredentialFieldNotSet,
    CredentialNotFound,
    resolve_credential_field,
)
from oc8.credentials.smtp import smtp_connection


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


async def send_mail(
    db: AsyncSession, *, tenant_id: uuid.UUID, to: str, subject: str, body: str
) -> bool:
    credential_id = await active_smtp_credential(db, tenant_id=tenant_id)
    if credential_id is None:
        return False
    try:
        host = await resolve_credential_field(
            db, tenant_id=tenant_id, credential_id=credential_id, field_key="host"
        )
        # ValueError is caught alongside the credential errors below: a port
        # typed as "five-eight-seven" is a broken configuration, not an
        # exception this function is allowed to leak to a forgot-password
        # handler that must answer identically either way.
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
    except (CredentialNotFound, CredentialFieldNotSet, ValueError):
        return False
    try:
        await asyncio.to_thread(
            _send, host, port, username, password, use_tls, from_address, to, subject, body
        )
    except Exception:
        return False
    return True
