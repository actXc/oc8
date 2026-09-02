from __future__ import annotations

import io
import zipfile

from oc8.capas.export import ExportedCapa
from oc8.capas.export_package import build_zip


def test_build_zip_writes_one_folder_per_item() -> None:
    items = [
        ExportedCapa(folder_name="vertrieb", manifest_toml='[plugin]\nname = "vertrieb"\n'),
        ExportedCapa(
            folder_name="crm_follow_up", manifest_toml='[plugin]\nname = "crm_follow_up"\n'
        ),
    ]
    data = build_zip(items)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert names == {"vertrieb/plugin.toml", "crm_follow_up/plugin.toml"}
        assert zf.read("vertrieb/plugin.toml").decode() == '[plugin]\nname = "vertrieb"\n'


def test_build_zip_writes_extra_files_alongside_plugin_toml() -> None:
    items = [
        ExportedCapa(
            folder_name="crm_follow_up",
            manifest_toml='[plugin]\nname = "crm_follow_up"\n',
            extra_files={"skills/crm_follow_up.toml": 'name = "crm-follow-up"\n'},
        ),
        # A department/agent export has no extra files -- must not write
        # anything beyond its own plugin.toml.
        ExportedCapa(folder_name="vertrieb", manifest_toml='[plugin]\nname = "vertrieb"\n'),
    ]
    data = build_zip(items)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert names == {
            "crm_follow_up/plugin.toml",
            "crm_follow_up/skills/crm_follow_up.toml",
            "vertrieb/plugin.toml",
        }
        assert (
            zf.read("crm_follow_up/skills/crm_follow_up.toml").decode()
            == 'name = "crm-follow-up"\n'
        )
