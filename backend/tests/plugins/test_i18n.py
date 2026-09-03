from __future__ import annotations

from oc8.capas.i18n import translations_for


def test_translations_for_resolves_exact_match() -> None:
    i18n = {"de": {"Read only": "Nur lesen"}}
    assert translations_for(i18n, "Read only") == {"de": "Nur lesen"}


def test_translations_for_omits_locale_missing_the_string() -> None:
    i18n = {"de": {"Read only": "Nur lesen"}}
    assert translations_for(i18n, "Write access") == {}


def test_translations_for_resolves_multiple_locales() -> None:
    i18n = {
        "de": {"Read only": "Nur lesen"},
        "fr": {"Read only": "Lecture seule"},
    }
    assert translations_for(i18n, "Read only") == {"de": "Nur lesen", "fr": "Lecture seule"}


def test_translations_for_empty_catalog_returns_empty_dict() -> None:
    assert translations_for({}, "Read only") == {}
