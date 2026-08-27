from __future__ import annotations

import pytest

from oc8.config import Settings


def test_sandbox_user_empty_is_valid() -> None:
    assert Settings(sandbox_user="").sandbox_user == ""


@pytest.mark.parametrize("value", ["1000", "1000:1000", "0:0"])
def test_sandbox_user_accepts_uid_or_uid_colon_gid(value: str) -> None:
    assert Settings(sandbox_user=value).sandbox_user == value


@pytest.mark.parametrize(
    "value",
    [
        "nobody",  # not an integer
        "a:b",  # neither part an integer
        ":1000",  # missing uid
        "1000:",  # missing gid
        "1000:1000:1000",  # too many parts
        "1000 ",  # whitespace is not a digit
    ],
)
def test_sandbox_user_rejects_malformed_values_at_startup(value: str) -> None:
    """An operator's typo must stop the process at startup (here), not surface
    as a broken agent run an hour later when the sandbox driver tries to pass
    it to `docker run --user` or the nanoclaw plugin tries to int() it."""
    with pytest.raises(ValueError, match="sandbox_user"):
        Settings(sandbox_user=value)
