"""One run's evidence, packed into one self-contained file.

§12.5.1 splits the audit trail into a LEDGER (which agent did what, small, hash
-chained, kept for the full retention period) and EVIDENCE (the transcript, the
standing instructions, the session state -- bulky, kept for a bounded window).
This module is the evidence side: it turns the directory a runtime left behind
into a single compressed archive whose SHA-256 the ledger can point at.

**Why one archive per run rather than a content-addressed blob store.** The
spec's §12.5.1 point 4 proposed content-addressing, on the argument that every
run ships byte-identical standing instructions and tool schemas. Measured
against 406 real runs (116.9 MB) it does not pay for itself:

| | kB/run | of today |
|---|---|---|
| as stored today | 281.1 | 100 % |
| drop what the runtime writes for itself | 184.9 | 66 % |
| + content-address (dedup) | 156.4 | 56 % |
| + compress | 17.4 | 6 % |

Content-addressing removes 10 % of the raw bytes -- and a compressor removes
those same repeated bytes anyway. Measured directly: after compression, dedup is
worth a further 0.32 %. The repetition it targets is real; it is simply not
repetition a compressor misses. So there is no blob store here, no reference
counting and no garbage collector: a run's evidence is one file, named after the
run, deleted when its window closes.

**Why xz and not a shared zstd dictionary.** A dictionary gets 5.5 kB/run
instead of 17.4 -- 3.2x better, and the biggest remaining win on the table. It
is not taken, because every archive would then depend on a file that has to
survive as long as the evidence does. Losing one dictionary would make years of
evidence unreadable at once, which is the failure an audit trail exists to be
immune to. This archive is readable with nothing but the Python standard
library, on any machine, forever. That independence is the 3.2x.

The other properties are evidentiary rather than operational, and each is a test
in tests/evidence/test_archive.py:

- **Deterministic.** Members sorted, mtime and ownership zeroed, so the hash is
  a function of the evidence and not of when the sweep happened to run. Re-
  archiving after a crash produces the same hash instead of looking like
  different evidence.
- **Regular files only, never followed.** The tree is writable from inside the
  container. A symlink to a host file would otherwise be pulled in and hashed
  into the ledger as "what this run produced" -- exfiltration wearing the
  costume of evidence.
- **Streamed.** A container can write a gigabyte into its own workspace; the
  sweep has to survive finding one.
- **All-or-nothing.** The archive is built under a `.part` name and renamed only
  once it is complete and hashed. A half-written file whose hash nobody recorded
  is worse than no file: the next sweep would read it as already archived.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import lzma
import os
import stat
import tarfile
from collections.abc import Sequence
from dataclasses import dataclass
from typing import BinaryIO

logger = logging.getLogger(__name__)

#: What an evidence archive is called. The sweep finds archives by this suffix,
#: so it is a shared constant rather than a literal in two places.
ARCHIVE_SUFFIX = ".tar.xz"

_PART_SUFFIX = ".part"

#: Measured on the real corpus: preset 6 (the stdlib default) gives 17.4 kB per
#: run. Preset 9 buys under 2 % for several times the CPU, which is a bad trade
#: for a job that runs on every worker's housekeeping tick.
_PRESET = 6

_HASH_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class ArchiveResult:
    """What was captured, what was left out, and what it cost.

    `dropped_*` and `skipped_*` are reported rather than logged away because
    they are the honest part: an auditor looking at this run's evidence has to
    be able to see that something was left out, and how much.
    """

    path: str
    sha256: str
    files: int
    raw_bytes: int
    stored_bytes: int
    dropped_files: int
    dropped_bytes: int
    skipped_files: int


def _is_excluded(rel: str, excludes: Sequence[str]) -> bool:
    """Prefix match on a path BOUNDARY.

    `claude-home/telemetry` must not also swallow `claude-home/telephone.md`,
    so a prefix matches either the whole path or a path followed by `/`.
    """
    for raw in excludes:
        prefix = raw.rstrip("/")
        if not prefix:
            continue
        if rel == prefix or rel.startswith(prefix + "/"):
            return True
    return False


def _add_member(tar: tarfile.TarFile, rel: str, path: str, size: int) -> None:
    """One regular file, stored without any trace of this host.

    Extracted so the tests can make a member fail mid-archive and prove nothing
    is left behind.
    """
    info = tarfile.TarInfo(rel)
    info.size = size
    info.mtime = 0
    info.mode = 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    with open(path, "rb") as fh:
        tar.addfile(info, fh)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _collect(src: str, excludes: Sequence[str]) -> tuple[list[tuple[str, str, int]], int, int, int]:
    """(members, dropped_files, dropped_bytes, skipped_files), members sorted.

    `os.walk` does not descend into symlinked directories, and `lstat` is what
    keeps a symlinked FILE from being opened -- both halves are needed, and the
    symlink test fails without either.
    """
    members: list[tuple[str, str, int]] = []
    dropped_files = dropped_bytes = skipped = 0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, src)
            try:
                st = os.lstat(path)
            except OSError:
                skipped += 1
                continue
            if not stat.S_ISREG(st.st_mode):
                # A symlink, socket or fifo. Never followed: this tree is
                # writable from inside the container.
                skipped += 1
                continue
            if _is_excluded(rel, excludes):
                dropped_files += 1
                dropped_bytes += st.st_size
                continue
            if not os.access(path, os.R_OK):
                logger.warning("evidence: cannot read %s, leaving it out", path)
                skipped += 1
                continue
            members.append((rel, path, st.st_size))
        # A symlinked directory shows up in dirnames; os.walk will not descend
        # into it, but it must not be counted as evidence either.
        for name in list(dirnames):
            if os.path.islink(os.path.join(dirpath, name)):
                skipped += 1
                dirnames.remove(name)
    members.sort()
    return members, dropped_files, dropped_bytes, skipped


def archive_dir(src: str, dest: str, *, excludes: Sequence[str]) -> ArchiveResult:
    """Pack `src` into `dest`, leaving `src` untouched.

    Deleting the source is deliberately NOT part of this: the sweep commits the
    ledger entry between archiving and deleting, so a crash in the middle leaves
    the evidence in both places rather than in neither.
    """
    members, dropped_files, dropped_bytes, skipped = _collect(src, excludes)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    part = dest + _PART_SUFFIX
    raw_bytes = 0
    try:
        with lzma.open(part, "wb", preset=_PRESET) as comp:
            # A stream ("w|") rather than a seekable archive: nothing here needs
            # to rewrite a header, and it keeps memory flat over a big member.
            with tarfile.open(fileobj=comp, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                for rel, path, size in members:
                    _add_member(tar, rel, path, size)
                    raw_bytes += size
        digest = _sha256(part)
        stored = os.path.getsize(part)
        os.replace(part, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(part)
        raise
    return ArchiveResult(
        path=dest,
        sha256=digest,
        files=len(members),
        raw_bytes=raw_bytes,
        stored_bytes=stored,
        dropped_files=dropped_files,
        dropped_bytes=dropped_bytes,
        skipped_files=skipped,
    )


def open_archive(path: str) -> BinaryIO:
    """Read an archive back. Nothing but the standard library, by design."""
    return lzma.open(path, "rb")  # type: ignore[return-value]
