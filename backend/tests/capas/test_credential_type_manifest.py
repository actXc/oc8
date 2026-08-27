from __future__ import annotations

import pytest

from oc8.capas.manifest import CredentialTypeSpec, SetupFieldSpec, parse_manifest


def test_credential_type_spec_parses_its_fields() -> None:
    spec = CredentialTypeSpec.model_validate(
        {
            "name": "s3_api",
            "display_name": "S3 / Object storage",
            "fields": [
                {"key": "region", "label": "Region", "kind": "text", "default": "us-east-1"},
                {"key": "access_key", "label": "Access key", "kind": "password"},
            ],
            "validate_entry_point": "credential:validate_s3",
        }
    )
    assert spec.name == "s3_api"
    assert [f.key for f in spec.fields] == ["region", "access_key"]


def test_setup_field_credential_kind_requires_credential_type() -> None:
    with pytest.raises(Exception):  # noqa: B017 -- pydantic ValidationError
        SetupFieldSpec.model_validate(
            {"key": "bot_token", "label": "Bot token", "kind": "credential"}
        )


def test_setup_field_credential_kind_with_type_is_valid() -> None:
    field = SetupFieldSpec.model_validate(
        {
            "key": "bot_token",
            "label": "Bot token",
            "kind": "credential",
            "credential_type": "telegram_bot",
        }
    )
    assert field.credential_type == "telegram_bot"


def test_setup_field_non_credential_kind_rejects_credential_type() -> None:
    with pytest.raises(Exception):  # noqa: B017 -- pydantic ValidationError
        SetupFieldSpec.model_validate(
            {"key": "x", "label": "X", "kind": "text", "credential_type": "foo"}
        )


def test_credential_type_spec_rejects_a_nested_credential_field() -> None:
    with pytest.raises(Exception):  # noqa: B017 -- pydantic ValidationError
        CredentialTypeSpec.model_validate(
            {
                "name": "outer",
                "display_name": "Outer",
                "fields": [
                    {
                        "key": "inner",
                        "label": "Inner",
                        "kind": "credential",
                        "credential_type": "some_other_type",
                    }
                ],
            }
        )


def test_manifest_carries_credential_types() -> None:
    manifest = parse_manifest(
        {
            "name": "s3_source",
            "version": "1.0.0",
            "type": "connector",
            "credential_types": [
                {
                    "name": "s3_api",
                    "display_name": "S3 / Object storage",
                    "fields": [{"key": "access_key", "label": "Access key", "kind": "password"}],
                }
            ],
        }
    )
    assert manifest.credential_types[0].name == "s3_api"
