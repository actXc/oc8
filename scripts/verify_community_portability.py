#!/usr/bin/env python3
"""Rehearse a Community PostgreSQL dump, restore, and migration upgrade safely.

This command talks only to the ``postgres`` service of an already-running
Community Compose stack.  It never writes to the live ``oc8`` database.  The
restored copy is dropped in a ``finally`` block unless ``--keep-verify-db`` is
given for diagnosis.

Usage:
    python3 scripts/verify_community_portability.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TextIO

LIVE_DATABASE = "oc8"
POSTGRES_SERVICE = "postgres"
_SAFE_DATABASE_NAME = re.compile(r"^oc8_restore_verify_[0-9]{8}_[0-9]{6}$")


def _compose(*args: str, stdout: TextIO | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        check=True,
        text=stdout is None,
        stdout=stdout,
    )


def _psql(database: str, statement: str) -> str:
    result = _compose(
        "exec",
        "-T",
        POSTGRES_SERVICE,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "postgres",
        "-d",
        database,
        "-At",
        "-c",
        statement,
    )
    return result.stdout.strip()


def _database_name() -> str:
    return "oc8_restore_verify_" + dt.datetime.now(tz=dt.UTC).strftime("%Y%m%d_%H%M%S")


def _require_running_postgres() -> None:
    status = _compose("ps", "--status", "running", "--services").stdout.splitlines()
    if POSTGRES_SERVICE not in status:
        raise RuntimeError(
            "The Community postgres service is not running. Start the normal Compose stack first."
        )


def _capture_counts(database: str) -> dict[str, int]:
    """Capture durable, universally expected Community records for comparison."""
    queries = {
        "organizations": "SELECT count(*) FROM organization",
        "members": "SELECT count(*) FROM org_member",
        "agents": "SELECT count(*) FROM agent",
        "audit_events": "SELECT count(*) FROM audit_event",
    }
    return {name: int(_psql(database, query)) for name, query in queries.items()}


def _capture_revision(database: str) -> str:
    revision = _psql(database, "SELECT version_num FROM alembic_version")
    if not revision:
        raise RuntimeError(f"{database} has no Community alembic_version row")
    return revision


def _run_upgrade_on_copy(database: str) -> None:
    """Run the normal Community migrator against the isolated restored copy."""
    migration_url = f"postgresql+psycopg://oc8_migrate:oc8@postgres:5432/{database}"
    _compose(
        "run",
        "--rm",
        "--no-deps",
        "-e",
        f"OC8_MIGRATION_URL={migration_url}",
        "migrate",
        "alembic",
        "upgrade",
        "head",
    )


def verify(*, keep_verify_db: bool) -> str:
    _require_running_postgres()
    verify_database = _database_name()
    if not _SAFE_DATABASE_NAME.fullmatch(verify_database):  # Defensive guard for future edits.
        raise RuntimeError("Refusing to operate on an unexpected verification database name")

    with tempfile.TemporaryDirectory(prefix="oc8-community-portability-") as directory:
        dump_path = Path(directory) / "community.dump"
        with dump_path.open("wb") as dump_file:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    POSTGRES_SERVICE,
                    "pg_dump",
                    "-U",
                    "postgres",
                    "-d",
                    LIVE_DATABASE,
                    "--format=custom",
                ],
                check=True,
                stdout=dump_file,
            )
        if dump_path.stat().st_size == 0:
            raise RuntimeError("pg_dump produced an empty backup")

        source_counts = _capture_counts(LIVE_DATABASE)
        source_revision = _capture_revision(LIVE_DATABASE)
        created = False
        try:
            _psql("postgres", f"CREATE DATABASE {verify_database}")
            created = True
            with dump_path.open("rb") as dump_file:
                subprocess.run(
                    [
                        "docker",
                        "compose",
                        "exec",
                        "-T",
                        POSTGRES_SERVICE,
                        "pg_restore",
                        "-U",
                        "postgres",
                        "-d",
                        verify_database,
                        "--exit-on-error",
                    ],
                    check=True,
                    stdin=dump_file,
                )

            restored_counts = _capture_counts(verify_database)
            if restored_counts != source_counts:
                raise RuntimeError(
                    "Restore sentinel counts differ: "
                    f"source={source_counts}, restored={restored_counts}"
                )
            if _capture_revision(verify_database) != source_revision:
                raise RuntimeError("Restore changed the Community Alembic revision before upgrade")

            _run_upgrade_on_copy(verify_database)
            upgraded_counts = _capture_counts(verify_database)
            if upgraded_counts != restored_counts:
                raise RuntimeError(
                    "Community migration upgrade changed portability sentinel counts: "
                    f"before={restored_counts}, after={upgraded_counts}"
                )
            upgraded_revision = _capture_revision(verify_database)
        finally:
            if created and not keep_verify_db:
                _psql("postgres", f"DROP DATABASE {verify_database} WITH (FORCE)")

    print("Community portability rehearsal passed")
    print(f"  source revision: {source_revision}")
    print(f"  upgraded revision: {upgraded_revision}")
    print(f"  sentinel counts: {source_counts}")
    if keep_verify_db:
        print(f"  retained verification database: {verify_database}")
    return upgraded_revision


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-verify-db",
        action="store_true",
        help="Keep the temporary restored database for manual diagnosis.",
    )
    args = parser.parse_args()
    try:
        verify(keep_verify_db=args.keep_verify_db)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Community portability rehearsal failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
