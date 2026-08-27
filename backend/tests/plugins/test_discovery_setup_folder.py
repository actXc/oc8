from __future__ import annotations

from pathlib import Path

import pytest

from oc8.capas.discovery import _read_setup_folder
from oc8.capas.manifest import ManifestError


def _write(folder: Path, name: str, content: str) -> None:
    (folder / name).write_text(content, encoding="utf-8")


def test_no_setup_folder_returns_none(tmp_path: Path) -> None:
    assert _read_setup_folder(tmp_path) is None


def test_setup_folder_without_fields_toml_is_a_hard_error(tmp_path: Path) -> None:
    # `PluginSetupSpec.title` is required with no default; assembling a form
    # with title="" would ship an untitled setup dialog instead of failing.
    setup = tmp_path / "setup"
    setup.mkdir()
    _write(setup, "validation.toml", 'any_of = [["a"], ["b"]]\n')
    with pytest.raises(ManifestError, match=r"fields\.toml"):
        _read_setup_folder(tmp_path)


def test_an_unknown_filename_in_setup_is_a_hard_error(tmp_path: Path) -> None:
    # Only four names are ever read, so anything else is invisible: one extra
    # letter in `oauth_provisioning.toml` dropped the whole OAuth block with no
    # error. Rejected by name, listing the file that will never be read.
    setup = tmp_path / "setup"
    setup.mkdir()
    _write(setup, "fields.toml", 'title = "Set up X"\n')
    _write(setup, "oauth_provisioning.toml", 'provider = "google"\n')
    with pytest.raises(ManifestError, match=r"oauth_provisioning\.toml"):
        _read_setup_folder(tmp_path)


def test_a_stray_non_toml_file_in_setup_is_a_hard_error(tmp_path: Path) -> None:
    setup = tmp_path / "setup"
    setup.mkdir()
    _write(setup, "fields.toml", 'title = "Set up X"\n')
    _write(setup, "notes.md", "scratch\n")
    with pytest.raises(ManifestError, match=r"notes\.md"):
        _read_setup_folder(tmp_path)


def test_an_unknown_top_level_key_in_fields_toml_is_a_hard_error(tmp_path: Path) -> None:
    # A `[validation]` block written into fields.toml instead of its own file
    # used to be discarded silently -- `spec.validation.any_of` came back `[]`
    # and the form quietly stopped enforcing the author's constraint. The check
    # is general, not `validation`-specific, so any misplaced block is caught.
    setup = tmp_path / "setup"
    setup.mkdir()
    _write(
        setup,
        "fields.toml",
        """
title = "Set up X"

[validation]
any_of = [["a"], ["b"]]
""",
    )
    with pytest.raises(ManifestError, match=r"unknown key\(s\) \['validation'\]"):
        _read_setup_folder(tmp_path)


def test_validate_entry_point_survives_the_split(tmp_path: Path) -> None:
    # Two shipped plugins (telegram_approvals, whatsapp_approvals) set this;
    # dropping it silently disables their credential test while the setup
    # submission still reports success.
    setup = tmp_path / "setup"
    setup.mkdir()
    _write(
        setup,
        "fields.toml",
        """
title = "Telegram verbinden"
validate_entry_point = "channel.setup:validate"

[[fields]]
key = "bot_token"
label = "Bot-Token"
kind = "password"
""",
    )
    spec = _read_setup_folder(tmp_path)
    assert spec is not None
    assert spec.validate_entry_point == "channel.setup:validate"


def test_fields_only_setup(tmp_path: Path) -> None:
    setup = tmp_path / "setup"
    setup.mkdir()
    _write(
        setup,
        "fields.toml",
        """
title = "Set up X"

[[fields]]
key = "url"
label = "URL"
kind = "url"
""",
    )
    spec = _read_setup_folder(tmp_path)
    assert spec is not None
    assert spec.title == "Set up X"
    assert len(spec.fields) == 1
    assert spec.fields[0].key == "url"
    assert spec.validation.any_of == []
    assert spec.mcp is None
    assert spec.oauth_provision is None


def test_all_four_files(tmp_path: Path) -> None:
    setup = tmp_path / "setup"
    setup.mkdir()
    _write(
        setup,
        "fields.toml",
        """
title = "Connect X"

[[fields]]
key = "tenant_id"
label = "Tenant ID"
kind = "text"

[[fields]]
key = "shared_drive_ids"
label = "Shared drives"
kind = "text"
required = false

[[fields]]
key = "delegated_mailboxes"
label = "Mailboxes"
kind = "text"
required = false
""",
    )
    _write(
        setup,
        "validation.toml",
        """
any_of = [["shared_drive_ids"], ["delegated_mailboxes"]]
""",
    )
    _write(
        setup,
        "oauth_provision.toml",
        """
provider = "google"
connector_type = "google_workspace_files"
site_ids_field = "shared_drive_ids"
source_ids_config_key = "sharedDriveIds"
""",
    )
    _write(
        setup,
        "mcp.toml",
        """
connection_key = "primary"
name = "x"
command = "python"
args = ["-m", "x_bridge"]
department_field = "department"
""",
    )
    spec = _read_setup_folder(tmp_path)
    assert spec is not None
    assert len(spec.fields) == 3
    assert spec.validation.any_of == [["shared_drive_ids"], ["delegated_mailboxes"]]
    assert spec.oauth_provision is not None
    assert spec.oauth_provision.provider == "google"
    assert spec.mcp is not None
    assert spec.mcp.command == "python"
    assert spec.mcp.department_field == "department"
