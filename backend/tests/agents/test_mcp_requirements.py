from __future__ import annotations

from oc8.agent.mcp_requirements import wrap_with_requirements


def test_no_requirements_leaves_command_and_args_untouched() -> None:
    command, args = wrap_with_requirements("python", ["-m", "some_mcp"], {})
    assert command == "python"
    assert args == ["-m", "some_mcp"]


def test_an_empty_requirements_list_is_also_a_no_op() -> None:
    command, args = wrap_with_requirements("python", ["-m", "x"], {"requirements": []})
    assert command == "python"
    assert args == ["-m", "x"]


def test_requirements_wrap_the_invocation_in_uv_run_with() -> None:
    command, args = wrap_with_requirements(
        "python",
        ["-m", "some_mcp"],
        {"requirements": ["httpx>=0.27", "some-sdk>=2.0"]},
    )
    assert command == "uv"
    assert args == [
        "run",
        "--with",
        "httpx>=0.27",
        "--with",
        "some-sdk>=2.0",
        "--",
        "python",
        "-m",
        "some_mcp",
    ]
