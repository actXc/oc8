"""`backup:export`/`backup:restore` exist, are never delegatable, and carry
prose in both languages -- the same discipline every other permission in the
catalogue is held to (see `test_the_catalogue_is_partitioned.py` and
`test_the_catalogue_is_readable.py`)."""

from __future__ import annotations

from oc8.authz.catalog import describe
from oc8.authz.permissions import ALL_PERMISSIONS, NEVER_DELEGATABLE, delegation_refusal


def test_both_permissions_exist_and_are_never_delegatable() -> None:
    assert "backup:export" in ALL_PERMISSIONS
    assert "backup:restore" in ALL_PERMISSIONS
    assert "backup:export" in NEVER_DELEGATABLE
    assert "backup:restore" in NEVER_DELEGATABLE
    assert delegation_refusal("backup:export") is not None
    assert delegation_refusal("backup:restore") is not None


def test_both_permissions_are_described_in_both_languages() -> None:
    for permission in ("backup:export", "backup:restore"):
        info = describe(permission)
        assert info.label and info.label_en
        assert info.description and info.description_en
        assert info.delegatable is False and info.reason
