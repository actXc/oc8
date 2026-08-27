"""Import a trusted plugin's entry point and let it contribute (§13).

What this does: for a plugin folder discovered on disk, import the module named
by ``entry_points`` and call its ``register(contrib)``, so the plugin can add
real implementations (a connector, today).

What it deliberately does NOT do:

* **Import untrusted code.** Only ``first_party``/``verified`` plugins are
  imported. Running community code safely needs the §8.5 isolation that does
  not exist yet; putting a folder in the plugins path IS the trust decision,
  exactly as in Odoo's addons model.
* **Decide who may use the result.** Loading is process-global; entitlement is
  per tenant and lives in the resolver. See ``oc8.capas.contributions``.
* **Let a bad plugin take the process down.** Any failure quarantines that
  plugin id in-process and is logged; the caller gets ``False``.

Quarantine here is process-local and is about *importability*, which is a
property of the code, not of a tenant. It is distinct from the per-tenant
circuit breaker in ``oc8.capas.lifecycle``, which quarantines an
*installation* after repeated runtime failures.
"""

from __future__ import annotations

import importlib
import logging
import sys
from typing import TYPE_CHECKING

from oc8.capas.contributions import PluginContributions, record

if TYPE_CHECKING:
    from oc8.capas.discovery import DiscoveredPlugin

logger = logging.getLogger(__name__)

TRUSTED = frozenset({"first_party", "verified"})

# plugin_id -> loaded successfully?  A cached False means "quarantined": the
# import is not retried, so a broken plugin costs one failure, not one per
# request.
_outcomes: dict[str, bool] = {}


class LoaderError(Exception):
    """A plugin's entry point could not be imported or did not register."""


def is_quarantined(plugin_id: str) -> bool:
    return _outcomes.get(plugin_id) is False


def import_entry_point(folder: str, spec: str) -> object:
    """Import ``pkg.module`` from a plugin folder and return its ``attr``.

    The folder is put on ``sys.path`` for the DURATION of this import only,
    and every ``sys.modules`` entry the import introduced is evicted before
    returning -- so two plugins that both use a generic, responsibility-named
    top-level package (``connector``, ``runtime``, ``mcp_bridge``) never
    collide, regardless of
    import order. Without this, Python's module cache (keyed by string name,
    not filesystem path) would silently hand the SECOND plugin's import the
    FIRST plugin's already-cached module -- a design consequence of the new
    per-plugin folder convention using the same names across every plugin
    (design §5.2), not a pre-existing bug: the old convention named every
    plugin's package after the plugin itself, so no two plugins ever shared
    a top-level module name.

    Known limit: only the entry point's own top-level name is evicted, so a
    plugin whose ``connector/`` imports a *sibling* top-level module from the
    same folder (e.g. a bare ``helpers.py`` next to ``connector/``) leaks that
    module and could still collide across plugins. Widening the filter to
    "any introduced module whose ``__file__`` is under ``folder``" would close
    it; that is deliberately out of scope here because no shipped plugin does
    this, but the next person should not have to re-derive why. ``cli_harness``
    is *not* an instance of this: it lives in its own directory, is never on a
    plugin's own folder path, and is resolved through pytest's ``pythonpath``
    (Task 7 keeps it there for exactly this reason).
    """
    module_path, _, attr = spec.partition(":")
    if not module_path or not attr:
        raise LoaderError(f"entry point must be 'module:attr', got {spec!r}")

    top_level = module_path.partition(".")[0]
    modules_before = set(sys.modules.keys())
    # ALWAYS insert at position 0, even when `folder` is already somewhere on
    # `sys.path`. Skipping the insert because the folder is "already there"
    # leaves resolution at the mercy of ambient path ORDER: with two plugin
    # roots on the path and this one not first, `import connector.connector`
    # resolves against the OTHER plugin and returns a correct-LOOKING register
    # for the wrong code. Inserting unconditionally makes the requested folder
    # win for the duration of this import; the matching `remove` below drops
    # the FIRST match, which is this insertion, so a pre-existing duplicate
    # entry survives untouched.
    sys.path.insert(0, folder)
    try:
        module = importlib.import_module(module_path)
        fn = getattr(module, attr, None)
        if fn is None:
            raise LoaderError(f"{module_path} has no attribute {attr!r}")
        if not callable(fn):
            raise LoaderError(f"{module_path}:{attr} is not callable")
        return fn
    finally:
        try:
            sys.path.remove(folder)
        except ValueError:  # an imported module cleared sys.path out from under us
            pass
        introduced = set(sys.modules.keys()) - modules_before
        for name in introduced:
            if name == top_level or name.startswith(f"{top_level}."):
                del sys.modules[name]


def _unsatisfied_requirements(requirements: list[str]) -> list[str]:
    """Return every PEP 508 requirement string in `requirements` that is not
    satisfied by what's importable in the CURRENT environment. Uses
    `importlib.metadata` (stdlib) + `packaging.requirements` (already a
    project dependency, per `oauth/tokens.py`'s own core_compat check in
    `plugins/service.py`) -- no new dependency needed.

    DIAGNOSTIC SCOPE, deliberately narrow (design §10.2): this checks
    DISTRIBUTION METADATA, not importability, and ignores PEP 508 environment
    markers and extras. So `foo; sys_platform == "win32"` reads as unsatisfied
    on Linux, and a vendored/namespace package with no `.dist-info` reads as
    missing even though `import` would work. Both are acceptable for a check
    whose whole job is to replace an opaque ModuleNotFoundError with a
    specific package name: neither produces a silent wrong answer, both fail
    loudly and legibly. Do not grow this into a resolver.
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as installed_version

    from packaging.requirements import Requirement

    unsatisfied: list[str] = []
    for raw in requirements:
        req = Requirement(raw)
        try:
            installed = installed_version(req.name)
        except PackageNotFoundError:
            unsatisfied.append(raw)
            continue
        if req.specifier and not req.specifier.contains(installed, prereleases=True):
            unsatisfied.append(raw)
    return unsatisfied


def load_plugin(discovered: DiscoveredPlugin) -> bool:
    """Import and register a discovered plugin's contributions.

    Returns True if the plugin is loaded (including a data-only plugin with no
    entry points, which loads to a no-op). Returns False and quarantines the
    plugin id on any failure -- it never raises.
    """
    plugin_id = discovered.plugin_id
    cached = _outcomes.get(plugin_id)
    if cached is not None:
        return cached

    if not discovered.valid or discovered.manifest is None:
        logger.warning("refusing to load invalid plugin %s: %s", plugin_id, discovered.error)
        _outcomes[plugin_id] = False
        return False

    if discovered.trust not in TRUSTED:
        # Not a load failure so much as a policy refusal, but it is cached the
        # same way so we never import the module to find out.
        logger.warning(
            "refusing to load plugin %s: trust %r is not one of %s",
            plugin_id,
            discovered.trust,
            sorted(TRUSTED),
        )
        _outcomes[plugin_id] = False
        return False

    requirements = discovered.manifest.get("requirements") or []
    if requirements:
        missing = _unsatisfied_requirements(list(requirements))
        if missing:
            logger.warning(
                "refusing to load plugin %s: missing/unsatisfied requirement(s) %s -- "
                "add them to backend/pyproject.toml and run `uv sync`",
                plugin_id,
                missing,
            )
            _outcomes[plugin_id] = False
            return False

    raw = discovered.manifest.get("entry_points") or {}
    entry_points = raw if isinstance(raw, dict) else {}

    contrib = PluginContributions(plugin_id)
    # Every declared entry point is imported and called with the SAME
    # contribution object. The key ("connectors", "runtimes", ...) is
    # documentation; what a plugin may actually add is bounded by the methods
    # PluginContributions exposes, not by the key it chose.
    for kind, spec in sorted(entry_points.items()):
        if not spec:
            continue
        try:
            register = import_entry_point(discovered.path, str(spec))
            register(contrib)  # type: ignore[operator]
        except Exception:
            logger.exception(
                "plugin %s failed to load entry point %r; quarantining", plugin_id, kind
            )
            _outcomes[plugin_id] = False
            return False

    record(contrib)
    _outcomes[plugin_id] = True
    return True


def reset_for_tests() -> None:
    _outcomes.clear()
