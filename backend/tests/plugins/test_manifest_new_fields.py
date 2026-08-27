from __future__ import annotations

from oc8.capas.manifest import parse_manifest


def test_plugin_depends_defaults_to_empty_list() -> None:
    manifest = parse_manifest({"name": "x", "version": "1.0.0"})
    assert manifest.plugin_depends == []


def test_plugin_depends_accepts_plugin_names() -> None:
    manifest = parse_manifest(
        {"name": "x", "version": "1.0.0", "plugin_depends": ["gdrive_source", "s3_source"]}
    )
    assert manifest.plugin_depends == ["gdrive_source", "s3_source"]


def test_requirements_defaults_to_empty_list() -> None:
    manifest = parse_manifest({"name": "x", "version": "1.0.0"})
    assert manifest.requirements == []


def test_requirements_accepts_pep508_strings() -> None:
    manifest = parse_manifest(
        {"name": "x", "version": "1.0.0", "requirements": ["python-docx>=1.1", "openpyxl>=3.1"]}
    )
    assert manifest.requirements == ["python-docx>=1.1", "openpyxl>=3.1"]
