"""Auto-imported by every Python interpreter that starts with this directory
on PYTHONPATH -- the `site` module's standard startup hook, which fires even
for a `uv tool run <package>` console-script entrypoint (confirmed live
against the real odoo_mcp subprocess, 2026-09-02). Gives an MCP tool server
subprocess a real `oc8/<version>` User-Agent instead of Python's bare
`Python-urllib/<pyver>` default, without touching a line of the third-party
package it runs.

Self-contained on purpose: this runs inside the SUBPROCESS's own venv (often
`uv tool run`'s isolated, ephemeral one), which never has the oc8 package
installed -- so it cannot `import oc8`. The actual version string travels in
via OC8_USER_AGENT (set by agent/mcp_client.py's _safe_env), not a shared
import. Deliberately core, not plugin, code: it patches Python's own urllib
default, not any one vendor's behaviour, so it lives under agent/ rather
than a specific capa's setup, per this repo's "core stays software-neutral"
rule."""

import os
import urllib.request

_ua = os.environ.get("OC8_USER_AGENT")
if _ua:
    _opener = urllib.request.build_opener()
    _opener.addheaders = [("User-agent", _ua)]
    urllib.request.install_opener(_opener)
