"""The thin agent shell that runs INSIDE an isolated per-run container (§8.3).

It holds no secrets and no data — only a run-scoped token and the control-plane
internal URL, both from the environment. It drives the run by alternating
`step` (a model turn) and `tool` (execute a tool) over the internal API, then
reports the terminal result. All privileged work happens control-plane-side.

Deliberately depends only on the standard library + httpx, never on oc8 core, so
the container is a genuinely thin execution shell.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

MAX_ITERS = 64  # hard backstop; the control plane enforces the real step budget
MAX_BODY_CHARS = 1000
PREVIEW_CHARS = 200


def _preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    """One log-line-safe rendering of a possibly long, multi-line value."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


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

    # Before this, the ONLY output this process ever produced was one line on
    # suspend and one on finish -- an operator running `docker logs` on a run
    # that was still in progress, or one that got force-killed by the
    # provisioner's timeout teardown, saw nothing at all. Every line below is
    # printed to stderr with an explicit flush: this process can be SIGKILLed
    # (isolated.py's teardown removes the container in its `finally`, whether
    # this run finished cleanly or the outer wait() timed out on a wedged
    # container), and a line still sitting in a stdio buffer at that moment is
    # gone for good.
    def log(message: str) -> None:
        print(f"[shell] run {run_id}: {message}", file=sys.stderr, flush=True)

    log("starting")
    status, output = "done", ""
    step_no = 0
    try:
        with httpx.Client(timeout=120.0, headers=headers) as c:
            for step_no in range(1, MAX_ITERS + 1):
                r = c.post(f"{api}/step")
                check_response(r)
                step = r.json()
                output = step.get("text") or output
                calls = step.get("tool_calls") or []
                log(
                    f"step {step_no}: model responded, tool_calls={len(calls)} "
                    f"text={_preview(step.get('text') or '')!r}"
                )
                if not calls:
                    if step.get("done"):
                        log(f"step {step_no}: model signalled it is finished, ending run as done")
                        break
                    # No tool calls but not "done" means the step budget is spent.
                    log(
                        f"step {step_no}: no tool calls and the step budget is spent, "
                        "ending run as done"
                    )
                    status = "done"
                    break
                for tc in calls:
                    args_preview = _preview(json.dumps(tc.get("arguments", {}), default=str))
                    log(f"step {step_no}: calling tool {tc['name']} args={args_preview}")
                    tr = c.post(
                        f"{api}/tool",
                        json={
                            "id": tc["id"],
                            "name": tc["name"],
                            "arguments": tc.get("arguments", {}),
                        },
                    )
                    check_response(tr)
                    result = tr.json()
                    log(
                        f"step {step_no}: tool {tc['name']} -> status={result.get('status')} "
                        f"output={_preview(str(result.get('output', '')))!r}"
                    )
                    if result.get("status") in ("waiting_for_approval", "waiting_for_input"):
                        # The control plane suspended the run; the shell's job is done.
                        log(f"run suspended: {result.get('status')}")
                        return 0
            else:
                log(f"reached the hard backstop of {MAX_ITERS} iterations, ending run as done")
                status = "done"

            check_response(c.post(f"{api}/finish", json={"status": status, "output": output}))
    except Exception as exc:
        # The traceback that follows this (Python's default excepthook, still
        # printed to stderr) has the stack; this line has the one thing the
        # stack can't: which step of THIS run it happened on.
        log(f"FAILED at step {step_no}: {exc}")
        raise
    log(f"finished: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
