from __future__ import annotations

import pytest

from oc8.capas.manifest import ManifestError, parse_manifest


def test_parses_v2_fields() -> None:
    mf = parse_manifest(
        {
            "name": "acme.crm",
            "version": "1.2.0",
            "type": "core_extension",
            "core_compat": ">=1.0,<2.0",
            "trust": "community",
            "permissions": ["hooks:task.before_create", "api:tasks.read"],
            "capabilities": ["streaming", "concurrent_tasks:3"],
            "provides": ["crm"],
            "entry_points": {"backend": "acme_crm:setup"},
            "handles": [{"point": "task.before_create", "priority": 10}],
        }
    )
    assert mf.type == "core_extension"
    assert mf.handles[0].point == "task.before_create"
    assert mf.handles[0].priority == 10
    assert mf.handles[0].replace is False


def test_backward_compatible_agent_module() -> None:
    mf = parse_manifest({"name": "legacy.mod", "version": "0.1.0"})
    assert mf.type == "agent_template"  # default keeps old manifests valid
    assert mf.trust == "first_party"
    assert mf.agent_template is None


def test_agent_template_section_parses() -> None:
    mf = parse_manifest(
        {
            "name": "finance_bookkeeper",
            "version": "1.0.0",
            "type": "agent_template",
            "agent_template": {
                "name": "Dana",
                "role_title": "Bookkeeper & Controller",
                "mission": "Keep the books accurate.",
                "persona": "# Dana",
                "skills": ["Month-end close"],
                "max_steps": 24,
            },
        }
    )
    assert mf.agent_template is not None
    assert mf.agent_template.name == "Dana"
    assert mf.agent_template.max_steps == 24
    assert mf.agent_template.skills == ["Month-end close"]


def test_rejects_unknown_field() -> None:
    with pytest.raises(ManifestError):
        parse_manifest({"name": "x", "version": "1.0.0", "bogus": 1})


def test_a_setup_contract_needs_no_mcp_block() -> None:
    """approval_channel plugins (telegram_approvals, whatsapp_approvals) have a
    setup form but nothing to materialise as an McpConnection -- 0009's design
    required `mcp` on every setup contract; this is the field that stopped it
    from being mandatory."""
    mf = parse_manifest(
        {
            "name": "acme.channel",
            "version": "1.0.0",
            "type": "approval_channel",
            "setup": {
                "title": "Connect",
                "fields": [
                    {
                        "key": "token",
                        "label": "Token",
                        "kind": "password",
                        "secret_ref": "acme/channel-token",
                    }
                ],
                "validate_entry_point": "acme_channel.setup:validate",
            },
        }
    )
    assert mf.setup is not None
    assert mf.setup.mcp is None
    assert mf.setup.validate_entry_point == "acme_channel.setup:validate"
    assert mf.setup.fields[0].secret_ref == "acme/channel-token"


def test_oauth_provision_without_an_mcp_block_is_refused_at_parse_time() -> None:
    """`configure_plugin` returns early into `_configure_without_connection`
    when `setup.mcp is None`, and the `oauth_provision` block sits after that
    return. A connector-only manifest -- a RAG-only Google Workspace variant is
    the concrete next one -- declaring `oauth_provision` alone would get HTTP
    200, no OAuthConnection, no DataSource and no error whatsoever.

    Refused here rather than restructuring the endpoint's control flow around a
    combination nothing ships: the failure is then loud, at the moment the
    manifest author can fix it, and the error says which block is missing."""
    with pytest.raises(ManifestError) as exc:
        parse_manifest(
            {
                "name": "acme.rag",
                "version": "1.0.0",
                "type": "connector",
                "setup": {
                    "title": "Connect",
                    "fields": [{"key": "client_id", "label": "Client ID"}],
                    "oauth_provision": {"provider": "microsoft"},
                },
            }
        )
    assert "oauth_provision" in str(exc.value)
    assert "mcp" in str(exc.value)


def test_oauth_provision_alongside_an_mcp_block_parses() -> None:
    mf = parse_manifest(
        {
            "name": "acme.pack",
            "version": "1.0.0",
            "type": "tool_pack",
            "setup": {
                "title": "Connect",
                "fields": [{"key": "client_id", "label": "Client ID"}],
                "mcp": {"name": "acme", "command": "python", "department_field": "department"},
                "oauth_provision": {"provider": "microsoft"},
            },
        }
    )
    assert mf.setup is not None
    assert mf.setup.oauth_provision is not None
    assert mf.setup.mcp is not None


def test_plugin_manifest_label_defaults_to_empty_string() -> None:
    mf = parse_manifest(
        {
            "name": "nanoclaw_runtime",
            "type": "runtime_adapter",
            "version": "1.0.0",
        }
    )
    assert mf.label == ""


def test_plugin_manifest_label_parses_when_present() -> None:
    mf = parse_manifest(
        {
            "name": "nanoclaw_runtime",
            "type": "runtime_adapter",
            "version": "1.0.0",
            "label": "nanoclaw",
        }
    )
    assert mf.label == "nanoclaw"


def _connection(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "key": "primary",
        "name": "svc",
        "server_url": "",
        "transport": "stdio",
    }
    base.update(overrides)
    return base


def _tool_pack_manifest(connection: dict[str, object]) -> dict[str, object]:
    return {
        "name": "demo",
        "version": "1.0.0",
        "type": "tool_pack",
        "tool_pack": {"connections": [connection]},
    }


def test_manifest_without_presets_still_parses() -> None:
    mf = parse_manifest(_tool_pack_manifest(_connection()))
    assert mf.tool_pack is not None
    assert mf.tool_pack.connections[0].guardrail_presets == []


def test_a_well_formed_preset_parses() -> None:
    preset = {
        "key": "read_only",
        "label": "Read only",
        "summary": "Can search.",
        "recommended": True,
        "read": True,
    }
    mf = parse_manifest(_tool_pack_manifest(_connection(guardrail_presets=[preset])))
    assert mf.tool_pack is not None
    got = mf.tool_pack.connections[0].guardrail_presets[0]
    assert got.key == "read_only"
    assert got.recommended is True
    assert got.approval_eur is None


def test_two_recommended_presets_fail_naming_both_keys() -> None:
    p1 = {
        "key": "a",
        "label": "A",
        "summary": "s",
        "recommended": True,
    }
    p2 = {
        "key": "b",
        "label": "B",
        "summary": "s",
        "recommended": True,
    }
    with pytest.raises(ManifestError) as exc:
        parse_manifest(_tool_pack_manifest(_connection(guardrail_presets=[p1, p2])))
    assert "a" in str(exc.value) and "b" in str(exc.value)


def test_duplicate_preset_keys_fail() -> None:
    p1 = {"key": "dup", "label": "A", "summary": "s"}
    p2 = {"key": "dup", "label": "B", "summary": "s"}
    with pytest.raises(ManifestError) as exc:
        parse_manifest(_tool_pack_manifest(_connection(guardrail_presets=[p1, p2])))
    assert "dup" in str(exc.value)


def test_unknown_approval_action_fails_naming_it() -> None:
    preset = {
        "key": "bad",
        "label": "x",
        "summary": "s",
        "approval_actions": ["delete"],
    }
    with pytest.raises(ManifestError) as exc:
        parse_manifest(_tool_pack_manifest(_connection(guardrail_presets=[preset])))
    assert "delete" in str(exc.value)


def test_non_numeric_approval_eur_fails() -> None:
    preset = {
        "key": "bad",
        "label": "x",
        "summary": "s",
        "approval_eur": "lots",
    }
    with pytest.raises(ManifestError):
        parse_manifest(_tool_pack_manifest(_connection(guardrail_presets=[preset])))


def test_empty_string_approval_eur_means_no_threshold() -> None:
    preset = {
        "key": "ok",
        "label": "x",
        "summary": "s",
        "approval_eur": "",
    }
    mf = parse_manifest(_tool_pack_manifest(_connection(guardrail_presets=[preset])))
    assert mf.tool_pack is not None
    assert mf.tool_pack.connections[0].guardrail_presets[0].approval_eur is None


def test_approval_eur_dropped_with_a_warning_when_connection_has_no_value_spec() -> None:
    preset = {
        "key": "ok",
        "label": "x",
        "summary": "s",
        "approval_eur": 1000,
    }
    mf = parse_manifest(
        _tool_pack_manifest(_connection(guardrail_presets=[preset]))
    )  # no config.value_spec
    assert mf.tool_pack is not None
    assert mf.tool_pack.connections[0].guardrail_presets[0].approval_eur is None


def test_approval_eur_kept_when_connection_has_value_spec() -> None:
    preset = {
        "key": "ok",
        "label": "x",
        "summary": "s",
        "approval_eur": 1000,
    }
    conn = _connection(
        guardrail_presets=[preset], config={"value_spec": {"direct_fields": ["amount"]}}
    )
    mf = parse_manifest(_tool_pack_manifest(conn))
    assert mf.tool_pack is not None
    assert mf.tool_pack.connections[0].guardrail_presets[0].approval_eur == 1000


def test_a_zero_threshold_survives_the_empty_string_normalisation() -> None:
    """`approval_eur = 0` means "a human decides every send", not "no threshold".

    TOML has no null, so the manifests spell "no threshold" as `""` and a
    validator maps it to None. Zero is falsy too, and a normalisation written
    as `if not v` instead of `if v == ""` would silently turn the strictest
    setting in the product into the absence of one. This is the guard on that
    exact line.
    """
    preset = {
        "key": "every_send",
        "label": "Every send",
        "summary": "A human decides every send.",
        "approval_eur": 0,
    }
    conn = _connection(
        guardrail_presets=[preset], config={"value_spec": {"direct_fields": ["amount"]}}
    )
    mf = parse_manifest(_tool_pack_manifest(conn))
    assert mf.tool_pack is not None
    assert mf.tool_pack.connections[0].guardrail_presets[0].approval_eur == 0
