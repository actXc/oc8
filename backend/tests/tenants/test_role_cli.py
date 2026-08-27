"""`oc8 role ...`, `oc8 member set-role` and `oc8 member import --csv`.

Three things live here that no HTTP test can reach, and one of them is the whole
reason the importer exists in this shape.

**ONE TRANSACTION PER ROW.** A commit inside `tenant_session` unbinds
`app.tenant_id` for every statement after it, so the two obvious shapes for a
500-row importer both fail and only one of them fails loudly:

* one transaction, one commit per row -> the GUC dies on the first commit and
  rows 2..500 match no RLS policy and affect ZERO rows, while the loop reports
  success for all of them;
* one transaction, one commit at the end -> correct, until row 500 names a
  department that does not exist, at which point 499 good rows are rolled back.

`test_a_bad_row_costs_one_row` is what tells those apart from the third shape
that is actually correct. It is deliberately built so that the failure is in the
MIDDLE: a bad row at the end would pass under the second shape too, and a bad
row at the start would pass under the first.

**The lockout repair.** `member:manage` is the permission that assigns roles, so
an administrator who ends up without it has no HTTP door back in. `--clear` is
that door, and it must work for exactly the assignment `PUT /members/{id}/role`
refuses to make.

**`--reset` must refuse to delete what a customer typed.** It TRUNCATEs every
table in the metadata, which now includes `role_permission`, and
`docker-compose.yml` runs `oc8 seed` on start when `OC8_SEED_ON_START=true`.
This is the first time that flag can lose configuration rather than demo data.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.authz.permissions import (
    APPROVAL,
    APPROVAL_DECIDE,
    CLARIFICATION_VIEW,
    SEAT_APPROVER,
    VIEW,
    perm,
)
from oc8.tenants import cli as tcli
from oc8.tenants.provision import create_tenant
from tests.tenants.conftest import OwnerSessionFactory

pytestmark = pytest.mark.asyncio

APPROVAL_VIEW = perm(APPROVAL, VIEW)
FREIGABE = [APPROVAL_VIEW, APPROVAL_DECIDE, CLARIFICATION_VIEW]


def _slug() -> str:
    return f"muster-{uuid.uuid4().hex[:10]}"


async def _tenant(owner_session: OwnerSessionFactory) -> tuple[str, uuid.UUID]:
    slug = _slug()
    async with owner_session() as db:
        created = await create_tenant(db, slug=slug, name="Muster GmbH", department_name="Vertrieb")
        return slug, created.tenant_id


async def _members(
    owner_session: OwnerSessionFactory, tenant_id: uuid.UUID
) -> dict[str, m.OrgMember]:
    async with owner_session() as db:
        rows = (
            (await db.execute(select(m.OrgMember).where(m.OrgMember.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
        return {r.subject: r for r in rows}


async def _seats(
    owner_session: OwnerSessionFactory, tenant_id: uuid.UUID
) -> list[m.OrgMemberDepartment]:
    async with owner_session() as db:
        return list(
            (
                await db.execute(
                    select(m.OrgMemberDepartment).where(
                        m.OrgMemberDepartment.tenant_id == tenant_id,
                        m.OrgMemberDepartment.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )


def _csv(tmp_path: Path, body: str, name: str = "people.csv") -> str:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


# ------------------------------------------------------------------ the importer


async def test_every_row_of_a_long_file_lands(
    owner_session: OwnerSessionFactory, tmp_path: Path
) -> None:
    """Sixty people, one file, and every one of them actually enrolled.

    Sixty rather than three, and asserted on the LAST row as well as the count:
    the failure this is written against -- a commit inside `tenant_session`
    unbinding the RLS GUC -- applies rows 2..n against no policy at all, so a
    three-row test that happened to check row one would be green with two
    people missing and the command reporting success for all three.
    """
    slug, tenant_id = await _tenant(owner_session)
    assert (
        await tcli.cmd_role_create(
            slug=slug, name="Abteilungsleitung", permissions=FREIGABE, description=""
        )
        == 0
    )

    rows = "\n".join(
        f"lead-{i:03d},Teamleitung {i:03d},Abteilungsleitung,Vertrieb,dept_approver"
        for i in range(60)
    )
    path = _csv(tmp_path, "subject,display_name,role,department,seat_role\n" + rows + "\n")

    assert await tcli.cmd_member_import(slug=slug, path=path) == 0

    people = await _members(owner_session, tenant_id)
    imported = {s: p for s, p in people.items() if s.startswith("lead-")}
    assert len(imported) == 60, f"only {len(imported)} of 60 rows landed"
    assert all(p.role_id is not None for p in imported.values()), (
        "somebody was enrolled without the role their row named"
    )
    assert imported["lead-059"].display_name == "Teamleitung 059", (
        "the LAST row is the one a dead RLS GUC loses first"
    )
    assert len(await _seats(owner_session, tenant_id)) == 60


async def test_a_bad_row_costs_one_row(owner_session: OwnerSessionFactory, tmp_path: Path) -> None:
    """A row naming a role that does not exist loses THAT row and nothing else.

    The bad row is in the MIDDLE on purpose. At the end it would also pass under
    "one transaction, commit once at the end" -- because nothing after it would
    have been rolled back; at the start it would pass under "commit per row
    inside one session", because the GUC is still bound for row one. Only the
    third shape -- a fresh transaction per row -- keeps the rows on BOTH sides of
    it, and that is what is asserted.

    And the failing row must land NEITHER HALF of itself: `anna` names a good
    role and a department that does not exist, so a per-STATEMENT commit would
    leave her holding the role with no seat.
    """
    slug, tenant_id = await _tenant(owner_session)
    assert (
        await tcli.cmd_role_create(slug=slug, name="Freigabe", permissions=FREIGABE, description="")
        == 0
    )

    path = _csv(
        tmp_path,
        "subject,display_name,role,department,seat_role\n"
        "vorher,Vorher,Freigabe,Vertrieb,dept_approver\n"
        "anna,Anna,Freigabe,Gibt Es Nicht,dept_approver\n"
        "nachher,Nachher,Freigabe,Vertrieb,dept_viewer\n",
    )

    # Non-zero, because a partially applied import that reports success is how an
    # administrator walks away believing 500 people were seated.
    assert await tcli.cmd_member_import(slug=slug, path=path) == 2

    people = await _members(owner_session, tenant_id)
    assert "vorher" in people, "the row BEFORE the failure was rolled back with it"
    assert "nachher" in people, "the loop stopped at the first bad row"
    assert people["vorher"].role_id is not None and people["nachher"].role_id is not None
    assert "anna" not in people, (
        "the failing row landed its first half: she was enrolled and given a role, "
        "and only the seat was refused"
    )
    assert len(await _seats(owner_session, tenant_id)) == 2


async def test_a_misspelled_column_is_refused_before_anything_is_written(
    owner_session: OwnerSessionFactory, tmp_path: Path
) -> None:
    """Read and validated in FULL first.

    A heading typo found on line one is a spelling mistake. The same typo found
    after 300 rows have landed is a tenant somebody has to unpick by hand, and
    the CSV is the bridge precisely because there is no seat-administration
    screen to unpick it with.
    """
    slug, tenant_id = await _tenant(owner_session)
    path = _csv(
        tmp_path,
        "subject,display_name,rolle,department\ndieter,Dieter,Freigabe,Vertrieb\n",
    )
    assert await tcli.cmd_member_import(slug=slug, path=path) == 2
    assert "dieter" not in await _members(owner_session, tenant_id)


async def test_a_row_with_no_subject_is_refused_before_anything_is_written(
    owner_session: OwnerSessionFactory, tmp_path: Path
) -> None:
    """The subject is the token `sub`, and it is the only column that identifies
    anybody. A blank one would upsert a member row keyed on the empty string --
    once, globally, for the tenant -- which nobody could ever sign in as and which
    would then own whichever role the row named."""
    slug, tenant_id = await _tenant(owner_session)
    path = _csv(
        tmp_path,
        "subject,display_name,department\ngut,Gut,Vertrieb\n,Ohne Subject,Vertrieb\n",
    )
    assert await tcli.cmd_member_import(slug=slug, path=path) == 2
    people = await _members(owner_session, tenant_id)
    assert "gut" not in people, "a file with a bad line wrote its good lines anyway"
    assert "" not in people


async def test_the_importer_is_re_runnable(
    owner_session: OwnerSessionFactory, tmp_path: Path
) -> None:
    """Running the same file twice must not make two of anybody.

    A reorganisation is driven by editing a spreadsheet and running it again, so
    "did this already run" is a question nobody should have to answer. Both
    writes are upserts: the member on `(tenant_id, subject)`, the seat on the
    partial unique index that makes a live seat unique per department.
    """
    slug, tenant_id = await _tenant(owner_session)
    assert (
        await tcli.cmd_role_create(slug=slug, name="Freigabe", permissions=FREIGABE, description="")
        == 0
    )
    path = _csv(
        tmp_path,
        "subject,display_name,role,department,seat_role\nanna,Anna,Freigabe,Vertrieb,dept_viewer\n",
    )

    assert await tcli.cmd_member_import(slug=slug, path=path) == 0
    assert await tcli.cmd_member_import(slug=slug, path=path) == 0

    people = await _members(owner_session, tenant_id)
    assert len([s for s in people if s == "anna"]) == 1
    seats = await _seats(owner_session, tenant_id)
    assert len(seats) == 1, f"a re-run produced {len(seats)} seats for one person"


async def test_a_promotion_is_a_second_run_of_the_same_file(
    owner_session: OwnerSessionFactory, tmp_path: Path
) -> None:
    """Anna's seat is upgraded to `dept_approver` by changing one cell.

    The reorganisation story depends on this: 60 seat writes against two new
    departments, driven from a file, with ZERO role edits -- because the
    department is on the seat and not in the role.
    """
    slug, tenant_id = await _tenant(owner_session)
    header = "subject,display_name,role,department,seat_role\n"
    assert (
        await tcli.cmd_member_import(
            slug=slug, path=_csv(tmp_path, header + "anna,Anna,,Vertrieb,dept_viewer\n", "a.csv")
        )
        == 0
    )
    assert (
        await tcli.cmd_member_import(
            slug=slug, path=_csv(tmp_path, header + "anna,Anna,,Vertrieb,dept_approver\n", "b.csv")
        )
        == 0
    )
    seats = await _seats(owner_session, tenant_id)
    assert len(seats) == 1
    assert seats[0].seat_role == SEAT_APPROVER


# ----------------------------------------------------------------- set-role


async def test_set_role_and_clear_are_the_lockout_repair(
    owner_session: OwnerSessionFactory,
) -> None:
    """The command that exists because HTTP cannot always be used.

    `PUT /members/{id}/role` refuses to let a caller assign HIMSELF a role
    without `member:manage` -- and `member:manage` is `NEVER_DELEGATABLE`, so
    that is every tenant role there can be. This runs out of band, so it must
    make that assignment (an administrator may narrow somebody else the endpoint
    would also narrow) and, crucially, must be able to take it back.
    """
    slug, tenant_id = await _tenant(owner_session)
    assert (
        await tcli.cmd_role_create(
            slug=slug, name="Praktikant", permissions=[APPROVAL_VIEW], description="Sieht nur zu"
        )
        == 0
    )
    assert (
        await tcli.cmd_member_grant(
            slug=slug,
            subject="anna",
            department="Vertrieb",
            role="dept_viewer",
            display_name="Anna",
        )
        == 0
    )

    assert await tcli.cmd_member_set_role(slug=slug, subject="anna", role="Praktikant") == 0
    assert (await _members(owner_session, tenant_id))["anna"].role_id is not None

    assert await tcli.cmd_member_set_role(slug=slug, subject="anna", role=None) == 0
    assert (await _members(owner_session, tenant_id))["anna"].role_id is None, (
        "--clear did not restore the token floor, so a locked-out administrator has no repair left"
    )


async def test_set_role_refuses_a_name_this_tenant_does_not_have(
    owner_session: OwnerSessionFactory,
) -> None:
    """Refused rather than silently doing nothing.

    A CLI that answers 0 for a role name nobody defined is a CLI an administrator
    trusts, and the person he thinks he narrowed is still an `org_admin`.
    """
    slug, tenant_id = await _tenant(owner_session)
    assert (
        await tcli.cmd_member_grant(
            slug=slug, subject="anna", department="Vertrieb", role="dept_viewer"
        )
        == 0
    )
    assert await tcli.cmd_member_set_role(slug=slug, subject="anna", role="Gibt Es Nicht") == 2
    assert (await _members(owner_session, tenant_id))["anna"].role_id is None


async def test_set_role_refuses_an_agent_kind_role(
    owner_session: OwnerSessionFactory,
) -> None:
    """`agent_default` is a row in the same table describing a different
    population, and provisioning writes one for every tenant.

    A person pointed at it resolves to the EMPTY SET -- a total, silent lockout
    that looks on every screen like a role which simply grants nothing. The
    refusal is on `kind`, which is the same column the PDP reads in the other
    direction.
    """
    slug, tenant_id = await _tenant(owner_session)
    assert (
        await tcli.cmd_member_grant(
            slug=slug, subject="anna", department="Vertrieb", role="dept_viewer"
        )
        == 0
    )
    assert await tcli.cmd_member_set_role(slug=slug, subject="anna", role="agent_default") == 2
    assert (await _members(owner_session, tenant_id))["anna"].role_id is None


# --------------------------------------------------------------------- roles


async def test_role_create_refuses_what_the_endpoint_refuses(
    owner_session: OwnerSessionFactory,
) -> None:
    """One validator, two front doors.

    The CLI is not a second implementation with its own idea of what may be
    granted: it calls `roles.service`, so a permission the form refuses is a
    permission the shell refuses, and a name the form refuses is refused here
    too. The alternative is an escalation path that consists of using the other
    client.
    """
    slug, tenant_id = await _tenant(owner_session)
    assert (
        await tcli.cmd_role_create(
            slug=slug, name="Zu viel", permissions=[APPROVAL_VIEW, "plugin:manage"], description=""
        )
        == 2
    )
    assert (
        await tcli.cmd_role_create(
            slug=slug, name="operator", permissions=[APPROVAL_VIEW], description=""
        )
        == 2
    )
    assert (
        await tcli.cmd_role_create(slug=slug, name="agent_default", permissions=[], description="")
        == 2
    )

    async with owner_session() as db:
        rows = (
            (await db.execute(select(m.Role).where(m.Role.tenant_id == tenant_id, ~m.Role.builtin)))
            .scalars()
            .all()
        )
    assert not rows, f"a refused create wrote a row anyway: {[r.name for r in rows]}"


async def test_role_delete_refuses_while_it_is_held_and_reassigns_on_request(
    owner_session: OwnerSessionFactory,
) -> None:
    """Deleting a held role would restore every holder to their TOKEN, which on
    every live tenant is `org_admin`. So it is refused, and `--reassign-to` is
    the way through -- with the holders MOVED rather than left pointing at a row
    that is gone."""
    slug, tenant_id = await _tenant(owner_session)
    for name in ("Alt", "Neu"):
        assert (
            await tcli.cmd_role_create(
                slug=slug, name=name, permissions=[APPROVAL_VIEW], description=""
            )
            == 0
        )
    assert (
        await tcli.cmd_member_grant(
            slug=slug, subject="anna", department="Vertrieb", role="dept_viewer"
        )
        == 0
    )
    assert await tcli.cmd_member_set_role(slug=slug, subject="anna", role="Alt") == 0

    assert await tcli.cmd_role_delete(slug=slug, name="Alt", reassign_to=None) == 2
    held = (await _members(owner_session, tenant_id))["anna"].role_id
    assert held is not None, "a refused delete demoted the holder anyway"

    assert await tcli.cmd_role_delete(slug=slug, name="Alt", reassign_to="Neu") == 0
    moved = (await _members(owner_session, tenant_id))["anna"].role_id
    assert moved is not None and moved != held, "the holder was not moved"


async def test_a_deleted_name_stays_taken(owner_session: OwnerSessionFactory) -> None:
    """The tombstone, asserted rather than assumed.

    `uq_role_tenant_name` is unconditional on purpose: renaming is refused, so
    delete-and-recreate is the sanctioned way to change a name, and a freed name
    would hand every mention of the old role in the audit trail to a new one that
    never earned them.
    """
    slug, _tenant_id = await _tenant(owner_session)
    assert (
        await tcli.cmd_role_create(
            slug=slug, name="Freigabe", permissions=[APPROVAL_VIEW], description=""
        )
        == 0
    )
    assert await tcli.cmd_role_delete(slug=slug, name="Freigabe", reassign_to=None) == 0
    assert (
        await tcli.cmd_role_create(slug=slug, name="freigabe", permissions=[], description="") == 2
    ), "a deleted role's name came back, case-insensitively"


async def test_role_list_shows_the_builtin_ladder_on_a_real_tenant(
    owner_session: OwnerSessionFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ladder comes from CODE, so it is the same on every tenant.

    Provisioning writes five `role` rows and Globex has one, and nothing has ever
    backfilled the difference -- a list built from rows would show a shorter
    ladder on the older tenant for a set of roles that is identical in both.
    `agent_default` is absent because it describes a container, not a person.
    """
    slug, _tenant_id = await _tenant(owner_session)
    assert await tcli.cmd_role_list(slug=slug) == 0
    printed = capsys.readouterr().out
    for name in ("org_admin", "dept_manager", "operator", "auditor", "member"):
        assert name in printed, f"{name} is missing from the ladder"
    assert "agent_default" not in printed, (
        "the agent's own role is offered in a list of roles for people"
    )
