"""The credential_type catalog: core-registered types (LLM provider
completion keys, Task 14 -- see the bottom-of-file import of
`oc8.credentials.core_types`) plus every enabled Capa's own
`credential_types` (design §2). Mirrors `oc8.knowledge.connectors.registry`'s
core-then-plugin merge shape exactly, for the same reason: a type an
unauthorized tenant's Capa never installed must never be reachable here
either.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from oc8.capas.discovery import DiscoveredPlugin, find_plugin
from oc8.capas.loader import load_plugin
from oc8.capas.manifest import CredentialTypeSpec, parse_manifest
from oc8.knowledge.connectors.registry import enabled_plugin_names

#: A credential_type's owning capa's `i18n/*.po` catalogs, `{locale: {msgid: msgstr}}`.
CredentialTypeI18n = Mapping[str, Mapping[str, str]]


class CredentialTypeNotFound(Exception):
    """No credential_type with this name is reachable for this tenant."""


# Populated by core features that own a credential type directly (LLM
# provider keys, Task 14) -- never a Capa's own contribution, which is
# always read fresh from its manifest below instead. Core types have no
# capas/<capa>/i18n/ folder to translate from.
CORE_CREDENTIAL_TYPES: dict[str, CredentialTypeSpec] = {}


async def _capa_credential_types(
    db: AsyncSession, *, tenant_id: uuid.UUID
) -> dict[str, tuple[CredentialTypeSpec, CredentialTypeI18n]]:
    out: dict[str, tuple[CredentialTypeSpec, CredentialTypeI18n]] = {}
    for name in await enabled_plugin_names(db, tenant_id):
        discovered = find_plugin(name)
        if discovered is None or discovered.manifest is None:
            continue
        if not load_plugin(discovered):
            continue
        manifest = parse_manifest(discovered.manifest)
        for credential_type in manifest.credential_types:
            out[credential_type.name] = (credential_type, discovered.i18n)
    return out


async def find_credential_type_owner(
    db: AsyncSession, *, tenant_id: uuid.UUID, credential_type: str
) -> DiscoveredPlugin | None:
    """The `DiscoveredPlugin` whose manifest declares `credential_type`, or
    `None` when it is core-owned (in `CORE_CREDENTIAL_TYPES`) or not
    reachable by any of this tenant's enabled Capas.

    Walks the same enabled-plugins list as `_capa_credential_types` above,
    but -- unlike that function, which discards the owning plugin once the
    type is merged into its by-name dict -- keeps it. `test_credential`
    (`credentials/service.py`) needs the owning plugin's own folder
    (`DiscoveredPlugin.path`) to resolve a `validate_entry_point` string like
    `"credential:validate_s3"` via `import_entry_point`, exactly the way
    `configure_plugin` (`api/v1/capas.py`) resolves a `PluginSetupSpec`'s
    `validate_entry_point` against ITS OWN already-known plugin. Passing the
    credential_type's own NAME (e.g. `"s3_api"`) to `find_plugin` -- which
    expects a PLUGIN name (e.g. `"s3_source"`) -- is exactly the bug this
    function exists to avoid reintroducing.
    """
    for name in await enabled_plugin_names(db, tenant_id):
        discovered = find_plugin(name)
        if discovered is None or discovered.manifest is None:
            continue
        if not load_plugin(discovered):
            continue
        manifest = parse_manifest(discovered.manifest)
        if any(ct.name == credential_type for ct in manifest.credential_types):
            return discovered
    return None


async def list_credential_types(
    db: AsyncSession, *, tenant_id: uuid.UUID
) -> list[CredentialTypeSpec]:
    """Every credential_type this tenant may actually use, core first."""
    all_types = await _capa_credential_types(db, tenant_id=tenant_id)
    capa_types = {name: pair[0] for name, pair in all_types.items()}
    merged = {**capa_types, **CORE_CREDENTIAL_TYPES}
    return sorted(merged.values(), key=lambda t: t.name)


async def list_credential_types_with_i18n(
    db: AsyncSession, *, tenant_id: uuid.UUID
) -> list[tuple[CredentialTypeSpec, CredentialTypeI18n]]:
    """Same merge as `list_credential_types`, but keeps each type's owning
    capa's `i18n/*.po` catalog alongside it, for a caller that needs to
    resolve `display_name`/field prose into every translated locale (design:
    capa-i18n) -- core-owned types pair with an empty catalog, since they
    have no `capas/<capa>/i18n/` folder to read."""
    capa_types = await _capa_credential_types(db, tenant_id=tenant_id)
    merged: dict[str, tuple[CredentialTypeSpec, CredentialTypeI18n]] = dict(capa_types)
    for name, spec in CORE_CREDENTIAL_TYPES.items():
        merged[name] = (spec, {})
    return sorted(merged.values(), key=lambda pair: pair[0].name)


async def get_credential_type(
    db: AsyncSession, *, tenant_id: uuid.UUID, name: str
) -> CredentialTypeSpec:
    core = CORE_CREDENTIAL_TYPES.get(name)
    if core is not None:
        return core
    capa_types = await _capa_credential_types(db, tenant_id=tenant_id)
    found = capa_types.get(name)
    if found is None:
        raise CredentialTypeNotFound(name)
    return found[0]


# Populates CORE_CREDENTIAL_TYPES as a side effect of import (Task 14) --
# mirrors `oc8.knowledge.connectors.registry`'s own core connectors, which
# are likewise wired at import time (`_UPLOAD`/`_WEBSITE`, imported at the
# top of that module) rather than through a separate startup step. This
# import sits at the BOTTOM of the file, after `CORE_CREDENTIAL_TYPES` is
# defined, because `core_types` itself imports it back from here -- any
# caller of this module (e.g. `oc8.api.v1.credentials`, imported while
# `create_app()` builds `api_router`, well before the first request) is
# enough to guarantee registration has already happened.
from oc8.credentials import core_types as _core_types  # noqa: E402, F401
