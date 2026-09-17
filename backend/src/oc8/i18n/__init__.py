"""Core UI translations: `<repo root>/i18n/<locale>.po`, one gettext catalog
per language, served to the frontend as a single JSON payload (`api/v1/i18n.py`).

Distinct from `oc8.capas.i18n`, which resolves a capa's own content strings --
this module is for the surrounding oc8 UI itself (buttons, labels, toasts).
Same file format and the same content-as-key convention, so a translator
who has already worked on a capa's catalog needs to learn nothing new here.
"""

from __future__ import annotations
