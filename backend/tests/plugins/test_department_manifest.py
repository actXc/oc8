from __future__ import annotations

from typing import Any

import pytest

from oc8.capas.manifest import ManifestError, parse_manifest

pytestmark = pytest.mark.asyncio


def _dept_manifest() -> dict[str, Any]:
    return {
        "name": "sales-dept",
        "version": "1.0.0",
        "type": "department_template",
        "department_template": {
            "frame": {"tools": {}, "kbs": [], "memory": {}},
            "agents": [
                {"name": "Head of Sales", "role_title": "Lead", "mission": "run sales",
                 "is_team_lead": True, "persona": "# Decisive", "skills": ["crm"]},
                {"name": "Rep A", "reports_to": "Head of Sales", "persona": "# Hungry"},
            ],
        },
    }


def test_department_template_manifest_parses() -> None:
    mf = parse_manifest(_dept_manifest())
    assert mf.type == "department_template"
    assert mf.department_template is not None
    assert len(mf.department_template.agents) == 2
    lead = mf.department_template.agents[0]
    assert lead.is_team_lead is True and lead.persona == "# Decisive"
    assert mf.department_template.agents[1].reports_to == "Head of Sales"


def test_department_template_rejects_unknown_agent_key() -> None:
    data = _dept_manifest()
    data["department_template"]["agents"][0]["bogus"] = 1
    with pytest.raises(ManifestError):
        parse_manifest(data)
