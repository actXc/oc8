from __future__ import annotations

import uuid

import pytest

from oc8 import models as m
from oc8.collab.flow_engine import advance_flow_run, condition_ok, start_flow_run
from oc8.collab.flow_spec import FlowSpecError, parse_flow_spec
from oc8.collab.handoff import create_handoff_type
from oc8.constants import ACME_TENANT_ID
from tests.conftest import AppSessionFactory


def _spec_dict(from_dept: uuid.UUID, to_dept: uuid.UUID) -> dict[str, object]:
    return {
        "id": "q2c",
        "version": "1.0.0",
        "trigger": {"event": "sales.deal.won", "from_department_id": str(from_dept)},
        "stages": [
            {
                "id": "kickoff",
                "handoff": {
                    "type": "flow.kickoff",
                    "to_department_id": str(to_dept),
                    "payload_map": {"customer": "$.customer"},
                },
            },
            {
                "id": "build",
                "after": "kickoff.completed",
                "handoff": {"type": "flow.build", "to_department_id": str(to_dept)},
            },
        ],
    }


def test_condition_evaluator() -> None:
    assert condition_ok(None, {}) is True
    assert condition_ok("$.budget > 0", {"budget": 5}) is True
    assert condition_ok("$.budget > 0", {"budget": 0}) is False
    assert condition_ok("$.stage == 'won'", {"stage": "won"}) is True


def test_parse_rejects_bad_spec() -> None:
    with pytest.raises(FlowSpecError):
        parse_flow_spec({"id": "x"})  # missing version/trigger/stages


async def test_flow_runs_stages_in_order(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    from_dept, to_dept = uuid.uuid4(), uuid.uuid4()
    async with app_session(tenant) as db:
        for name in ("flow.kickoff", "flow.build"):
            await create_handoff_type(
                db, tenant_id=tenant, name=name, payload_schema={"type": "object"}
            )
        ver = m.FlowVersion(
            tenant_id=tenant,
            flow_id=uuid.uuid4(),
            semver="1.0.0",
            spec={},
            artifact_hash=b"\x00" * 32,
        )
        db.add(ver)
        await db.flush()

        spec = parse_flow_spec(_spec_dict(from_dept, to_dept))
        run = await start_flow_run(
            db, tenant_id=tenant, flow_version=ver, spec=spec, context={"customer": "Acme"}
        )
        assert run.status == "running"
        assert run.current_stages == ["kickoff"]

        # the kickoff handoff was created, mapped, linked to the run
        handoffs = (await db.execute(_select_handoffs(run.id))).scalars().all()
        assert len(handoffs) == 1 and handoffs[0].payload == {"customer": "Acme"}

        await advance_flow_run(db, run=run, spec=spec, completed_stage_id="kickoff")
        assert run.current_stages == ["build"] and run.status == "running"

        await advance_flow_run(db, run=run, spec=spec, completed_stage_id="build")
        assert run.current_stages == [] and run.status == "completed"


def _select_handoffs(flow_run_id: uuid.UUID):  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    return select(m.Handoff).where(m.Handoff.flow_run_id == flow_run_id)
