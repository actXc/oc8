"""Zip N rendered capa manifests into one downloadable archive. `discovery.py`
already treats every subfolder of a plugin root as an independent capa -- a
ZIP with several sibling `<name>/plugin.toml` folders is not a new concept,
just several ordinary capas produced by one export run (design's Architecture
Overview)."""

from __future__ import annotations

import io
import zipfile

from oc8.capas.discovery import MANIFEST_FILENAME
from oc8.capas.export import ExportedCapa

__all__ = ["build_zip"]


def build_zip(items: list[ExportedCapa]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in items:
            zf.writestr(f"{item.folder_name}/{MANIFEST_FILENAME}", item.manifest_toml)
    return buf.getvalue()
