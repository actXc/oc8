# backend/tests/api/test_totp_endpoints.py
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import uuid

import pyotp
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.auth.password import hash_password
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    # get_key_provider reads settings.secret_kek via get_settings(); set it so
    # the store is available (mirrors tests/api/test_mcp_logins.py) -- /confirm
    # calls store_secret to persist the TOTP shared secret in the vault.
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


def _client(app: object) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _make_member_with_full_session(
    app_session: AppSessionFactory, tenant: uuid.UUID, email: str
) -> str:
    async with app_session(tenant) as db:
        db.add(
            m.OrgMember(
                tenant_id=tenant,
                subject=email,
                subject_uuid=uuid.uuid5(uuid.NAMESPACE_URL, f"oc8:local:{email}"),
                password_hash=hash_password("Whatever123!"),
            )
        )
        await db.flush()
    return get_identity_provider().mint(tenant_id=tenant, subject=email, role="org_admin")


async def test_enroll_returns_a_secret_and_provisioning_uri_and_persists_nothing(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "enroll@example.com")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.post(
                "/api/v1/auth/totp/enroll", headers={"Authorization": f"Bearer {token}"}
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert "secret" in body and len(body["secret"]) >= 16
            assert body["provisioningUri"].startswith("otpauth://totp/")

    async with app_session(tenant) as db:
        rows = (await db.execute(m.TotpCredential.__table__.select())).fetchall()
        assert rows == []  # nothing persisted until /confirm


async def test_confirm_with_a_valid_code_creates_the_credential_and_returns_backup_codes(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "confirm@example.com")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            enroll = await c.post(
                "/api/v1/auth/totp/enroll", headers={"Authorization": f"Bearer {token}"}
            )
            secret = enroll.json()["secret"]
            code = pyotp.TOTP(secret).now()
            confirm = await c.post(
                "/api/v1/auth/totp/confirm",
                json={"secret": secret, "code": code},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert confirm.status_code == 200, confirm.text
            body = confirm.json()
            assert len(body["backupCodes"]) == 10

    async with app_session(tenant) as db:
        member = (
            await db.execute(
                m.OrgMember.__table__.select().where(m.OrgMember.subject == "confirm@example.com")
            )
        ).fetchone()
        cred = (
            await db.execute(
                m.TotpCredential.__table__.select().where(m.TotpCredential.member_id == member.id)
            )
        ).fetchone()
        assert cred is not None
        assert cred.enrolled_at is not None


async def test_confirm_writes_the_vault_secret_with_kind_totp(
    app_session: AppSessionFactory,
) -> None:
    """The generic /secrets CRUD's kind="totp" guard (fix round 1, finding
    F1) only protects a row that actually carries kind="totp" -- if
    totp_confirm ever stops passing that kwarg to store_secret, the guard in
    secrets.py stays green while every TOTP secret quietly becomes
    overwritable/deletable through the generic API again. This pins the real
    enroll -> confirm flow, not just secrets.py's own unit tests, so that
    regression can't hide behind an unrelated file's coverage."""
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "kind-check@example.com")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            enroll = await c.post(
                "/api/v1/auth/totp/enroll", headers={"Authorization": f"Bearer {token}"}
            )
            secret = enroll.json()["secret"]
            code = pyotp.TOTP(secret).now()
            confirm = await c.post(
                "/api/v1/auth/totp/confirm",
                json={"secret": secret, "code": code},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert confirm.status_code == 200, confirm.text

    async with app_session(tenant) as db:
        member = (
            await db.execute(
                m.OrgMember.__table__.select().where(
                    m.OrgMember.subject == "kind-check@example.com"
                )
            )
        ).fetchone()
        cred = (
            await db.execute(
                m.TotpCredential.__table__.select().where(m.TotpCredential.member_id == member.id)
            )
        ).fetchone()
        row = (
            await db.execute(m.Secret.__table__.select().where(m.Secret.name == cred.secret_ref))
        ).fetchone()
        assert row is not None
        assert row.kind == "totp"


async def test_confirm_clears_the_totp_grace_clock(app_session: AppSessionFactory) -> None:
    """models/identity.py's own docstring documents totp_grace_started_at as
    NULL for anyone who has enrolled -- Task 8 only starts the clock
    (assign_role/password_setup), nothing clears it at enrollment without
    this. Currently inert either way (password_login checks
    TotpCredential.enrolled_at before ever reading the clock), but the
    model's stated invariant deserves to be actually enforced, not just
    accidentally unreachable."""
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "clock-clear@example.com")

    async with app_session(tenant) as db:
        member = (
            await db.execute(
                m.OrgMember.__table__.select().where(
                    m.OrgMember.subject == "clock-clear@example.com"
                )
            )
        ).fetchone()
        await db.execute(
            m.OrgMember.__table__.update()
            .where(m.OrgMember.id == member.id)
            .values(totp_grace_started_at=dt.datetime.now(tz=dt.UTC))
        )
        await db.commit()

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            enroll = await c.post(
                "/api/v1/auth/totp/enroll", headers={"Authorization": f"Bearer {token}"}
            )
            secret = enroll.json()["secret"]
            code = pyotp.TOTP(secret).now()
            confirm = await c.post(
                "/api/v1/auth/totp/confirm",
                json={"secret": secret, "code": code},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert confirm.status_code == 200, confirm.text

    async with app_session(tenant) as db:
        member = (
            await db.execute(
                m.OrgMember.__table__.select().where(
                    m.OrgMember.subject == "clock-clear@example.com"
                )
            )
        ).fetchone()
        assert member.totp_grace_started_at is None


async def test_confirm_with_a_wrong_code_is_rejected_and_persists_nothing(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "wrongcode@example.com")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            enroll = await c.post(
                "/api/v1/auth/totp/enroll", headers={"Authorization": f"Bearer {token}"}
            )
            secret = enroll.json()["secret"]
            confirm = await c.post(
                "/api/v1/auth/totp/confirm",
                json={"secret": secret, "code": "000000"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert confirm.status_code == 422, confirm.text

    async with app_session(tenant) as db:
        rows = (await db.execute(m.TotpCredential.__table__.select())).fetchall()
        assert rows == []


async def test_backup_codes_never_appear_in_any_response_after_confirm(
    app_session: AppSessionFactory,
) -> None:
    """Confirm's own response is the ONE exception (checked by the test
    above via len() only, never by string match on a real code) -- this
    test proves nothing else that echoes the credential ever repeats them."""
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "leak-check@example.com")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            enroll = await c.post(
                "/api/v1/auth/totp/enroll", headers={"Authorization": f"Bearer {token}"}
            )
            secret = enroll.json()["secret"]
            code = pyotp.TOTP(secret).now()
            confirm = await c.post(
                "/api/v1/auth/totp/confirm",
                json={"secret": secret, "code": code},
                headers={"Authorization": f"Bearer {token}"},
            )
            codes = confirm.json()["backupCodes"]
            me = await c.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
            for one_code in codes:
                assert one_code not in me.text


async def test_secret_never_appears_in_any_response_after_enroll(
    app_session: AppSessionFactory,
) -> None:
    """The spec's own "Testing & Security" checklist names the secret
    alongside backup codes: enroll's own response is the ONE place it may
    appear. Structurally guaranteed today (no response model past enroll's
    own declares a secret field -- confirm, verify, status, regenerate, and
    /me each have their own fixed field list), but proven here rather than
    left to that reasoning alone, mirroring
    test_backup_codes_never_appear_in_any_response_after_confirm's own
    approach for the same property on the other secret this flow handles."""
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(
        app_session, tenant, "secret-leak-check@example.com"
    )
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            enroll = await c.post(
                "/api/v1/auth/totp/enroll", headers={"Authorization": f"Bearer {token}"}
            )
            secret = enroll.json()["secret"]
            code = pyotp.TOTP(secret).now()
            confirm = await c.post(
                "/api/v1/auth/totp/confirm",
                json={"secret": secret, "code": code},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert secret not in confirm.text

            status = await c.get(
                "/api/v1/auth/totp/status", headers={"Authorization": f"Bearer {token}"}
            )
            assert secret not in status.text

            regenerate = await c.post(
                "/api/v1/auth/totp/regenerate-backup-codes",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert secret not in regenerate.text

            me = await c.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
            assert secret not in me.text


async def test_confirm_a_second_time_is_a_clean_409_not_a_500(
    app_session: AppSessionFactory,
) -> None:
    """F2: `totp_credential.member_id` is UNIQUE, so a second /confirm for an
    already-enrolled member must not reach that constraint as the only thing
    stopping it -- a raw 500 on a benign double-click (or someone re-running
    this exact flow by hand). Also proves the pre-check doesn't let a second
    write through: the ORIGINAL secret in the vault is unchanged."""
    from oc8.secrets.service import resolve_secret

    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "double-confirm@example.com")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            enroll = await c.post(
                "/api/v1/auth/totp/enroll", headers={"Authorization": f"Bearer {token}"}
            )
            secret = enroll.json()["secret"]
            code = pyotp.TOTP(secret).now()
            first = await c.post(
                "/api/v1/auth/totp/confirm",
                json={"secret": secret, "code": code},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert first.status_code == 200, first.text

            # A second, independently-generated secret and a fresh valid code
            # for IT -- proves the pre-check fires on "already enrolled",
            # not merely on "the same secret submitted twice".
            enroll2 = await c.post(
                "/api/v1/auth/totp/enroll", headers={"Authorization": f"Bearer {token}"}
            )
            secret2 = enroll2.json()["secret"]
            code2 = pyotp.TOTP(secret2).now()
            second = await c.post(
                "/api/v1/auth/totp/confirm",
                json={"secret": secret2, "code": code2},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert second.status_code == 409, second.text

    async with app_session(tenant) as db:
        member = (
            await db.execute(
                m.OrgMember.__table__.select().where(
                    m.OrgMember.subject == "double-confirm@example.com"
                )
            )
        ).fetchone()
        rows = (
            await db.execute(
                m.TotpCredential.__table__.select().where(m.TotpCredential.member_id == member.id)
            )
        ).fetchall()
        assert len(rows) == 1  # the second call created no second row
        stored = await resolve_secret(db, tenant_id=tenant, ref=f"totp:{member.id}")
        assert stored == secret  # NOT secret2 -- the second write never happened


async def test_confirm_rejects_an_oversized_secret_with_422(
    app_session: AppSessionFactory,
) -> None:
    """F3: an unbounded `secret` field let a 100KB string reach the secret
    vault. Pydantic's `max_length` must refuse it before the handler runs."""
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(
        app_session, tenant, "oversized-secret@example.com"
    )
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            confirm = await c.post(
                "/api/v1/auth/totp/confirm",
                json={"secret": "A" * 100_000, "code": "123456"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert confirm.status_code == 422, confirm.text

    async with app_session(tenant) as db:
        rows = (await db.execute(m.TotpCredential.__table__.select())).fetchall()
        assert rows == []


async def test_confirm_rejects_an_oversized_code_with_422(
    app_session: AppSessionFactory,
) -> None:
    """F3: `code` has no legitimate reason to exceed a generous cap either."""
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "oversized-code@example.com")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            enroll = await c.post(
                "/api/v1/auth/totp/enroll", headers={"Authorization": f"Bearer {token}"}
            )
            secret = enroll.json()["secret"]
            confirm = await c.post(
                "/api/v1/auth/totp/confirm",
                json={"secret": secret, "code": "1" * 100},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert confirm.status_code == 422, confirm.text


async def _enroll_and_confirm(client: AsyncClient, token: str) -> tuple[str, list[str]]:
    enroll = await client.post(
        "/api/v1/auth/totp/enroll", headers={"Authorization": f"Bearer {token}"}
    )
    secret = enroll.json()["secret"]
    code = pyotp.TOTP(secret).now()
    confirm = await client.post(
        "/api/v1/auth/totp/confirm",
        json={"secret": secret, "code": code},
        headers={"Authorization": f"Bearer {token}"},
    )
    return secret, confirm.json()["backupCodes"]


async def test_verify_with_a_valid_totp_code_returns_a_real_session(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "verify@example.com")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            secret, _codes = await _enroll_and_confirm(c, token)
            # Mint a fresh CHALLENGE token as password_login would (Task 7
            # wires this for real; here it's minted directly to test /verify
            # in isolation).
            challenge_token = get_identity_provider().mint(
                tenant_id=tenant, subject="verify@example.com", role="org_admin",
                scopes=["totp:challenge"],
            )
            code = pyotp.TOTP(secret).now()
            r = await c.post(
                "/api/v1/auth/totp/verify",
                json={"code": code},
                headers={"Authorization": f"Bearer {challenge_token}"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert "token" in body
            # The returned token is a REAL session -- not scoped -- proven by
            # reaching a route the totp-pending guard would otherwise refuse.
            members = await c.get(
                "/api/v1/members", headers={"Authorization": f"Bearer {body['token']}"}
            )
            assert (
                members.status_code != 403
                or "totp" not in members.json().get("detail", "").lower()
            )


async def test_verify_with_a_backup_code_burns_it_single_use(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "backup@example.com")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            _secret, codes = await _enroll_and_confirm(c, token)
            challenge_token = get_identity_provider().mint(
                tenant_id=tenant, subject="backup@example.com", role="org_admin",
                scopes=["totp:challenge"],
            )
            first_use = await c.post(
                "/api/v1/auth/totp/verify",
                json={"code": codes[0]},
                headers={"Authorization": f"Bearer {challenge_token}"},
            )
            assert first_use.status_code == 200, first_use.text

            second_challenge = get_identity_provider().mint(
                tenant_id=tenant, subject="backup@example.com", role="org_admin",
                scopes=["totp:challenge"],
            )
            second_use = await c.post(
                "/api/v1/auth/totp/verify",
                json={"code": codes[0]},
                headers={"Authorization": f"Bearer {second_challenge}"},
            )
            assert second_use.status_code == 401, second_use.text


async def test_verify_with_an_invalid_code_and_invalid_backup_code_is_rejected(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "badcode@example.com")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            await _enroll_and_confirm(c, token)
            challenge_token = get_identity_provider().mint(
                tenant_id=tenant, subject="badcode@example.com", role="org_admin",
                scopes=["totp:challenge"],
            )
            r = await c.post(
                "/api/v1/auth/totp/verify",
                json={"code": "000000"},
                headers={"Authorization": f"Bearer {challenge_token}"},
            )
            assert r.status_code == 401, r.text


async def test_two_concurrent_verifies_of_one_backup_code_let_exactly_one_through(
    app_session: AppSessionFactory,
) -> None:
    """The row lock, not the sequential path, is what makes the burn one-time.

    `test_verify_with_a_backup_code_burns_it_single_use` above passes with or
    without `totp_verify`'s `SELECT ... FOR UPDATE` -- it issues its two
    requests one after the other, so the second one always reads a row the
    first one already committed. This test is the one that actually exercises
    the lock: two /verify calls, each in its OWN request transaction, race for
    the SAME backup code. Without the lock both read `used_at: None` from
    independent READ COMMITTED snapshots, both rebuild the whole JSONB list
    from that stale copy, and the second COMMIT silently overwrites the
    first's burn -- two real sessions minted from one single-use code.
    Verified by mutation: dropping `.with_for_update()` makes this fail with
    two 200s.
    """
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "race@example.com")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as setup_client:
            _secret, codes = await _enroll_and_confirm(setup_client, token)

        def _challenge() -> str:
            return get_identity_provider().mint(
                tenant_id=tenant,
                subject="race@example.com",
                role="org_admin",
                scopes=["totp:challenge"],
            )

        async def _verify(client: AsyncClient, code: str) -> int:
            r = await client.post(
                "/api/v1/auth/totp/verify",
                json={"code": code},
                headers={"Authorization": f"Bearer {_challenge()}"},
            )
            return r.status_code

        async with _client(app) as a, _client(app) as b:
            # Warm both request paths FIRST, with a code that cannot match.
            # Without this the race never actually happens: the second
            # request spends ~50ms opening its own pooled connection --
            # longer than the first request's whole critical section -- so
            # it reaches its SELECT only after the first has committed, and
            # the test passes for a reason that has nothing to do with the
            # lock. With the pool warm, both reach the SELECT inside the
            # same window -- confirmed by mutation, see the docstring.
            warm = await asyncio.gather(_verify(a, "ZZZZZZZZZZ"), _verify(b, "ZZZZZZZZZZ"))
            assert warm == [401, 401], warm
            first, second = await asyncio.gather(_verify(a, codes[0]), _verify(b, codes[0]))
    assert sorted([first, second]) == [200, 401], (first, second)


async def test_status_reports_false_before_enrollment_and_true_after(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "status@example.com")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            before = await c.get(
                "/api/v1/auth/totp/status", headers={"Authorization": f"Bearer {token}"}
            )
            assert before.status_code == 200, before.text
            assert before.json()["enrolled"] is False

            await _enroll_and_confirm(c, token)

            after = await c.get(
                "/api/v1/auth/totp/status", headers={"Authorization": f"Bearer {token}"}
            )
            assert after.json()["enrolled"] is True


async def test_regenerate_backup_codes_invalidates_the_old_set(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "regen@example.com")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            _secret, old_codes = await _enroll_and_confirm(c, token)
            regen = await c.post(
                "/api/v1/auth/totp/regenerate-backup-codes",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert regen.status_code == 200, regen.text
            new_codes = regen.json()["backupCodes"]
            assert set(new_codes).isdisjoint(set(old_codes))

            challenge_token = get_identity_provider().mint(
                tenant_id=tenant, subject="regen@example.com", role="org_admin",
                scopes=["totp:challenge"],
            )
            old_code_attempt = await c.post(
                "/api/v1/auth/totp/verify",
                json={"code": old_codes[0]},
                headers={"Authorization": f"Bearer {challenge_token}"},
            )
            assert old_code_attempt.status_code == 401, old_code_attempt.text


async def test_regenerate_without_an_enrolled_credential_404s(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    token = await _make_member_with_full_session(app_session, tenant, "never-enrolled@example.com")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.post(
                "/api/v1/auth/totp/regenerate-backup-codes",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 404, r.text
