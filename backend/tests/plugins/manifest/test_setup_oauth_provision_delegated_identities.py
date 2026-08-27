# backend/tests/plugins/manifest/test_setup_oauth_provision_delegated_identities.py
from __future__ import annotations

from oc8.capas.manifest import SetupOAuthProvision


def test_setup_oauth_provision_accepts_delegated_identity_fields() -> None:
    spec = SetupOAuthProvision(
        provider="google",
        delegated_identities_field="delegated_mailboxes",
        delegated_identities_env="GOOGLE_DELEGATED_MAILBOXES",
        delegated_token_env_prefix="GOOGLE_DELEGATED_TOKEN",
    )
    assert spec.delegated_identities_field == "delegated_mailboxes"


def test_source_ids_config_key_defaults_to_site_ids_for_microsoft365() -> None:
    """Microsoft 365's manifest declares no `source_ids_config_key`, so it must
    keep writing under the same `siteIds` key it always has -- this is the
    regression the Task 12 brief calls out explicitly: fixing `google_workspace`'s
    `sharedDriveIds` key must not silently change what microsoft365 writes."""
    spec = SetupOAuthProvision(provider="microsoft")
    assert spec.source_ids_config_key == "siteIds"


def test_build_delegated_identity_config_writes_indexed_secret_env_entries() -> None:
    from oc8.api.v1.capas import _build_delegated_identity_config

    provision = SetupOAuthProvision(
        provider="google",
        delegated_identities_field="delegated_mailboxes",
        delegated_identities_env="GOOGLE_DELEGATED_MAILBOXES",
        delegated_token_env_prefix="GOOGLE_DELEGATED_TOKEN",
    )
    env, secret_env = _build_delegated_identity_config(
        provision, values={"delegated_mailboxes": "a@company.com, b@company.com"}
    )
    assert env == {"GOOGLE_DELEGATED_MAILBOXES": "a@company.com,b@company.com"}
    assert secret_env == {
        "GOOGLE_DELEGATED_TOKEN_0": "oauth-delegated:a@company.com",
        "GOOGLE_DELEGATED_TOKEN_1": "oauth-delegated:b@company.com",
    }


def test_build_delegated_identity_config_with_no_identities_is_empty() -> None:
    from oc8.api.v1.capas import _build_delegated_identity_config

    provision = SetupOAuthProvision(
        provider="google",
        delegated_identities_field="delegated_mailboxes",
        delegated_identities_env="GOOGLE_DELEGATED_MAILBOXES",
        delegated_token_env_prefix="GOOGLE_DELEGATED_TOKEN",
    )
    env, secret_env = _build_delegated_identity_config(
        provision, values={"delegated_mailboxes": ""}
    )
    assert env == {}
    assert secret_env == {}


def test_build_delegated_identity_config_is_a_no_op_without_the_field_declared() -> None:
    from oc8.api.v1.capas import _build_delegated_identity_config

    provision = SetupOAuthProvision(provider="microsoft")  # no delegated_identities_field at all
    env, secret_env = _build_delegated_identity_config(provision, values={})
    assert env == {}
    assert secret_env == {}
