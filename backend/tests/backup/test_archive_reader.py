"""Tests for the archive reader and its validation (design doc §3.1, §8)."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.backup.archive import export_archive
from oc8.backup.errors import BackupError
from oc8.backup.reader import parse_archive
from tests.conftest import AppSessionFactory

# NOTE: no module-level `pytestmark = pytest.mark.asyncio` here, unlike
# `test_archive_writer.py` -- this suite mixes async (DB-backed) tests with
# plain sync ones for the structural-corruption and security cases that
# never touch the database. `asyncio_mode = auto` (pyproject.toml) already
# detects the async ones without it.


@pytest.fixture
async def acme_tenant(app_session: AppSessionFactory) -> uuid.UUID:
    """A fresh tenant per test, NOT the shared `ACME_TENANT_ID` constant (see
    `test_archive_writer.py`'s identical fixture -- that constant is reused,
    uncommitted-and-never-rolled-back, across the whole suite)."""
    tenant_id = uuid.uuid4()
    async with app_session(tenant_id) as db:
        db.add(
            m.Organization(id=tenant_id, slug=f"acme-{tenant_id.hex[:8]}", name="ACME", region="eu")
        )
        await db.flush()
        dept = m.Department(tenant_id=tenant_id, name="Vertrieb", frame={})
        db.add(dept)
        await db.flush()
        db.add(
            m.Agent(
                tenant_id=tenant_id,
                department_id=dept.id,
                name="Nora",
                narrowing={},
                definition={},
                presentation={},
            )
        )
    return tenant_id


@pytest.fixture
async def db_session(
    app_session: AppSessionFactory, acme_tenant: uuid.UUID
) -> AsyncIterator[AsyncSession]:
    async with app_session(acme_tenant) as session:
        yield session


def _open_tar(data: bytes) -> tarfile.TarFile:
    return tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")


def _extract(tar: tarfile.TarFile, member: str) -> bytes:
    """`TarFile.extractfile` is typed `IO[bytes] | None`; every member these
    tests ask for was just written into the same archive -- `None` would
    mean the test's own setup is broken, so fail loudly rather than let
    mypy's `union-attr` complaint turn into a runtime `AttributeError`."""
    fh = tar.extractfile(member)
    assert fh is not None, f"missing archive member: {member}"
    return fh.read()


def _read_manifest(data: bytes) -> dict[str, Any]:
    with _open_tar(data) as tar:
        return json.loads(_extract(tar, "manifest.json"))  # type: ignore[no-any-return]


def _repack(
    manifest: dict[str, Any], tables: dict[str, bytes], secrets: bytes | None = None
) -> bytes:
    """Build a fresh archive from a (possibly tampered) manifest and table
    blobs -- lets a test corrupt exactly one thing while keeping the rest of
    the archive well-formed, gzip included."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:

        def add(name: str, blob: bytes) -> None:
            info = tarfile.TarInfo(name=name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))

        add("manifest.json", json.dumps(manifest).encode())
        for name, blob in tables.items():
            add(f"tables/{name}.ndjson", blob)
        if secrets is not None:
            add("secrets.json", secrets)
    return buf.getvalue()


async def test_valid_archive_parses_with_no_problems(
    db_session: AsyncSession, acme_tenant: uuid.UUID
) -> None:
    _, data = await export_archive(db_session, tenant_id=acme_tenant, passphrase=None)
    manifest = _read_manifest(data)
    parsed, problems = parse_archive(
        data, current_revision=manifest["alembic_revision"], strict=True
    )
    assert problems == []
    assert parsed.manifest["alembic_revision"] == manifest["alembic_revision"]
    assert parsed.tables["agent"][0]["tenant_id"] == str(acme_tenant)
    assert parsed.secrets_ciphertext is None


async def test_revision_mismatch_is_reported_not_raised_in_preview_mode(
    db_session: AsyncSession, acme_tenant: uuid.UUID
) -> None:
    _, data = await export_archive(db_session, tenant_id=acme_tenant, passphrase=None)
    manifest = _read_manifest(data)
    parsed, problems = parse_archive(data, current_revision="0000_bogus", strict=False)
    assert any("0000_bogus" in p and manifest["alembic_revision"] in p for p in problems)
    # Preview mode still hands back the parsed tables -- the UI needs the
    # row counts even though the archive is not restorable as-is.
    assert parsed.tables


async def test_revision_mismatch_raises_in_strict_mode(
    db_session: AsyncSession, acme_tenant: uuid.UUID
) -> None:
    _, data = await export_archive(db_session, tenant_id=acme_tenant, passphrase=None)
    with pytest.raises(BackupError, match="0000_bogus"):
        parse_archive(data, current_revision="0000_bogus", strict=True)


async def test_checksum_mismatch_is_detected_bitflip(
    db_session: AsyncSession, acme_tenant: uuid.UUID
) -> None:
    _, data = await export_archive(db_session, tenant_id=acme_tenant, passphrase=None)
    corrupted = bytearray(data)
    corrupted[-5] ^= 0xFF  # flip a byte near the end of the gzip stream
    with pytest.raises(BackupError):
        parse_archive(bytes(corrupted), current_revision="doesn't matter", strict=True)


async def test_checksum_mismatch_is_detected_targeted(
    db_session: AsyncSession, acme_tenant: uuid.UUID
) -> None:
    """A well-formed archive whose `agent` table content was swapped out
    without updating the manifest's recorded `sha256` -- the checksum gate,
    isolated from any gzip-level corruption."""
    _, data = await export_archive(db_session, tenant_id=acme_tenant, passphrase=None)
    manifest = _read_manifest(data)
    with _open_tar(data) as tar:
        table_blobs = {name: _extract(tar, f"tables/{name}.ndjson") for name in manifest["tables"]}
    table_blobs["agent"] = b'{"tampered": true}\n'
    tampered = _repack(manifest, table_blobs)

    with pytest.raises(BackupError, match="'agent'"):
        parse_archive(tampered, current_revision=manifest["alembic_revision"], strict=True)

    _, problems = parse_archive(
        tampered, current_revision=manifest["alembic_revision"], strict=False
    )
    assert any("checksum mismatch" in p and "'agent'" in p for p in problems)


async def test_unknown_table_is_refused_naming_it(
    db_session: AsyncSession, acme_tenant: uuid.UUID
) -> None:
    _, data = await export_archive(db_session, tenant_id=acme_tenant, passphrase=None)
    manifest = _read_manifest(data)
    with _open_tar(data) as tar:
        table_blobs = {name: _extract(tar, f"tables/{name}.ndjson") for name in manifest["tables"]}
    blob = b'{"totally_made_up": true}\n'
    manifest["tables"]["totally_bogus_table"] = {
        "rows": 1,
        "sha256": hashlib.sha256(blob).hexdigest(),
    }
    table_blobs["totally_bogus_table"] = blob
    newer_archive = _repack(manifest, table_blobs)

    with pytest.raises(BackupError, match="totally_bogus_table"):
        parse_archive(newer_archive, current_revision=manifest["alembic_revision"], strict=True)

    _, problems = parse_archive(
        newer_archive, current_revision=manifest["alembic_revision"], strict=False
    )
    assert any("totally_bogus_table" in p for p in problems)


async def test_missing_table_member_is_refused_naming_it(
    db_session: AsyncSession, acme_tenant: uuid.UUID
) -> None:
    _, data = await export_archive(db_session, tenant_id=acme_tenant, passphrase=None)
    manifest = _read_manifest(data)
    with _open_tar(data) as tar:
        table_blobs = {name: _extract(tar, f"tables/{name}.ndjson") for name in manifest["tables"]}
    del table_blobs["agent"]  # manifest still claims it, but drop the member itself
    truncated = _repack(manifest, table_blobs)

    with pytest.raises(BackupError, match=r"agent\.ndjson"):
        parse_archive(truncated, current_revision=manifest["alembic_revision"], strict=True)

    _, problems = parse_archive(
        truncated, current_revision=manifest["alembic_revision"], strict=False
    )
    assert any("agent.ndjson" in p for p in problems)


async def test_malformed_ndjson_is_refused_naming_the_table(
    db_session: AsyncSession, acme_tenant: uuid.UUID
) -> None:
    """Content that is not valid JSON per line, with the manifest's `sha256`
    recomputed to match it -- so the checksum gate alone would let it
    through, and the NDJSON parser itself must catch it."""
    _, data = await export_archive(db_session, tenant_id=acme_tenant, passphrase=None)
    manifest = _read_manifest(data)
    with _open_tar(data) as tar:
        table_blobs = {name: _extract(tar, f"tables/{name}.ndjson") for name in manifest["tables"]}
    garbage = b"not-json-at-all\n"
    table_blobs["agent"] = garbage
    manifest["tables"]["agent"]["sha256"] = hashlib.sha256(garbage).hexdigest()
    tampered = _repack(manifest, table_blobs)

    with pytest.raises(BackupError, match="'agent'"):
        parse_archive(tampered, current_revision=manifest["alembic_revision"], strict=True)

    _, problems = parse_archive(
        tampered, current_revision=manifest["alembic_revision"], strict=False
    )
    assert any("'agent'" in p for p in problems)


def test_malformed_gzip_raises() -> None:
    with pytest.raises(BackupError):
        parse_archive(b"not a tarball", current_revision="0055", strict=True)


def test_malformed_manifest_json_raises() -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        blob = b"{not valid json"
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(blob)
        tar.addfile(info, io.BytesIO(blob))
    with pytest.raises(BackupError):
        parse_archive(buf.getvalue(), current_revision="0055", strict=True)


def test_missing_manifest_raises() -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        blob = b"{}\n"
        info = tarfile.TarInfo(name="tables/agent.ndjson")
        info.size = len(blob)
        tar.addfile(info, io.BytesIO(blob))
    with pytest.raises(BackupError):
        parse_archive(buf.getvalue(), current_revision="0055", strict=True)


def test_format_version_mismatch_always_raises() -> None:
    manifest = {"format_version": 999, "alembic_revision": "0055", "tables": {}}
    archive = _repack(manifest, {})
    with pytest.raises(BackupError, match="999"):
        parse_archive(archive, current_revision="0055", strict=True)
    with pytest.raises(BackupError):
        # Also unconditional in preview mode: there is nothing safe to
        # report about a shape this reader does not understand.
        parse_archive(archive, current_revision="0055", strict=False)


def test_path_traversal_member_is_refused() -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_blob = json.dumps(
            {"format_version": 1, "alembic_revision": "0055", "tables": {}}
        ).encode()
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_blob)
        tar.addfile(info, io.BytesIO(manifest_blob))

        evil_blob = b"malicious"
        evil = tarfile.TarInfo(name="../../etc/passwd")
        evil.size = len(evil_blob)
        tar.addfile(evil, io.BytesIO(evil_blob))
    with pytest.raises(BackupError, match=r"\.\."):
        parse_archive(buf.getvalue(), current_revision="0055", strict=True)


def test_absolute_path_member_is_refused() -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_blob = json.dumps(
            {"format_version": 1, "alembic_revision": "0055", "tables": {}}
        ).encode()
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_blob)
        tar.addfile(info, io.BytesIO(manifest_blob))

        evil_blob = b"malicious"
        evil = tarfile.TarInfo(name="/etc/passwd")
        evil.size = len(evil_blob)
        tar.addfile(evil, io.BytesIO(evil_blob))
    with pytest.raises(BackupError):
        parse_archive(buf.getvalue(), current_revision="0055", strict=True)


def test_symlink_member_is_refused() -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_blob = json.dumps(
            {"format_version": 1, "alembic_revision": "0055", "tables": {}}
        ).encode()
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_blob)
        tar.addfile(info, io.BytesIO(manifest_blob))

        link = tarfile.TarInfo(name="tables/agent.ndjson")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    with pytest.raises(BackupError):
        parse_archive(buf.getvalue(), current_revision="0055", strict=True)


async def test_decompression_bomb_is_refused(
    db_session: AsyncSession, acme_tenant: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real, well-formed archive is already bigger, decompressed, than a
    deliberately tiny cap -- the same code path a genuine gzip bomb (small
    compressed input, huge decompressed output) would hit, exercised here
    without actually allocating gigabytes in a unit test."""
    _, data = await export_archive(db_session, tenant_id=acme_tenant, passphrase=None)
    monkeypatch.setattr("oc8.backup.reader.MAX_DECOMPRESSED_BYTES", 16)
    with pytest.raises(BackupError, match="16 bytes"):
        parse_archive(data, current_revision="doesn't matter", strict=True)


def test_real_bomb_ratio_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuinely bomb-shaped payload: highly compressible zero bytes that
    expand well past the (lowered, for this test) cap while the compressed
    archive itself stays tiny on the wire."""
    huge_zeros = bytes(2 * 1024 * 1024)  # 2 MiB of zeros
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as tar:
        info = tarfile.TarInfo(name="tables/agent.ndjson")
        info.size = len(huge_zeros)
        tar.addfile(info, io.BytesIO(huge_zeros))
    bomb = buf.getvalue()
    assert len(bomb) < 4096  # tiny on the wire relative to what it expands to

    monkeypatch.setattr("oc8.backup.reader.MAX_DECOMPRESSED_BYTES", 1024)
    with pytest.raises(BackupError):
        parse_archive(bomb, current_revision="0055", strict=True)
