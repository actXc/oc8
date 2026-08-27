"""A door written next year cannot forget who is deciding.

`decide_approval` is the single funnel both existing doors already call
(`api/v1/approvals.py:36`, `channels/dispatch.py:196`), which is exactly why the
department term lives in it. But a funnel only holds if a new caller CANNOT
reach it without the term: today the nearest parameter is
`principal: Principal | None = None`, so a door that forgot it compiles, runs,
and decides.

So `actor` is keyword-only with NO default. A caller that omits it is a
`TypeError` at call time and a mypy error in CI, rather than a silently
unscoped decision.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any

import pytest

from oc8.approvals import decide_approval
from oc8.approvals.repo import load_for_actor, visible_approvals
from oc8.workspace.queue import answer_clarification, open_clarifications


def test_decide_approval_cannot_be_called_without_one() -> None:
    sig = inspect.signature(decide_approval)

    actor = sig.parameters.get("actor")
    assert actor is not None, "decide_approval takes no actor at all"
    assert actor.default is inspect.Parameter.empty, (
        "a default makes the department term optional, which is the same as not "
        "having one -- the caller that forgets is the caller it exists for"
    )
    assert actor.kind is inspect.Parameter.KEYWORD_ONLY

    # Removed, not deprecated. `via` comes off the actor and attribution comes
    # off `actor.principal`; leaving either behind would let a new door satisfy
    # the signature while saying nothing about scope.
    assert "principal" not in sig.parameters
    assert "via" not in sig.parameters

    # Typed as `Any` because the call is meant to be REJECTED before anything
    # looks at these two -- a real session and a real row would prove nothing
    # more and would need a database.
    nothing: Any = object()
    with pytest.raises(TypeError):
        coro: Any = decide_approval(nothing, nothing, decision="approve", tenant_id=uuid.uuid4())
        # Closed so a signature that ACCEPTED the call does not additionally
        # produce a "coroutine was never awaited" warning instead of the clear
        # failure below.
        coro.close()
        pytest.fail("decide_approval accepted a call with no actor")


@pytest.mark.parametrize(
    "func",
    [visible_approvals, load_for_actor, open_clarifications, answer_clarification],
    ids=["visible_approvals", "load_for_actor", "open_clarifications", "answer_clarification"],
)
def test_every_read_funnel_demands_the_same_term(func: Any) -> None:
    """Not named in §8, and it is the same forcing trick applied to the READ
    side. `load_for_actor` returning `None` for "not yours" is only a boundary if
    nothing can call it without an actor; a default of `None` there would make
    every list tenant-wide again and nothing would fail."""
    actor = inspect.signature(func).parameters.get("actor")
    assert actor is not None, f"{func.__name__} takes no actor"
    assert actor.default is inspect.Parameter.empty, f"{func.__name__}'s actor has a default"
    assert actor.kind is inspect.Parameter.KEYWORD_ONLY
