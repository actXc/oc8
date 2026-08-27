"""Static third-party OAuth provider registry (tech-spec §11.2)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from oc8.config import get_settings
from oc8.oauth.errors import OAuthError


@dataclass(frozen=True)
class OAuthProvider:
    provider_id: str
    authorize_url: str
    token_url: str
    revoke_url: str | None
    userinfo_url: str | None
    default_scopes: tuple[str, ...]
    extra_authorize_params: Mapping[str, str] = field(default_factory=dict)


GOOGLE = OAuthProvider(
    provider_id="google",
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    revoke_url="https://oauth2.googleapis.com/revoke",
    userinfo_url="https://www.googleapis.com/oauth2/v3/userinfo",
    default_scopes=(
        "openid",
        "email",
        "https://www.googleapis.com/auth/drive.readonly",
    ),
    # access_type=offline + prompt=consent are load-bearing: without both,
    # Google returns no refresh_token on re-authorization and the connection
    # dies unrenewably when the access token expires.
    extra_authorize_params={"access_type": "offline", "prompt": "consent"},
)

MICROSOFT = OAuthProvider(
    provider_id="microsoft",
    # Tenant-scoped: {tenant_id} is filled in per-connection by _post_token,
    # not here -- this provider entry has no one tenant to bind to.
    authorize_url="https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize",
    token_url="https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
    revoke_url=None,
    userinfo_url=None,
    default_scopes=("https://graph.microsoft.com/.default",),
)

_PROVIDERS: dict[str, OAuthProvider] = {p.provider_id: p for p in (GOOGLE, MICROSOFT)}


def get_provider(provider_id: str) -> OAuthProvider:
    try:
        return _PROVIDERS[provider_id]
    except KeyError:
        raise OAuthError(f"unknown oauth provider: {provider_id!r}") from None


def provider_ids() -> list[str]:
    return list(_PROVIDERS)


def redirect_uri_for(provider_id: str) -> str:
    base = get_settings().oauth_redirect_base_url.rstrip("/")
    return f"{base}/api/v1/oauth/{provider_id}/callback"
