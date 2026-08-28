"""Which approval channels one tenant actually has, built ready to use (§5.6).

Three things have to line up before a channel exists for a tenant: its plugin is
installed AND enabled here, its code was importable (trust, §13), and the
credential it needs resolves. Any of those missing means no channel — and that
must be a quiet nothing, never an error: an approval still has to be raised when
a bot is misconfigured, because the inbox is the record and is always there.

Secrets are resolved HERE rather than by the plugin. A plugin that could reach
the secret store could reach another plugin's credentials, so core reads the
value and hands it over as plain config.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8.channels.base import ApprovalChannel

logger = logging.getLogger(__name__)

#: What a plugin's `[plugin.config]` may name as the secret holding its
#: credential. The VALUE is resolved by core and passed as `bot_token`-style
#: plain config; the reference itself never leaves this module.
SECRET_REF = "secret_ref"


async def channels_for_tenant(
    db: AsyncSession, *, tenant_id: uuid.UUID
) -> dict[str, ApprovalChannel]:
    """Every usable approval channel for this tenant, keyed by channel id.

    Never raises. A channel that cannot be built is logged and left out, because
    the caller is in the middle of raising an approval and nothing about a chat
    bot may interfere with that.
    """
    from oc8.capas.contributions import channels_for
    from oc8.capas.discovery import find_plugin
    from oc8.capas.loader import load_plugin
    from oc8.models import Capa, CapaInstallation, CapaVersion

    try:
        rows = (
            await db.execute(
                select(Capa.name, CapaVersion.manifest, CapaInstallation.config)
                .join(CapaVersion, CapaVersion.capa_id == Capa.id)
                .join(CapaInstallation, CapaInstallation.version_id == CapaVersion.id)
                .where(
                    CapaInstallation.status == "enabled",
                    Capa.type == "approval_channel",
                )
            )
        ).all()
    except Exception:
        logger.warning("could not read approval channels for %s", tenant_id, exc_info=True)
        return {}

    built: dict[str, ApprovalChannel] = {}
    for plugin_name, manifest, installation_config in rows:
        # `channels_for` reads an in-process, load-triggered catalogue
        # (`capas.contributions`, populated by the plugin's own `register()`
        # entry point) -- it is empty until something in THIS process has
        # loaded the plugin at least once. Every other *_for(plugin_name)
        # registry in this codebase (runtime, modelrouter, credentials,
        # knowledge.connectors) calls load_plugin itself for exactly this
        # reason; this one had not, which worked by accident in the
        # `backend`/`worker` processes (something else always happened to
        # load an enabled plugin first) and failed silently in `scheduler`
        # the moment oc8.channels.poll became the first caller there.
        discovered = find_plugin(str(plugin_name))
        if discovered is None or not load_plugin(discovered):
            logger.info(
                "approval channel plugin %s is enabled but could not be loaded", plugin_name
            )
            continue
        factories = channels_for(str(plugin_name))
        if not factories:
            # Installed and enabled, but its code never loaded -- untrusted, or
            # quarantined after a failed import. Worth a line: from the outside
            # this looks like a channel that simply never delivers.
            logger.info(
                "approval channel plugin %s is enabled but contributed nothing", plugin_name
            )
            continue
        config = await _resolved_config(
            db,
            tenant_id=tenant_id,
            manifest=manifest or {},
            installation_config=installation_config or {},
        )
        for channel_id, factory in factories.items():
            try:
                built[channel_id] = factory(config)
            except Exception:
                logger.warning(
                    "approval channel %s could not be built for %s",
                    channel_id,
                    tenant_id,
                    exc_info=True,
                )
    return built


async def _resolved_config(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    manifest: dict[str, Any],
    installation_config: dict[str, Any],
) -> dict[str, str]:
    """The plugin's own config, with its secret reference turned into a value.

    The resolved credential is named `bot_token` for every channel: a plugin
    declares WHERE its secret lives, not what core should call it, and one name
    keeps core from growing a per-messenger vocabulary it was designed not to
    have.

    `installation_config` -- values a tenant supplied through the setup wizard
    (§13, CapaInstallation.config) -- wins over the manifest's own static
    `[plugin.config]`: the manifest is one shared file on disk, the wizard is
    what makes a value like a WhatsApp phone number different per tenant.
    """
    manifest_config = manifest.get("config")
    raw = {
        **(manifest_config if isinstance(manifest_config, dict) else {}),
        **installation_config,
    }
    config: dict[str, str] = {str(k): str(v) for k, v in raw.items() if v is not None}

    ref = config.pop(SECRET_REF, "")
    if not ref:
        return config
    try:
        from oc8.secrets.service import resolve_secret

        config["bot_token"] = await resolve_secret(db, tenant_id=tenant_id, ref=ref)
    except Exception:
        # Left absent rather than empty: the factory refuses to build a channel
        # without a credential, which is what should happen -- an approval
        # quietly not delivered is worse than one that visibly has no channel.
        logger.warning("secret %r for an approval channel did not resolve", ref, exc_info=True)
    return config
