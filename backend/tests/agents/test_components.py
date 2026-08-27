from __future__ import annotations

import pytest
from pydantic import ValidationError

from oc8.agent.components import COMPONENT_CATALOG, RecordCardProps


def test_record_card_is_in_the_catalog() -> None:
    assert COMPONENT_CATALOG["record_card"] is RecordCardProps


def test_record_card_accepts_its_documented_shape() -> None:
    props = RecordCardProps.model_validate(
        {
            "title": "Acme GmbH — 12.400 €",
            "subtitle": "Verhandlung",
            "fields": [{"label": "Abschluss erwartet", "value": "2026-09-01"}],
            "link_label": "In CRM öffnen",
            "link_url": "https://crm.example.com/deals/42",
        }
    )
    assert props.title == "Acme GmbH — 12.400 €"
    assert props.fields[0].label == "Abschluss erwartet"


def test_record_card_rejects_an_unknown_field() -> None:
    """extra='forbid': a model that starts inventing new prop keys must be
    told, not silently ignored -- an ignored key could carry a value nobody
    ever sees, which is worse than an outright error."""
    with pytest.raises(ValidationError):
        RecordCardProps.model_validate({"title": "x", "unexpected": "y"})


def test_record_card_requires_a_title() -> None:
    with pytest.raises(ValidationError):
        RecordCardProps.model_validate({"fields": []})
