from __future__ import annotations

from pathlib import Path

from oc8.capas.discovery import discover_plugins


def _plugin_toml(folder: Path, name: str, extra: str = "") -> None:
    (folder / "plugin.toml").write_text(
        f"""
[plugin]
name = "{name}"
version = "1.0.0"
type = "tool_pack"
trust = "first_party"
summary = "test plugin"
{extra}
""",
        encoding="utf-8",
    )


def test_tool_pack_toml_merges_into_manifest(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x")
    (plugin_dir / "tool_pack.toml").write_text(
        """
[[connections]]
key = "primary"
name = "x"
transport = "stdio"
server_url = ""

[connections.scopes]
read = ["get_thing"]
send = ["do_thing"]

[connections.config]
command = "python"
args = ["-m", "x_bridge"]
""",
        encoding="utf-8",
    )
    [discovered] = discover_plugins([str(tmp_path)])
    assert discovered.valid, discovered.error
    tool_pack = discovered.manifest["tool_pack"]
    assert tool_pack is not None
    conn = tool_pack["connections"][0]
    assert conn["name"] == "x"
    assert conn["scopes"]["read"] == ["get_thing"]
    assert conn["config"]["command"] == "python"


def test_guardrails_folder_merges_presets_into_the_named_connection(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x")
    (plugin_dir / "tool_pack.toml").write_text(
        """
[[connections]]
key = "primary"
name = "x"
transport = "stdio"
server_url = ""
""",
        encoding="utf-8",
    )
    guardrails = plugin_dir / "guardrails"
    guardrails.mkdir()
    (guardrails / "read_only.toml").write_text(
        """
kind = "preset"
connection = "primary"
key = "read_only"
label = "Nur lesen"
label_en = "Read only"
summary = "x"
summary_en = "x"
read = true
""",
        encoding="utf-8",
    )
    [discovered] = discover_plugins([str(tmp_path)])
    assert discovered.valid, discovered.error
    conn = discovered.manifest["tool_pack"]["connections"][0]
    assert len(conn["guardrail_presets"]) == 1
    assert conn["guardrail_presets"][0]["key"] == "read_only"


def test_guardrails_folder_library_entries_attach_to_discovered_plugin(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x")
    guardrails = plugin_dir / "guardrails"
    guardrails.mkdir()
    (guardrails / "scenario_one.toml").write_text(
        """
kind = "library"
key = "scenario_one"
label = "x"
label_en = "x"
summary = "x"
summary_en = "x"
use_case = "mail"
""",
        encoding="utf-8",
    )
    [discovered] = discover_plugins([str(tmp_path)])
    assert discovered.valid, discovered.error
    assert discovered.guardrail_library is not None
    assert len(discovered.guardrail_library.guardrail) == 1
    assert discovered.guardrail_library.guardrail[0].key == "scenario_one"


def test_setup_folder_merges_into_manifest_setup(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x")
    setup = plugin_dir / "setup"
    setup.mkdir()
    (setup / "fields.toml").write_text(
        """
title = "Set up X"

[[fields]]
key = "url"
label = "URL"
kind = "url"
""",
        encoding="utf-8",
    )
    [discovered] = discover_plugins([str(tmp_path)])
    assert discovered.valid, discovered.error
    assert discovered.manifest["setup"]["title"] == "Set up X"


def test_validate_entry_point_survives_end_to_end_through_read(tmp_path: Path) -> None:
    # Task 3 proved validate_entry_point survives _read_setup_folder's own
    # 4-file merge in isolation; this proves it also survives once THIS
    # task's _read wires that merged dict through parse_manifest and back out
    # as DiscoveredPlugin.manifest -- the thing every retrofit task and the
    # setup-validate endpoint actually consume.
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x")
    setup = plugin_dir / "setup"
    setup.mkdir()
    (setup / "fields.toml").write_text(
        """
title = "Connect the channel"
validate_entry_point = "x.setup:validate"
""",
        encoding="utf-8",
    )
    [discovered] = discover_plugins([str(tmp_path)])
    assert discovered.valid, discovered.error
    assert discovered.manifest["setup"]["validate_entry_point"] == "x.setup:validate"


def test_i18n_folder_loads_po_files(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x")
    i18n = plugin_dir / "i18n"
    i18n.mkdir()
    (i18n / "de.po").write_text('#\nmsgid "Read only"\nmsgstr "Nur lesen"\n', encoding="utf-8")
    [discovered] = discover_plugins([str(tmp_path)])
    assert discovered.valid, discovered.error
    assert discovered.i18n["de"]["Read only"] == "Nur lesen"


def test_no_i18n_folder_gives_empty_dict(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x")
    [discovered] = discover_plugins([str(tmp_path)])
    assert discovered.valid, discovered.error
    assert discovered.i18n == {}


def test_a_preset_without_connection_binds_to_the_sole_connection(tmp_path: Path) -> None:
    # The connection's key is deliberately NOT "primary" -- an omitted
    # `connection` must bind to whatever the plugin's one connection is called.
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x")
    (plugin_dir / "tool_pack.toml").write_text(
        """
[[connections]]
key = "the_only_one"
name = "x"
transport = "stdio"
server_url = ""
""",
        encoding="utf-8",
    )
    guardrails = plugin_dir / "guardrails"
    guardrails.mkdir()
    (guardrails / "read_only.toml").write_text(
        """
kind = "preset"
key = "read_only"
label = "x"
label_en = "x"
summary = "x"
summary_en = "x"
read = true
""",
        encoding="utf-8",
    )
    [discovered] = discover_plugins([str(tmp_path)])
    assert discovered.valid, discovered.error
    conn = discovered.manifest["tool_pack"]["connections"][0]
    assert [p["key"] for p in conn["guardrail_presets"]] == ["read_only"]


def test_an_inline_tool_pack_table_is_a_hard_error(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x", extra="\n[plugin.tool_pack]\nconnections = []\n")
    [discovered] = discover_plugins([str(tmp_path)])
    assert not discovered.valid
    assert "tool_pack.toml" in (discovered.error or "")


def test_an_inline_setup_table_is_a_hard_error(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x", extra='\n[plugin.setup]\ntitle = "Set up X"\n')
    [discovered] = discover_plugins([str(tmp_path)])
    assert not discovered.valid
    assert "setup/" in (discovered.error or "")


def test_a_leftover_flat_guardrails_toml_is_a_hard_error(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x")
    (plugin_dir / "guardrails.toml").write_text("[[guardrail]]\n", encoding="utf-8")
    [discovered] = discover_plugins([str(tmp_path)])
    assert not discovered.valid
    assert "guardrails/" in (discovered.error or "")


def test_a_typod_setup_filename_is_a_hard_error(tmp_path: Path) -> None:
    # The cutover's own failure class, reached from the TYPO direction: only
    # four filenames are read, so `oauth_provisioning.toml` (one extra letter)
    # used to discover perfectly valid with the ENTIRE OAuth provisioning
    # block missing and nothing said anywhere.
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x")
    setup = plugin_dir / "setup"
    setup.mkdir()
    (setup / "fields.toml").write_text('title = "Set up X"\n', encoding="utf-8")
    (setup / "oauth_provisioning.toml").write_text(
        """
provider = "google"
connector_type = "google_workspace_files"
site_ids_field = "shared_drive_ids"
source_ids_config_key = "sharedDriveIds"
""",
        encoding="utf-8",
    )
    [discovered] = discover_plugins([str(tmp_path)])
    assert not discovered.valid
    assert "oauth_provisioning.toml" in (discovered.error or "")


def test_a_typod_guardrail_file_extension_is_a_hard_error(tmp_path: Path) -> None:
    # `read_only.tml` matches no `*.toml` glob, so the guardrail simply did not
    # exist -- and guardrails are an agent's permission ceiling.
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x")
    guardrails = plugin_dir / "guardrails"
    guardrails.mkdir()
    (guardrails / "read_only.tml").write_text(
        """
kind = "preset"
key = "read_only"
label = "x"
label_en = "x"
summary = "x"
summary_en = "x"
read = true
""",
        encoding="utf-8",
    )
    [discovered] = discover_plugins([str(tmp_path)])
    assert not discovered.valid
    assert "read_only.tml" in (discovered.error or "")


def test_a_validation_block_inside_fields_toml_is_a_hard_error(tmp_path: Path) -> None:
    # `[validation]` belongs in setup/validation.toml. Written into fields.toml
    # it used to be discarded silently: `setup.validation.any_of` came out `[]`
    # and the form stopped enforcing its constraint with no error.
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x")
    setup = plugin_dir / "setup"
    setup.mkdir()
    (setup / "fields.toml").write_text(
        """
title = "Set up X"

[[fields]]
key = "a"
label = "A"
kind = "text"
required = false

[[fields]]
key = "b"
label = "B"
kind = "text"
required = false

[validation]
any_of = [["a"], ["b"]]
""",
        encoding="utf-8",
    )
    [discovered] = discover_plugins([str(tmp_path)])
    assert not discovered.valid
    assert "validation" in (discovered.error or "")


def test_inline_guardrail_presets_in_tool_pack_toml_are_a_hard_error(tmp_path: Path) -> None:
    # The one inline shape the cutover did not cover, and it is squarely on the
    # retrofit path: an author moving `[plugin.tool_pack]` out of plugin.toml
    # carries `guardrail_presets` along with it. Accepted as-is, then silently
    # OVERWRITTEN by guardrails/ for the same connection.
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x")
    (plugin_dir / "tool_pack.toml").write_text(
        """
[[connections]]
key = "primary"
name = "x"
transport = "stdio"
server_url = ""

[[connections.guardrail_presets]]
key = "legacy_inline"
label = "x"
label_en = "x"
summary = "x"
summary_en = "x"
read = true
""",
        encoding="utf-8",
    )
    [discovered] = discover_plugins([str(tmp_path)])
    assert not discovered.valid
    assert "guardrail_presets has moved" in (discovered.error or "")
    assert "guardrails/<key>.toml" in (discovered.error or "")


def test_inline_guardrail_presets_are_rejected_even_when_guardrails_folder_wins(
    tmp_path: Path,
) -> None:
    # The silent-DATA-LOSS half of the same bug: with a guardrails/ preset for
    # the same connection, the inline entry used to be dropped on the floor and
    # discovery reported a perfectly valid plugin carrying only `read_only`.
    plugin_dir = tmp_path / "x"
    plugin_dir.mkdir()
    _plugin_toml(plugin_dir, "x")
    (plugin_dir / "tool_pack.toml").write_text(
        """
[[connections]]
key = "primary"
name = "x"
transport = "stdio"
server_url = ""

[[connections.guardrail_presets]]
key = "legacy_inline"
label = "x"
label_en = "x"
summary = "x"
summary_en = "x"
read = true
""",
        encoding="utf-8",
    )
    guardrails = plugin_dir / "guardrails"
    guardrails.mkdir()
    (guardrails / "read_only.toml").write_text(
        """
kind = "preset"
connection = "primary"
key = "read_only"
label = "x"
label_en = "x"
summary = "x"
summary_en = "x"
read = true
""",
        encoding="utf-8",
    )
    [discovered] = discover_plugins([str(tmp_path)])
    assert not discovered.valid
    assert "guardrail_presets has moved" in (discovered.error or "")


def test_a_malformed_connections_shape_invalidates_only_that_plugin(tmp_path: Path) -> None:
    # Regression for the crash a reviewer found: `connections = ["a"]` (the
    # single-bracket-typo shape a plugin author is realistically going to
    # write instead of `[[connections]]`) used to raise an unhandled
    # AttributeError out of `_read`'s preset-resolution block -- escaping
    # `discover_plugins()` entirely and taking down every plugin in the root,
    # not just the malformed one. This proves both halves: the bad plugin is
    # reported invalid with a clear message, AND its sibling in the same root
    # is still discovered normally -- the sibling assertion is what actually
    # proves the crash no longer escapes.
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    _plugin_toml(bad_dir, "bad")
    (bad_dir / "tool_pack.toml").write_text(
        'connections = ["a"]\n',
        encoding="utf-8",
    )
    guardrails = bad_dir / "guardrails"
    guardrails.mkdir()
    (guardrails / "read_only.toml").write_text(
        """
kind = "preset"
key = "read_only"
label = "x"
label_en = "x"
summary = "x"
summary_en = "x"
read = true
""",
        encoding="utf-8",
    )

    good_dir = tmp_path / "good"
    good_dir.mkdir()
    _plugin_toml(good_dir, "good")

    discovered = {p.plugin_id: p for p in discover_plugins([str(tmp_path)])}
    assert set(discovered) == {"bad", "good"}

    bad = discovered["bad"]
    assert not bad.valid
    assert "connections must be an array of tables" in (bad.error or "")

    good = discovered["good"]
    assert good.valid, good.error
