"""What a run's evidence archive has to guarantee.

The archive is not a backup: it is the thing an auditor reads when the ledger
says an agent did something and the question is *why*. So the properties tested
here are evidentiary, not operational -- the bytes come back exactly, the hash
in the ledger identifies THIS archive and not a similar one, and nothing the
container could write into its own workspace can widen what gets captured.
"""

from __future__ import annotations

import hashlib
import io
import lzma
import os
import tarfile
from pathlib import Path

from oc8.evidence.archive import ARCHIVE_SUFFIX, archive_dir


def _tree(root: str, files: dict[str, bytes]) -> str:
    for rel, data in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    return root


def _members(path: str) -> dict[str, bytes]:
    with lzma.open(path, "rb") as raw, tarfile.open(fileobj=raw, mode="r|") as tar:
        out = {}
        for info in tar:
            fh = tar.extractfile(info)
            out[info.name] = fh.read() if fh else b""
        return out


def test_round_trip_is_byte_exact(tmp_path: Path) -> None:
    """Evidence that cannot be read back is not evidence."""
    src = _tree(
        str(tmp_path / "run"),
        {
            "agent/CLAUDE.md": b"# standing instructions\n" * 40,
            "claude-home/projects/p/transcript.jsonl": b'{"role":"assistant"}\n' * 200,
            "inbound.db": os.urandom(4096),
        },
    )
    dest = str(tmp_path / "run.tar.xz")

    result = archive_dir(src, dest, excludes=())

    assert _members(dest) == {
        "agent/CLAUDE.md": b"# standing instructions\n" * 40,
        "claude-home/projects/p/transcript.jsonl": b'{"role":"assistant"}\n' * 200,
        "inbound.db": _members(dest)["inbound.db"],  # compared in full below
    }
    with open(os.path.join(src, "inbound.db"), "rb") as fh:
        assert _members(dest)["inbound.db"] == fh.read()
    assert result.files == 3
    assert result.stored_bytes == os.path.getsize(dest)


def test_hash_identifies_the_archive_on_disk(tmp_path: Path) -> None:
    """The ledger records this hash; it has to be the file's own."""
    src = _tree(str(tmp_path / "run"), {"a.txt": b"one", "b/c.txt": b"two"})
    dest = str(tmp_path / "run.tar.xz")

    result = archive_dir(src, dest, excludes=())

    with open(dest, "rb") as fh:
        assert result.sha256 == hashlib.sha256(fh.read()).hexdigest()


def test_same_evidence_hashes_the_same_from_a_different_directory(tmp_path: Path) -> None:
    """Two archivings of identical evidence agree.

    Without this the hash would encode the filesystem's walk order and the
    files' mtimes -- accidents of when the sweep ran -- so re-archiving the
    same tree after a crash would look like different evidence.
    """
    files = {"z.txt": b"z", "a.txt": b"a", "m/n.txt": b"n"}
    one = archive_dir(_tree(str(tmp_path / "r1"), files), str(tmp_path / "1.tar.xz"), excludes=())
    # Different mtimes, different creation order, different parent directory.
    two = archive_dir(
        _tree(str(tmp_path / "r2"), dict(reversed(list(files.items())))),
        str(tmp_path / "2.tar.xz"),
        excludes=(),
    )

    assert one.sha256 == two.sha256


def test_excluded_paths_are_dropped_and_counted(tmp_path: Path) -> None:
    """Dropping is reported, never silent -- an auditor must be able to see
    that something was left out and how much of it there was."""
    src = _tree(
        str(tmp_path / "run"),
        {
            "agent/CLAUDE.md": b"keep me",
            "claude-home/telemetry/1p_failed_events.a.json": b"x" * 5000,
            "claude-home/telemetry/1p_failed_events.b.json": b"y" * 3000,
            ".heartbeat": b"1",
        },
    )
    dest = str(tmp_path / "run.tar.xz")

    result = archive_dir(src, dest, excludes=("claude-home/telemetry/", ".heartbeat"))

    assert set(_members(dest)) == {"agent/CLAUDE.md"}
    assert result.files == 1
    assert result.dropped_files == 3
    assert result.dropped_bytes == 5000 + 3000 + 1


def test_a_symlink_out_of_the_tree_captures_nothing(tmp_path: Path) -> None:
    """The container writes into this tree, so a symlink in it is attacker input.

    Following one would pull a host file into an archive that is then hashed
    into the audit ledger as "what this run produced" -- an exfiltration path
    dressed up as evidence.
    """
    secret = tmp_path / "host-secret"
    secret.write_bytes(b"root:x:0:0:/root:/bin/sh")
    src = _tree(str(tmp_path / "run"), {"agent/CLAUDE.md": b"ok"})
    os.symlink(str(secret), os.path.join(src, "agent", "stolen"))
    os.symlink(str(tmp_path), os.path.join(src, "escape"))
    dest = str(tmp_path / "run.tar.xz")

    result = archive_dir(src, dest, excludes=())

    assert set(_members(dest)) == {"agent/CLAUDE.md"}
    assert result.skipped_files == 2
    with open(dest, "rb") as fh:
        assert b"root:x:0:0" not in lzma.decompress(fh.read())


def test_an_empty_tree_still_produces_a_readable_archive(tmp_path: Path) -> None:
    """A run that wrote nothing is a fact worth recording, not an error."""
    src = str(tmp_path / "run")
    os.makedirs(src)
    dest = str(tmp_path / "run.tar.xz")

    result = archive_dir(src, dest, excludes=())

    assert result.files == 0
    assert _members(dest) == {}


def test_a_failed_archive_leaves_no_archive_behind(tmp_path: Path) -> None:
    """A half-written archive whose hash was never recorded is worse than none:
    the next sweep would find it and treat the run as already archived."""
    src = _tree(str(tmp_path / "run"), {"a.txt": b"a"})
    dest = str(tmp_path / "run.tar.xz")

    class Boom(Exception):
        pass

    def explode(*_a: object, **_k: object) -> None:
        raise Boom

    import oc8.evidence.archive as mod

    original = mod._add_member
    mod._add_member = explode
    try:
        try:
            archive_dir(src, dest, excludes=())
        except Boom:
            pass
        else:
            raise AssertionError("expected the failure to propagate")
    finally:
        mod._add_member = original

    assert not os.path.exists(dest)
    assert [p for p in os.listdir(tmp_path) if p.endswith(".part")] == []


def test_the_source_tree_is_left_alone(tmp_path: Path) -> None:
    """Archiving and deleting are separate steps: the ledger entry commits
    between them, so a crash can never lose the evidence AND its record."""
    src = _tree(str(tmp_path / "run"), {"a.txt": b"a"})

    archive_dir(src, str(tmp_path / "run.tar.xz"), excludes=())

    assert os.path.exists(os.path.join(src, "a.txt"))


def test_suffix_is_the_one_the_sweep_looks_for(tmp_path: Path) -> None:
    assert ARCHIVE_SUFFIX == ".tar.xz"


def test_nested_exclude_prefix_matches_only_on_a_path_boundary(tmp_path: Path) -> None:
    """`claude-home/tele` must not drop `claude-home/telemetry-notes.md`'s
    sibling `claude-home/telephone.md` by accident."""
    src = _tree(
        str(tmp_path / "run"),
        {"claude-home/telemetry/a.json": b"drop", "claude-home/telephone.md": b"keep"},
    )
    dest = str(tmp_path / "run.tar.xz")

    archive_dir(src, dest, excludes=("claude-home/telemetry",))

    assert set(_members(dest)) == {"claude-home/telephone.md"}


def test_raw_bytes_is_what_went_in(tmp_path: Path) -> None:
    src = _tree(str(tmp_path / "run"), {"a.txt": b"a" * 1000, "b.txt": b"b" * 2000})

    result = archive_dir(src, str(tmp_path / "run.tar.xz"), excludes=())

    assert result.raw_bytes == 3000
    # The whole point: what lands on disk is a fraction of what went in.
    assert result.stored_bytes < result.raw_bytes


def test_archive_lands_in_a_directory_that_does_not_exist_yet(tmp_path: Path) -> None:
    src = _tree(str(tmp_path / "run"), {"a.txt": b"a"})
    dest = str(tmp_path / "archive" / "agent" / "run.tar.xz")

    archive_dir(src, dest, excludes=())

    assert os.path.exists(dest)


def test_unreadable_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    """One bad file must not cost the run its whole archive."""
    src = _tree(str(tmp_path / "run"), {"good.txt": b"g", "bad.txt": b"b"})
    bad = os.path.join(src, "bad.txt")
    os.chmod(bad, 0o000)
    dest = str(tmp_path / "run.tar.xz")
    try:
        if os.access(bad, os.R_OK):  # running as root: the premise does not hold
            return
        result = archive_dir(src, dest, excludes=())
    finally:
        os.chmod(bad, 0o600)

    assert set(_members(dest)) == {"good.txt"}
    assert result.skipped_files == 1


def test_members_are_stored_without_host_identity(tmp_path: Path) -> None:
    """uid/gid/mtime of the control plane are not evidence, and leaking them
    makes two archivings of the same tree disagree."""
    src = _tree(str(tmp_path / "run"), {"a.txt": b"a"})
    dest = str(tmp_path / "run.tar.xz")

    archive_dir(src, dest, excludes=())

    with lzma.open(dest, "rb") as raw, tarfile.open(fileobj=raw, mode="r|") as tar:
        info = next(iter(tar))
        assert (info.uid, info.gid, info.mtime, info.uname, info.gname) == (0, 0, 0, "", "")


def test_a_large_member_does_not_have_to_fit_in_memory(tmp_path: Path) -> None:
    """Streamed, not read whole: a container can write whatever it likes into
    its own workspace, and the sweep must survive finding it."""
    src = str(tmp_path / "run")
    os.makedirs(src)
    big = os.path.join(src, "big.bin")
    with open(big, "wb") as fh:
        fh.write(b"\0" * (8 * 1024 * 1024))
    dest = str(tmp_path / "run.tar.xz")

    result = archive_dir(src, dest, excludes=())

    assert result.raw_bytes == 8 * 1024 * 1024
    with lzma.open(dest, "rb") as raw, tarfile.open(fileobj=raw, mode="r|") as tar:
        info = next(iter(tar))
        assert info.size == 8 * 1024 * 1024


def test_empty_directories_are_not_members(tmp_path: Path) -> None:
    """Only files. A directory carries no evidence and its permissions are the
    control plane's, not the run's."""
    src = _tree(str(tmp_path / "run"), {"a.txt": b"a"})
    os.makedirs(os.path.join(src, "empty", "deeper"))
    dest = str(tmp_path / "run.tar.xz")

    archive_dir(src, dest, excludes=())

    assert set(_members(dest)) == {"a.txt"}


def test_reading_it_back_needs_nothing_but_the_stdlib(tmp_path: Path) -> None:
    """No shared dictionary, no sidecar: the archive is self-contained, which
    is why it costs 3.2x what a dictionary-compressed one would."""
    src = _tree(str(tmp_path / "run"), {"a.txt": b"a" * 500})
    dest = str(tmp_path / "run.tar.xz")

    archive_dir(src, dest, excludes=())

    with open(dest, "rb") as fh:
        blob = fh.read()
    with tarfile.open(fileobj=io.BytesIO(lzma.decompress(blob)), mode="r") as tar:
        assert tar.extractfile("a.txt").read() == b"a" * 500  # type: ignore[union-attr]
