// stdio <-> HTTP bridge for oc8's tool gateway.
//
// nanoclaw's McpServerConfig is stdio-only ({command, args, env}), while oc8's
// /mcp is HTTP with a Bearer token -- the token being the whole point, since it
// is what binds every tool call to one run and one agent. This shim reads
// newline-delimited JSON-RPC on stdin, POSTs each message to the gateway with
// the Authorization header, and writes the reply back to stdout.
//
// The token comes from the container env (OC8_TOKEN), never from
// container.json: that file lives in the per-agent folder and outlives the
// run whose token it would hold. A Task 0 spike against the real image
// confirmed the MCP subprocess DOES inherit the container env, so there is no
// config-file fallback here (e.g. reading /workspace/.oc8-token).

import { createInterface } from 'node:readline';

const url = process.env.OC8_MCP_URL;
const token = process.env.OC8_TOKEN;
if (!url || !token) {
  console.error('[oc8-bridge] OC8_MCP_URL and OC8_TOKEN are required');
  process.exit(2);
}

// terminal: false + input-only avoids readline echoing input back, and
// crlfDelay: Infinity treats a \r\n pair split across two `data` chunks as one
// line break instead of two, so CRLF-terminated input doesn't produce a
// spurious blank line.
const rl = createInterface({ input: process.stdin, crlfDelay: Infinity, terminal: false });

// Best-effort extraction of a request id from a line we could not JSON-parse,
// so an error reply still has a chance of matching the caller's own id
// instead of forcing a fallback to null. Never throws.
function idFromBrokenLine(line) {
  const m = line.match(/"id"\s*:\s*("(?:[^"\\]|\\.)*"|-?\d+)/);
  if (!m) return null;
  try {
    return JSON.parse(m[1]);
  } catch {
    return null;
  }
}

// A tool call sits behind every line ahead of it on stdin, so a gateway that
// hangs would otherwise stall the whole run, not just one call. 180s matches
// the model-router's own upstream timeout: long enough that a call which
// would have succeeded is not cut short, short enough to eventually tell the
// model something rather than hang forever.
const GATEWAY_TIMEOUT_MS = 180_000;

for await (const line of rl) {
  const trimmed = line.trim();
  if (!trimmed) continue;

  // A malformed or unexpectedly-shaped line must never crash the bridge: one
  // bad frame would otherwise take down the process and cost the harness
  // every tool for the rest of the run. Parse defensively and classify the
  // *shape* once, totally, before anything downstream reads `.id` -- valid
  // JSON that isn't an object (null, 42, "hi", true, [1,2,3]) has no `.id`
  // to read and `'id' in parsed` throws on non-objects, so that check may
  // only run once we know `parsed` is actually an object.
  let parsed;
  let parseError = null;
  try {
    parsed = JSON.parse(trimmed);
  } catch (err) {
    parseError = err;
  }
  const isRequestObject =
    !parseError && typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed);

  let reply;
  if (parseError || !isRequestObject) {
    // Invalid JSON, or valid JSON that is not a JSON-RPC object at all (a
    // bare scalar, null, or array). Neither case has a trustworthy "id" to
    // echo back for a scalar/array/null (unlike a same-shaped-but-broken
    // object, where the id can still often be recovered by regex), so these
    // always reply with id: null rather than risk reading a field that does
    // not exist.
    reply = JSON.stringify({
      jsonrpc: '2.0',
      id: parseError ? idFromBrokenLine(trimmed) : null,
      error: {
        code: parseError ? -32700 : -32600,
        message: parseError
          ? `bridge: invalid JSON: ${parseError.message}`
          : `bridge: not a JSON-RPC request object: ${trimmed.slice(0, 200)}`,
      },
    });
  } else {
    // A JSON-RPC notification (no "id") gets no reply on the wire: echoing
    // one back would be an extra, unrequested frame the client never asked
    // for.
    const isNotification = !('id' in parsed);
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: trimmed,
        signal: AbortSignal.timeout(GATEWAY_TIMEOUT_MS),
      });
      const text = await res.text();
      if (res.ok) {
        reply = isNotification ? '' : text;
      } else {
        // A non-2xx still carries a body worth relaying: the gateway says WHY
        // it refused, and a bare status code would leave the model guessing.
        reply = isNotification ? '' : JSON.stringify({
          jsonrpc: '2.0',
          id: parsed.id ?? null,
          error: { code: -32603, message: `gateway ${res.status}: ${text.slice(0, 500)}` },
        });
      }
    } catch (err) {
      const isTimeout = err && err.name === 'TimeoutError';
      reply = isNotification ? '' : JSON.stringify({
        jsonrpc: '2.0',
        id: parsed.id ?? null,
        error: {
          code: -32603,
          message: isTimeout
            ? `bridge: gateway did not respond within ${GATEWAY_TIMEOUT_MS / 1000}s`
            : `bridge failed: ${err}`,
        },
      });
    }
  }

  if (reply && reply.trim()) process.stdout.write(reply.trim() + '\n');
}
