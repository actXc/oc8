"""`GET /auth/dev-members` — the sign-in screen's persona picker.

It has to work with no token at all (nobody is signed in yet, that is the whole
point) and it has to be dev-only, the same as `dev-login` and `dev-tenants`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.authz.permissions import SEAT_APPROVER
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _subject_uuid(subject: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"oc8:subject:{subject}")


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def _seed_hos(app_session: AppSessionFactory, tenant: uuid.UUID) -> None:
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(sales)
        await db.flush()
        member = m.OrgMember(
            tenant_id=tenant,
            subject="hos-vertrieb",
            subject_uuid=_subject_uuid("hos-vertrieb"),
            display_name="Head of Sales",
        )
        db.add(member)
        await db.flush()
        db.add(
            m.OrgMemberDepartment(
                tenant_id=tenant,
                member_id=member.id,
                department_id=sales.id,
                seat_role=SEAT_APPROVER,
            )
        )


async def test_a_seated_person_is_listed_with_no_token_at_all(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    await _seed_hos(app_session, tenant)

    async with _http() as http:
        got = await http.get("/api/v1/auth/dev-members", params={"tenant_id": str(tenant)})
    assert got.status_code == 200, got.text

    body = got.json()
    assert len(body) == 1
    person = body[0]
    assert person["subject"] == "hos-vertrieb"
    assert person["displayName"] == "Head of Sales"
    assert len(person["seats"]) == 1
    assert person["seats"][0]["seatRole"] == SEAT_APPROVER
    assert person["seats"][0]["departmentName"] == "Vertrieb"


async def test_two_tenants_do_not_see_each_others_people(
    app_session: AppSessionFactory,
) -> None:
    seen_tenant = uuid.uuid4()
    other_tenant = uuid.uuid4()
    await _seed_hos(app_session, seen_tenant)

    async with _http() as http:
        empty = await http.get("/api/v1/auth/dev-members", params={"tenant_id": str(other_tenant)})
    assert empty.status_code == 200, empty.text
    assert empty.json() == []


async def test_a_freshly_seeded_tenant_answers_empty_not_an_error(
    app_session: AppSessionFactory,
) -> None:
    """The two-person company that has configured nothing yet: nobody has
    signed in before, so the picker's "people" section is legitimately empty
    and the quick-access built-in roles are the only way in. That must be a
    200 with `[]`, not a 404 or a 500."""
    async with _http() as http:
        got = await http.get("/api/v1/auth/dev-members", params={"tenant_id": str(uuid.uuid4())})
    assert got.status_code == 200, got.text
    assert got.json() == []
