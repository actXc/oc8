"""Reading and validating an uploaded archive (design doc §3.1, §8).

The input here is a tenant-admin-uploaded file, so this module treats it as
hostile rather than merely "possibly corrupt":

- **Path traversal.** Nothing in this module ever writes an extracted member
  to the filesystem (`tarfile.extractall` is never called; every member is
  read into memory via `extractfile`), but every member name is still
  checked before it is opened -- an absolute path, a `..` path segment, or a
  symlink/hardlink/device entry is refused outright. That way a future
  refactor toward `extractall` does not silently inherit a hole this module
  never had to have.
- **Decompression bombs.** Gzip's ratio can be enormous. The archive is
  gunzipped up front into a size-capped buffer (`MAX_DECOMPRESSED_BYTES`),
  read in bounded chunks so the cap is enforced incrementally rather than
  after a huge buffer already exists; only the bounded, no-longer-compressed
  bytes are ever handed to `tarfile`.

`parse_archive` serves both the restore path (`strict=True`: raise on the
first problem) and the preview path (`strict=False`: collect every problem
into the returned list instead). Deliberately one function: preview must
never say an archive is clean when restore would then refuse it, so the two
modes share every problem-detecting code path and differ only in what
happens once a problem is found.

Two categories are the deliberate exception and always raise regardless of
`strict`, because there is nothing safe to *report* about them:

1. Structural corruption -- not a valid gzip/tar, a `manifest.json` that
   cannot be parsed at all, or a `format_version` this reader does not
   understand. A v1 reader cannot say anything trustworthy about a v2
   archive's contents, so a problem list built from it would be fiction.
2. A hostile archive shape -- a member escaping via `..`, an absolute path, a
   symlink, hardlink or device node. Refusing to describe a file that is
   trying to attack the host is the point; enumerating its problems politely
   is not.

Everything else -- revision mismatch, checksum failure, unknown table, missing
member, malformed NDJSON -- flows through the shared problem list, so preview
can never call an archive clean that restore would then refuse.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from typing import Any

from oc8.backup.archive import FORMAT_VERSION
from oc8.backup.errors import BackupError
from oc8.backup.tables import exported_tables

#: Hard ceiling on the decompressed size of an uploaded archive. Generous for
#: a pilot-sized company's backup (design doc §6 -- whole-archive-in-memory
#: is the chosen tradeoff there too) while making a crafted small `.gz` that
#: expands to gigabytes fail fast instead of exhausting memory.
MAX_DECOMPRESSED_BYTES = 512 * 1024 * 1024  # 512 MiB

#: Gunzip in bounded chunks rather than one `.read()` call, so the cap above
#: is enforced as bytes come out instead of only after they are all in
#: memory.
_CHUNK_SIZE = 1024 * 1024  # 1 MiB


@dataclass
class ParsedArchive:
    manifest: dict[str, Any]
    tables: dict[str, list[dict[str, Any]]]
    secrets_ciphertext: bytes | None


def _decompress_bounded(data: bytes) -> bytes:
    """Gunzip `data` into memory, refusing once more than
    `MAX_DECOMPRESSED_BYTES` has come out."""
    try:
        gz = gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb")
        out = io.BytesIO()
        total = 0
        while True:
            chunk = gz.read(_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DECOMPRESSED_BYTES:
                raise BackupError(
                    f"archive expands past {MAX_DECOMPRESSED_BYTES} bytes uncompressed; refusing"
                )
            out.write(chunk)
    except BackupError:
        raise
    except OSError as exc:
        raise BackupError(f"malformed archive: {exc}") from exc
    return out.getvalue()


def _validate_member_names(tar: tarfile.TarFile) -> None:
    """Refuse any member this reader is not willing to open, before any
    member's content is read (design doc §8 SECURITY note: traversal and
    symlink guards apply even though nothing here extracts to disk)."""
    for info in tar.getmembers():
        if info.name.startswith("/") or ".." in info.name.split("/"):
            raise BackupError(f"unsafe path in archive: {info.name!r}")
        if info.issym() or info.islnk() or info.isdev():
            raise BackupError(f"unsafe member type in archive: {info.name!r}")


def _read_member(tar: tarfile.TarFile, info: tarfile.TarInfo) -> bytes:
    fh = tar.extractfile(info)
    if fh is None:
        # Cannot happen for a `isreg()` member, but `extractfile`'s return
        # type is `IO[bytes] | None` -- fail loudly rather than propagate a
        # `None.read()` AttributeError.
        raise BackupError(f"archive member {info.name!r} could not be read")
    return fh.read()


def _safe_member(tar: tarfile.TarFile, name: str) -> tarfile.TarInfo | None:
    """Look up `name`, refusing anything that is not a plain regular file.
    Returns `None` if the member is simply absent (a "missing member"
    problem, distinct from an unsafe one, which raises)."""
    try:
        info = tar.getmember(name)
    except KeyError:
        return None
    if not info.isreg():
        raise BackupError(f"unsafe member type in archive: {name!r}")
    return info


def parse_archive(
    data: bytes, *, current_revision: str, strict: bool = True
) -> tuple[ParsedArchive, list[str]]:
    problems: list[str] = []

    tar_bytes = _decompress_bounded(data)

    try:
        tar = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:")
    except tarfile.TarError as exc:
        raise BackupError(f"malformed archive: {exc}") from exc

    with tar:
        _validate_member_names(tar)

        manifest_info = _safe_member(tar, "manifest.json")
        if manifest_info is None:
            raise BackupError("archive is missing manifest.json")
        try:
            manifest = json.loads(_read_member(tar, manifest_info))
        except (ValueError, UnicodeDecodeError) as exc:
            raise BackupError(f"malformed manifest.json: {exc}") from exc
        if not isinstance(manifest, dict):
            raise BackupError("malformed manifest.json: expected a JSON object")

        if manifest.get("format_version") != FORMAT_VERSION:
            raise BackupError(
                f"unsupported format_version {manifest.get('format_version')!r}; "
                f"this instance understands {FORMAT_VERSION!r}"
            )

        archive_revision = manifest.get("alembic_revision")
        if archive_revision != current_revision:
            problems.append(
                f"archive is at alembic revision {archive_revision!r}, this instance "
                f"is at {current_revision!r}; there is no migration path for an "
                "archived tenant -- upgrade the source instance and re-export"
            )

        manifest_tables = manifest.get("tables", {})
        if not isinstance(manifest_tables, dict):
            raise BackupError("malformed manifest.json: 'tables' is not an object")

        known = exported_tables()
        tables: dict[str, list[dict[str, Any]]] = {}
        for name, meta in manifest_tables.items():
            if name not in known:
                problems.append(
                    f"unknown table {name!r} in archive -- this archive was likely "
                    "exported from a newer instance"
                )
                continue

            member = _safe_member(tar, f"tables/{name}.ndjson")
            if member is None:
                problems.append(f"archive is missing tables/{name}.ndjson")
                continue

            blob = _read_member(tar, member)
            expected_sha = meta.get("sha256") if isinstance(meta, dict) else None
            if hashlib.sha256(blob).hexdigest() != expected_sha:
                problems.append(f"checksum mismatch for table {name!r}")
                continue

            try:
                rows = [json.loads(line) for line in blob.splitlines() if line]
            except ValueError as exc:
                problems.append(f"malformed data for table {name!r}: {exc}")
                continue

            tables[name] = rows

        secrets_ciphertext: bytes | None = None
        # Presence, not truthiness. `secrets: {}` is a writer that produced an
        # envelope with empty metadata, not a writer that wrote no secrets --
        # and a truthy test would skip reading a `secrets.json` that is
        # physically in the tar, dropping every credential without a word.
        # `null` is the only value that means "there are none".
        if manifest.get("secrets") is not None:
            secrets_member = _safe_member(tar, "secrets.json")
            if secrets_member is None:
                problems.append("manifest declares secrets but secrets.json is missing")
            else:
                secrets_ciphertext = _read_member(tar, secrets_member)

    if strict and problems:
        raise BackupError(problems[0])

    return (
        ParsedArchive(manifest=manifest, tables=tables, secrets_ciphertext=secrets_ciphertext),
        problems,
    )
