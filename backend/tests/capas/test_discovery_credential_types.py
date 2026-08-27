from __future__ import annotations

from pathlib import Path

import pytest

from oc8.capas.discovery import _read_credential_types_folder
from oc8.capas.manifest import ManifestError


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_absent_folder_returns_empty_list(tmp_path: Path) -> None:
    assert _read_credential_types_folder(tmp_path) == []


def test_reads_one_file_per_type(tmp_path: Path) -> None:
    _write(
        tmp_path / "credential_types" / "s3_api.toml",
        """
        name = "s3_api"
        display_name = "S3 / Object storage"

        [[fields]]
        key = "access_key"
        label = "Access key"
        kind = "password"
        """,
    )
    specs = _read_credential_types_folder(tmp_path)
    assert [s.name for s in specs] == ["s3_api"]
    assert specs[0].fields[0].key == "access_key"


def test_non_toml_file_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "credential_types" / "readme.md", "not a credential type")
    with pytest.raises(ManifestError, match="unexpected file"):
        _read_credential_types_folder(tmp_path)


def test_malformed_toml_names_the_file(tmp_path: Path) -> None:
    _write(tmp_path / "credential_types" / "broken.toml", "not = valid = toml = [")
    with pytest.raises(ManifestError, match=r"broken\.toml"):
        _read_credential_types_folder(tmp_path)
