"""The thin agent shell that runs INSIDE an isolated per-run container (§8.3).

It holds no secrets and no data — only a run-scoped token and the control-plane
internal URL, both from the environment. It drives the run by alternating
`step` (a model turn) and `tool` (execute a tool) over the internal API, then
reports the terminal result. All privileged work happens control-plane-side.

Deliberately depends only on the standard library + httpx, never on oc8 core, so
the container is a genuinely thin execution shell.
"""

from __future__ import annotations

import os
import sys

import httpx

MAX_ITERS = 64  # hard backstop; the control plane enforces the real step budget
MAX_BODY_CHARS = 1000


def check_response(resp: httpx.Response) -> None:
    """`raise_for_status()`, but the message carries the control plane's reason.

    When the shell dies the only trace left is the container log tail, so a bare
    "Client error '402 Payment Required'" means the cause is simply gone -- while
    the body says which budget, which frame, which tool. Deliberately duplicated
    from `modelrouter.http_errors` rather than imported: this module must depend
    on nothing but the standard library and httpx (see the module docstring).
    """
    if not resp.is_error:
        return
    try:
        body = resp.text[:MAX_BODY_CHARS]
    except Exception:
        body = ""
    message = f"{resp.status_code} from {resp.request.url}"
    if body:
        message = f"{message}: {body}"
    raise httpx.HTTPStatusError(message, request=resp.request, response=resp)


def main() -> int:
    base = os.environ["OC8_INTERNAL_URL"].rstrip("/")
    token = os.environ["OC8_AGENT_TOKEN"]
    run_id = os.environ["OC8_RUN_ID"]
    headers = {"Authorization": f"Bearer {token}"}
    api = f"{base}/api/v1/internal/agent/{run_id}"

    status, output = "done", ""
    with httpx.Client(timeout=120.0, headers=headers) as c:
        for _ in range(MAX_ITERS):
            r = c.post(f"{api}/step")
            check_response(r)
            step = r.json()
            output = step.get("text") or output
            calls = step.get("tool_calls") or []
            if not calls:
                if step.get("done"):
                    break
                # No tool calls but not "done" means the step budget is spent.
                status = "done"
                break
            suspended = False
            for tc in calls:
                tr = c.post(
                    f"{api}/tool",
                    json={"id": tc["id"], "name": tc["name"], "arguments": tc.get("arguments", {})},
                )
                check_response(tr)
                result = tr.json()
                if result.get("status") in ("waiting_for_approval", "waiting_for_input"):
                    # The control plane suspended the run; the shell's job is done.
                    print(f"[shell] run suspended: {result.get('status')}", file=sys.stderr)
                    return 0
            if suspended:
                break
        else:
            status = "done"

        check_response(c.post(f"{api}/finish", json={"status": status, "output": output}))
    print(f"[shell] run {run_id} finished: {status}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
