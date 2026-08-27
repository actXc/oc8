from __future__ import annotations

from oc8.modelrouter.keys import model_admin_key_ref, model_key_ref


def test_admin_key_ref_is_distinct_from_the_completion_key_ref() -> None:
    assert model_admin_key_ref("anthropic") == "model/anthropic/admin_key"
    assert model_admin_key_ref("anthropic") != model_key_ref("anthropic")
