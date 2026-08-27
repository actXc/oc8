# backend/src/oc8/modelrouter/http_errors.py
"""An upstream rejection keeps the provider's own explanation.

`httpx`'s `raise_for_status()` reports the status line and discards the response
body -- but the body is where a provider actually SAYS what was wrong
("Unexpected role 'system' after role 'tool'", "max_tokens too large"). Without
it every 4xx is an unexplained failure that has to be reproduced by hand, which
is exactly the debugging tax this module exists to remove.

The raised error stays an `httpx.HTTPStatusError`, so `fallback.is_retryable`
and the fallback chain classify it exactly as before.
"""

from __future__ import annotations

import re

import httpx

# Enough for any real provider error; short enough that a provider echoing the
# whole prompt back cannot flood the logs or a 502 body.
MAX_BODY_CHARS = 2000


def raise_for_status_with_body(resp: httpx.Response) -> None:
    """Like `resp.raise_for_status()`, but the message carries the body.

    For a response whose body has not been read (a streamed one), use
    `araise_for_status_with_body` instead.
    """
    if not resp.is_error:
        return
    raise _error(resp, _body_of(resp))


async def araise_for_status_with_body(resp: httpx.Response) -> None:
    """`raise_for_status_with_body` for a streamed response.

    A streamed response has not been read when the status is checked, so the
    body has to be pulled in first -- otherwise `resp.text` raises
    `ResponseNotRead` and the provider's reason is lost twice over.
    """
    if not resp.is_error:
        return
    try:
        await resp.aread()
    except Exception:
        pass
    raise _error(resp, _body_of(resp))


def describe_upstream_error(exc: Exception) -> str:
    """A log/response line for an upstream failure, with the body if there is one.

    Adapters in this package already embed the body in the message; this covers
    anything that does not (a plugin-provided adapter calling `raise_for_status`),
    without repeating a body that is already there.
    """
    detail = str(exc)
    resp = getattr(exc, "response", None)
    if resp is None:
        return detail
    body = _body_of(resp)
    if body and body not in detail:
        detail = f"{detail} | body={body}"
    return detail


def _body_of(resp: httpx.Response) -> str:
    try:
        return resp.text[:MAX_BODY_CHARS]
    except Exception:
        return ""


def _error(resp: httpx.Response, body: str) -> httpx.HTTPStatusError:
    url = resp.request.url if resp.request is not None else "upstream"
    message = f"{resp.status_code} from {url}"
    if body:
        message = f"{message}: {body}"
    return httpx.HTTPStatusError(message, request=resp.request, response=resp)


#: A provider rejecting a NEGATIVE max_tokens. The number is the deficit: the
#: prompt is that many tokens past what the model can hold, so the request could
#: not leave room for even one token of answer. Seen from
#: litellm/vLLM as `max_tokens must be at least 1, got -7075`.
_NEGATIVE_MAX_TOKENS = re.compile(r"max_tokens must be at least 1, got -(\d+)")


def explain_upstream_error(detail: str) -> str | None:
    """A plain sentence for an upstream failure whose own words mislead.

    Returns None for everything else, deliberately: a provider's own message is
    what a person needs to look an error up, and paraphrasing it would take that
    away. Only the shape below is rewritten, because it does the opposite of
    helping -- it reads like a misconfigured request or a broken provider, and
    the generic advice wrapped around it ("try again in a moment") can never
    work, since the next attempt sends the same transcript.
    """
    match = _NEGATIVE_MAX_TOKENS.search(detail)
    if match is None:
        return None
    over = match.group(1)
    return (
        "Das Gespräch passt nicht mehr in das Kontextfenster des Modells: es ist "
        f"rund {over} Token zu lang, sodass für eine Antwort kein Platz bleibt. "
        "Ein erneuter Versuch ändert daran nichts — der Lauf braucht ein kürzeres "
        "Transkript oder ein Modell mit größerem Fenster."
    )
