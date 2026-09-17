from __future__ import annotations

from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8.config import get_settings
from oc8.i18n.catalog import invalidate_core_catalog_cache
from oc8.main import create_app

pytestmark = pytest.mark.asyncio


async def _get_core_catalog(monkeypatch: pytest.MonkeyPatch, root: Path) -> dict[str, object]:
    settings = get_settings().model_copy(update={"core_i18n_path": str(root)})
    monkeypatch.setattr("oc8.i18n.catalog.get_settings", lambda: settings)
    invalidate_core_catalog_cache()
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get("/api/v1/i18n/core")
            assert r.status_code == 200, r.text
            body = r.json()
            assert isinstance(body, dict)
            return body


async def test_core_i18n_endpoint_is_unauthenticated(tmp_path: Path) -> None:
    """No `Authorization` header at all -- reachable before login, like
    `/auth/config`, since the login screen itself is translated."""
    invalidate_core_catalog_cache()
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get("/api/v1/i18n/core")
            assert r.status_code == 200, r.text


async def test_core_i18n_endpoint_empty_when_no_catalogs_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = await _get_core_catalog(monkeypatch, tmp_path)
    assert body["locales"] == []


async def test_core_i18n_endpoint_serves_a_locale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "fr.po").write_text(
        'msgid ""\n'
        'msgstr ""\n'
        '"X-Native-Name: Français\\n"\n'
        '"X-Flag: 🇫🇷\\n"\n'
        "\n"
        'msgid "Save"\n'
        'msgstr "Enregistrer"\n',
        encoding="utf-8",
    )
    body = await _get_core_catalog(monkeypatch, tmp_path)
    assert body["locales"] == [
        {
            "locale": "fr",
            "nativeName": "Français",
            "flag": "🇫🇷",
            "translations": {"Save": "Enregistrer"},
        }
    ]
