"""Contract checks for the operator-run Community portability rehearsal."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_portability_rehearsal_uses_an_isolated_restore_and_normal_migrator() -> None:
    script = (REPO_ROOT / "scripts" / "verify_community_portability.py").read_text(
        encoding="utf-8"
    )

    assert 'LIVE_DATABASE = "oc8"' in script
    assert "oc8_restore_verify_" in script
    assert '"pg_dump"' in script
    assert '"pg_restore"' in script
    assert '"alembic",\n        "upgrade",\n        "head"' in script
    assert "DROP DATABASE {verify_database} WITH (FORCE)" in script
    assert "_SAFE_DATABASE_NAME.fullmatch(verify_database)" in script
    assert "source_counts" in script
    assert "upgraded_counts" in script
