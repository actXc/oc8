"""OpenAI "Sign in with ChatGPT" device-code OAuth parameters (design §1,
ChatGPT subscription auth spec). These are OpenAI/Codex-CLI-specific
constants -- deliberately kept out of oc8.oauth.providers, which is built
around the browser-redirect authorization_code shape this device flow does
not use.

Sourced from openai/codex's own public implementation, read at commit
7625bd56657da7ce6d96b6d27e983e568757cdbc (2026-08-26, ~rust-v0.149.1):

  - codex-rs/login/src/device_code_auth.rs   -- the whole device flow
  - codex-rs/login/src/server.rs             -- DEFAULT_ISSUER, scope list,
                                                exchange_code_for_tokens()
  - codex-rs/login/src/auth/manager.rs       -- CLIENT_ID, REFRESH_TOKEN_URL
  - codex-rs/login/src/token_data.rs         -- id_token claim shape
  - codex-rs/model-provider-info/src/lib.rs  -- CHATGPT_CODEX_BASE_URL
  - codex-rs/login/tests/suite/device_code_login.rs -- wire shapes, asserted

Cross-checked against two independent reimplementations:
  - tumf/opencode-openai-device-auth, src/index.ts (TypeScript) -- identical
    CLIENT_ID, issuer, both /deviceauth paths, and verification URL.
  - openclaw/openclaw, extensions/openai/base-url.ts and
    extensions/openai/openai-chatgpt-oauth-authorization.runtime.ts --
    identical backend base URL; a narrower scope set (see DEFAULT_SCOPES).

WARNING -- this is NOT an RFC 8628 device flow. Codex implements a bespoke,
undocumented OpenAI flow and the differences are load-bearing for every
caller of these constants:

1. Two device endpoints, not one. DEVICE_AUTHORIZATION_URL starts the flow;
   polling happens at a *different* URL, DEVICE_POLL_URL
   (``https://auth.openai.com/api/accounts/deviceauth/token``). Not part of
   this module's original five-constant interface -- added once the poller
   (`oauth/device_flow.py`) actually needed it.
2. Both device endpoints take **JSON** bodies, not form encoding. Start
   sends ``{"client_id": ...}`` only -- no ``scope``, no PKCE. Poll sends
   ``{"device_auth_id": ..., "user_code": ...}``.
3. The start response is ``{"device_auth_id", "user_code", "interval"}``.
   There is no ``device_code``, no ``verification_uri``, no
   ``verification_uri_complete``, no ``expires_in``. ``interval`` arrives as
   a *string*. The verification URL is built client-side as
   ``https://auth.openai.com/codex/device`` and the 15-minute expiry is a
   client-side constant.
4. Polling signals "keep waiting" with HTTP **403 or 404**, not with an
   ``authorization_pending`` error body. Any other non-2xx is fatal.
5. A successful poll does not return tokens. It returns
   ``{"authorization_code", "code_challenge", "code_verifier"}`` -- the
   server generates the PKCE pair for you. The caller then performs an
   ordinary PKCE authorization_code exchange against TOKEN_URL, form-encoded
   (``grant_type=authorization_code&code=&redirect_uri=&client_id=
   &code_verifier=``) with
   ``redirect_uri=https://auth.openai.com/deviceauth/callback``.
6. Refresh is form-encoded nowhere: it POSTs **JSON**
   ``{"client_id", "grant_type": "refresh_token", "refresh_token"}`` to
   TOKEN_URL. Refresh tokens **rotate** -- reuse is rejected with
   ``refresh_token_reused`` and is terminal, so the new refresh_token must be
   persisted on every refresh.
7. No client_secret anywhere. Confirmed public client (device start, code
   exchange and refresh all send client_id alone).
8. Requests to CHATGPT_BACKEND_BASE_URL require a ``ChatGPT-Account-ID``
   header whose value is the ``chatgpt_account_id`` claim inside the
   ``https://api.openai.com/auth`` namespace of the returned id_token JWT.
   Codex also sends an ``originator`` header (default ``codex_cli_rs``).
9. CHATGPT_BACKEND_BASE_URL speaks the **Responses API** only. Codex removed
   ``wire_api = "chat"`` support entirely (see CHAT_WIRE_API_REMOVED_ERROR in
   model-provider-info), and openclaw names the same URL
   OPENAI_CODEX_RESPONSES_BASE_URL. Do not assume /v1/chat/completions works.
10. Device-code login is off by default: the end user must first enable it
    under ChatGPT security settings (personal) or workspace permissions
    (admin), per learn.chatgpt.com/docs/auth.md. A disabled account gets a
    404 from DEVICE_AUTHORIZATION_URL.
"""

from __future__ import annotations

# codex-rs/login/src/auth/manager.rs:1708 -- `pub const CLIENT_ID`.
# Byte-identical in tumf/opencode-openai-device-auth src/index.ts:19.
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

# codex-rs/login/src/device_code_auth.rs:68 (`{auth_base_url}/deviceauth/usercode`)
# over api_base_url `{issuer}/api/accounts` (line 171), issuer defaulting to
# DEFAULT_ISSUER = "https://auth.openai.com" (server.rs:59).
# NOT an RFC 8628 device_authorization_endpoint -- see quirks 1-4 above.
DEVICE_AUTHORIZATION_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"

# codex-rs/login/src/device_code_auth.rs -- the poll endpoint, distinct from
# DEVICE_AUTHORIZATION_URL. See this module's own docstring, quirk 1.
DEVICE_POLL_URL = "https://auth.openai.com/api/accounts/deviceauth/token"

# codex-rs/login/src/server.rs:827 (`{issuer}/oauth/token`) and
# codex-rs/login/src/auth/manager.rs:197 REFRESH_TOKEN_URL, which agree.
# Serves both the authorization_code exchange and the refresh_token grant --
# but with different body encodings; see quirks 5 and 6 above.
TOKEN_URL = "https://auth.openai.com/oauth/token"

# codex-rs/model-provider-info/src/lib.rs:40 CHATGPT_CODEX_BASE_URL, selected
# as the default base_url for every ChatGPT-ish AuthMode (same file, :303).
# Responses API only -- see quirk 9.
CHATGPT_BACKEND_BASE_URL = "https://chatgpt.com/backend-api/codex"

# codex-rs/login/src/server.rs:590 -- the scope string Codex puts on its
# browser authorize URL. Recorded here for the browser/PKCE path and as the
# scope set OpenAI actually grants Codex; the device-code start request sends
# no scope at all (quirk 2), so this is not submitted anywhere in that flow.
# openclaw requests only the first four for the same provider, so the
# api.connectors.* pair is evidently optional in practice.
DEFAULT_SCOPES: tuple[str, ...] = (
    "openid",
    "profile",
    "email",
    "offline_access",
    "api.connectors.read",
    "api.connectors.invoke",
)
