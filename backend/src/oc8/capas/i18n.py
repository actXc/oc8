"""Resolves a capa-authored English string into every locale its `i18n/*.po`
catalogs translate it into (design: capa-i18n).

`msgid` is content-as-key: the English source string itself, exactly as it
appears in the capa's TOML. There is no separate stable-key scheme, matching
gettext/Odoo convention -- a changed English string simply becomes
untranslated again until someone re-translates it, which is visible in the
`.po` diff rather than a silently stale key.
"""

from __future__ import annotations

from collections.abc import Mapping


def translations_for(i18n: Mapping[str, Mapping[str, str]], source_text: str) -> dict[str, str]:
    """Every translation of `source_text` across a capa's `i18n/*.po`
    catalogs (`DiscoveredPlugin.i18n`, `{locale: {msgid: msgstr}}`).

    A locale with no entry for this exact string is omitted rather than
    defaulted to the source text -- callers already display `source_text`
    itself for any locale missing here, so there is nothing this function
    needs to say about it.
    """
    return {
        locale: catalog[source_text]
        for locale, catalog in i18n.items()
        if source_text in catalog
    }
