"""Host directories a CLI-harness container bind-mounts, created BEFORE the
container starts and owned by the uid the container will run as.

Two separate failures live here, and both were live in all three plugins
until the whole-branch review found them:

1. **Docker auto-creates a missing bind-mount source as ``root:root``.** A
   plugin that names ``{session_root}/{run_id}`` as a mount host_path without
   creating it first gets a root-owned directory, and a container running as
   anything but root then cannot write into its own ``/workspace`` -- which
   defeats the entire "the CLI's native file tools write freely inside the
   container" design decision this whole feature rests on.
2. **A CLI's resumable session transcript lives OUTSIDE ``/workspace``.**
   Claude Code keeps it under ``$CLAUDE_CONFIG_DIR`` (``~/.claude``), Codex
   under ``$CODEX_HOME/sessions``, opencode under
   ``$XDG_DATA_HOME/opencode/storage``. ``driver.teardown()`` destroys the
   container filesystem at every park, so without a persistent HOST directory
   behind those paths a resume leg's ``--resume``/``resume``/``--session``
   points at a session that no longer exists anywhere and the CLI errors out.
   ``nanoclaw_runtime/runtime/session.py``'s ``claude_home_dir`` documents exactly
   this failure (live-observed there, n=2); these plugins mirror its fix.

``ensure_dir`` is what makes ``SandboxSpec.user`` (``settings.sandbox_user``,
a raw HOST uid) and these mounts consistent with each other: bind mounts
carry HOST ownership, so pinning the container's uid is only half the job --
the host directory has to be owned by that same uid or it stays unwritable no
matter what ``--user`` the container runs as. Mirrors
``nanoclaw_runtime/runtime/session.py``'s ``_chown_to_sandbox_user`` (see its
docstring for why the chown is best-effort-and-logged rather than raised: one
path failing to chown must not abort a run, but swallowing it silently would
recreate the half-working state this exists to close).
"""

from __future__ import annotations

import logging
import os

from oc8.config import get_settings

logger = logging.getLogger(__name__)


def chown_to_sandbox_user(path: str) -> None:
    """Hand ``path`` to ``settings.sandbox_user``; a no-op when unset."""
    user = get_settings().sandbox_user
    if not user:
        # The default: containers keep the image's own user, and
        # control-plane-owned files are already fine as they are.
        return
    uid_s, _, gid_s = user.partition(":")
    # Shape (empty, "uid", or "uid:gid" with integer parts) is guaranteed by
    # oc8.config.Settings._validate_sandbox_user, which fails the process at
    # startup rather than raising here, mid-run.
    uid = int(uid_s)
    gid = int(gid_s) if gid_s else -1
    try:
        os.chown(path, uid, gid)
    except OSError:
        # EPERM (not running as root, a restricted capability set, a
        # filesystem that refuses chown) is a real possibility. Logged, not
        # raised: the run can still work when the control plane and the
        # sandbox share a uid anyway, and a silent swallow would put the
        # eventual in-container permission error a long way from its cause.
        logger.warning("could not chown %s to sandbox user %s", path, user, exc_info=True)


def ensure_dir(path: str) -> str:
    """``mkdir -p`` a host directory the container will mount, and return it."""
    os.makedirs(path, exist_ok=True)
    chown_to_sandbox_user(path)
    return path


def write_private_file(path: str, content: str) -> str:
    """Write a config file only the sandbox uid may read, and return its path.

    Every one of these files carries the run's bearer JWT (opencode's
    ``opencode.jsonc`` embeds it directly; Codex's ``config.toml`` names the
    env var holding it). They are written to a host path that outlives the
    container, so the default 0644 would leave a live credential readable by
    every account on the host for as long as the session root is kept. 0600
    plus the chown is what keeps it readable by exactly the container that
    needs it -- the chown is not optional here: a 0600 file still owned by the
    control plane's uid is one the sandbox uid cannot read at all, which would
    turn a hardening step into a broken run.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    chown_to_sandbox_user(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.chmod(path, 0o600)
    chown_to_sandbox_user(path)
    return path
