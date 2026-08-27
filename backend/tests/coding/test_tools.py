from __future__ import annotations

from oc8.coding.tools import CODING_TOOLS, FS_READ, FS_WRITE, SHELL_RUN


def test_three_tools_with_expected_names() -> None:
    names = {t.name for t in CODING_TOOLS}
    assert names == {FS_READ, FS_WRITE, SHELL_RUN}


def test_schemas_declare_required_params() -> None:
    by_name = {t.name: t for t in CODING_TOOLS}
    assert by_name[FS_WRITE].parameters["required"] == ["path", "content"]
    assert by_name[FS_READ].parameters["required"] == ["path"]
    assert by_name[SHELL_RUN].parameters["required"] == ["command"]
    for tool in CODING_TOOLS:
        assert tool.parameters["type"] == "object"
        assert tool.description
