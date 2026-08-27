"""The container shell reports WHY the control plane refused it.

When the shell dies, all anyone has is the container log tail (isolated.py logs
the last 800 chars on a non-zero exit). `raise_for_status()` puts only the status
line there -- "Client error '402 Payment Required'" -- while the reason the
control plane gave ("tenant budget exhausted") is thrown away, in the one place
where nobody can re-run the call to find out.
"""

from __future__ import annotations

import httpx
import pytest

from oc8.isolated_shell import check_response

STEP = "http://oc8:8000/api/v1/internal/agent/1234/step"


def _response(status_code: int, payload: dict[str, str]) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("POST", STEP))


def test_a_refusal_carries_the_control_planes_reason() -> None:
    with pytest.raises(httpx.HTTPStatusError) as exc:
        check_response(_response(402, {"detail": "tenant budget exhausted"}))

    assert "tenant budget exhausted" in str(exc.value)
    assert "402" in str(exc.value)


def test_a_successful_call_passes_through() -> None:
    check_response(_response(200, {"text": "done"}))  # must not raise
