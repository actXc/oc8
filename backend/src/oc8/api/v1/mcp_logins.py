"""Create/list a login: a Credential paired 1:1 with a tenant-global
McpConnection (agent tool login selection design). Distinct from
`api/v1/mcp.py`'s existing department-scoped connection CRUD, which stays
untouched for the OAuth/legacy tool packs that still use it."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from oc8 import models as m
from oc8.api.deps import CurrentPrincipal, DbSession, require_permission
from oc8.api.v1.mcp import _manifest_connection
from oc8.authz.permissions import INTEGRATION, MANAGE, VIEW, perm
from oc8.credentials.registry import CredentialTypeNotFound, get_credential_type
from oc8.credentials.service import CredentialNotFound, create_credential, get_credential
from oc8.schemas.dto import McpLoginDTO
from oc8.schemas.requests import CreateMcpLoginRequest
from oc8.secrets.keyprovider import SecretStoreUnavailable

router = APIRouter()


def _to_dto(c: m.McpConnection) -> McpLoginDTO:
    return McpLoginDTO(
        id=str(c.id),
        name=c.name,
        credential_id=str(c.credential_id),
        department_id=str(c.department_id) if c.department_id else None,
        connected=c.connected,
        scopes=c.scopes,
        health=c.health or {},
    )


@router.post(
    "/mcp/logins",
    response_model=McpLoginDTO,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perm(INTEGRATION, MANAGE)))],
)
async def create_login(
    body: CreateMcpLoginRequest, db: DbSession, principal: CurrentPrincipal
) -> McpLoginDTO:
    try:
        cred_type = await get_credential_type(
            db, tenant_id=principal.tenant_id, name=body.credential_type
        )
    except CredentialTypeNotFound as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"unknown credential type: {exc}"
        ) from exc
    # Only conflict with another LOGIN-backed connection (credential_id IS NOT
    # NULL) -- an existing, untouched OAuth/department-scoped connection may
    # coincidentally share this name (a different, older mechanism this plan
    # doesn't touch) and must not block a login from being created. Checked
    # before create_credential runs, so a name collision never leaves behind
    # an orphaned Credential row with no paired connection. The DB has no
    # UniqueConstraint enforcing this (unlike Credential's own
    # uq_credential_tenant_name) because McpConnection is shared with the
    # pre-existing, unconstrained OAuth flow -- this check is what a later
    # by-name lookup (e.g. agents_write.py's login-backed connection
    # resolution, Task 4) depends on to stay a single-row query instead of
    # raising MultipleResultsFound.
    existing = (
        await db.execute(
            select(m.McpConnection.id).where(
                m.McpConnection.tenant_id == principal.tenant_id,
                m.McpConnection.name == body.name,
                m.McpConnection.credential_id.is_not(None),
            )
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"a login named {body.name!r} already exists for this tenant"
        )
    if body.credential_id is not None:
        # Reuse a credential that already exists -- e.g. one a capa's own
        # setup form created -- instead of asking the operator to retype
        # values already sitting in the credential store.
        try:
            cred = await get_credential(
                db, tenant_id=principal.tenant_id, credential_id=body.credential_id
            )
        except CredentialNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such credential") from exc
        if cred.credential_type != body.credential_type:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"credential {cred.name!r} is a {cred.credential_type!r}, "
                f"not a {body.credential_type!r}",
            )
    else:
        try:
            cred = await create_credential(
                db,
                tenant_id=principal.tenant_id,
                name=body.name,
                credential_type=body.credential_type,
                field_values=body.field_values,
            )
        except SecretStoreUnavailable as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        await db.flush()
    # A login is credential storage, not a spawn recipe -- the command/args/
    # focus_spec/etc. a real MCP server needs to start still only exist on
    # the manifest-materialised connection sharing this NAME (the same
    # by-name convention `_resolve_mcp_connection` and `set_department_
    # tools`'s cascade already use). Without this, a pinned login's own
    # bare `config` (env/secret_env only) reached the runtime as `cfg.get(
    # "command", "")` -> "" -> a subprocess spawn on an empty path, which
    # surfaces as `PermissionError(13, 'Permission denied')` with no hint
    # of the real cause (live bug report). `credential_id.is_(None)` picks
    # the manifest row, never another login sharing this same tenant-global
    # name.
    source_conn = (
        await db.execute(
            select(m.McpConnection)
            .where(
                m.McpConnection.tenant_id == principal.tenant_id,
                m.McpConnection.name == body.name,
                m.McpConnection.credential_id.is_(None),
            )
            .order_by(m.McpConnection.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    source_cfg = source_conn.config if source_conn and isinstance(source_conn.config, dict) else {}

    # ENV_VAR_NAME -> credential field key (ToolPackConnection.
    # credential_env_fields, e.g. odoo_mcp's {"ODOO_URL": "url", ...}) --
    # only resolvable off the manifest object itself, since materialise.py
    # never copies this field onto the row's own `config`. Inverted below to
    # field key -> list of ENV_VAR_NAMEs, the direction the loop needs.
    #
    # A list, not a single name: odoo_mcp maps BOTH `ODOO_PASSWORD` and
    # `ODOO_API_KEY` to the same `password` field (mcp-server-odoo treats
    # them as two different auth modes to try, not two names for one var --
    # see tool_pack.toml's own comment). A `{v: k for k, v in ...}` inversion
    # silently keeps only the LAST env name written for a given field and
    # drops the rest -- live-observed 2026-08-26: a login created after that
    # mapping was added got ODOO_API_KEY only, never ODOO_PASSWORD, because
    # dict inversion collapsed the two entries into one.
    credential_env_fields: dict[str, str] = {}
    if source_conn is not None:
        resolved = _manifest_connection(source_conn)
        if resolved is not None:
            credential_env_fields = resolved[0].credential_env_fields
    field_key_to_env_names: dict[str, list[str]] = {}
    for env_name, field_key in credential_env_fields.items():
        field_key_to_env_names.setdefault(field_key, []).append(env_name)

    # Derive config["secret_env"]/["env"] from the credential's OWN stored
    # refs/values -- resolve_mcp_env's existing per-entry loop (agent/mcp_env.py)
    # already knows how to resolve a plain secret-store ref the same way
    # resolve_credential_field does (both ultimately call
    # oc8.secrets.service.resolve_secret), so no change to that resolution
    # code is needed, only to what gets written here. A field with no entry
    # in credential_env_fields (or no matching manifest connection at all)
    # falls back to its own key, so a login with no matching manifest
    # connection (credential_type registered but not yet wired to any tool
    # pack) still gets a usable Credential + connection pair.
    secret_env: dict[str, str] = {}
    env: dict[str, str] = {}
    secret_field_keys = {f.key for f in cred_type.fields if f.kind == "password"}
    for field in cred_type.fields:
        target_keys = field_key_to_env_names.get(field.key, [field.key])
        if field.key in secret_field_keys:
            ref = cred.secret_refs.get(field.key)
            if ref:
                for target_key in target_keys:
                    secret_env[target_key] = ref
        else:
            value = cred.field_values.get(field.key)
            if value:
                for target_key in target_keys:
                    env[target_key] = str(value)
    # Everything from the manifest connection's own config EXCEPT env/
    # secret_env (those are this login's credential, not the shared
    # plugin's demo/default values) -- command, args, _plugin_name,
    # focus_spec, value_spec, outward_tools, _connection_key, all carried
    # over so the runtime can actually start this server.
    config = {k: v for k, v in source_cfg.items() if k not in ("env", "secret_env")}
    config["env"] = env
    config["secret_env"] = secret_env
    conn = m.McpConnection(
        tenant_id=principal.tenant_id,
        credential_id=cred.id,
        name=body.name,
        server_url="",
        transport="stdio",
        scopes=body.scopes,
        config=config,
        connected=False,
    )
    db.add(conn)
    await db.flush()
    dto = _to_dto(conn)
    await db.commit()
    return dto


@router.get(
    "/mcp/logins",
    response_model=list[McpLoginDTO],
    dependencies=[Depends(require_permission(perm(INTEGRATION, VIEW)))],
)
async def list_logins(
    db: DbSession,
    principal: CurrentPrincipal,
    credential_type: str | None = Query(default=None, alias="credentialType"),
) -> list[McpLoginDTO]:
    stmt = select(m.McpConnection).where(
        m.McpConnection.tenant_id == principal.tenant_id,
        m.McpConnection.credential_id.is_not(None),
    )
    rows = (await db.execute(stmt.order_by(m.McpConnection.name))).scalars().all()
    if credential_type is not None:
        cred_ids = set(
            (
                await db.execute(
                    select(m.Credential.id).where(
                        m.Credential.tenant_id == principal.tenant_id,
                        m.Credential.credential_type == credential_type,
                    )
                )
            )
            .scalars()
            .all()
        )
        rows = [r for r in rows if r.credential_id in cred_ids]
    return [_to_dto(r) for r in rows]
