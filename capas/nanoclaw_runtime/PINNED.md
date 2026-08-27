# Pinned nanoclaw commit

This is the nanoclaw commit this adapter's protocol assumptions (session DB
schema, container-runner mount/env layout, agent-runner poll loop, MCP server
config shape) were verified against. Re-verify Task 0's spike findings before
bumping this pin.

- SHA: `879835bf8bb882672742f0765747312c4948420f`
- `git describe --tags --always`: `v2.1.17-428-g879835bf`
- Reference checkout: `/Users/justinmester/Documents/PyCharm_Git/nanoclaw` (read-only, MIT)
- Agent image built from this checkout via `bash container/build.sh` (unmodified):
  `nanoclaw-agent-v2-e8d09c34:latest` (image id `b0574cc0bf0d`, ~2.23GB)

## Provisioner image (`capas/nanoclaw_runtime/provisioner`)

nanoclaw's session schema lives in `src/db/schema.ts` -- TypeScript, not
shipped compiled -- so a `node:22-slim` + `better-sqlite3` image importing
`schema.js` (an earlier sketch) cannot resolve that import. The image is
built on `oven/bun:1` instead: bun's module loader imports `.ts` files
directly (types stripped at import time, no `tsc` step) and ships a
built-in SQLite driver (`bun:sqlite`), so `provision.js` does
`import { INBOUND_SCHEMA, OUTBOUND_SCHEMA } from "/src/src/db/schema.ts"`
straight against the cloned, pinned checkout. This route worked on the
first build (verified 2026-07-27): `docker build` succeeded, and running
the resulting image with `node /app/provision.js /session` produced
`inbound.db` with `delivered`, `destinations`, `messages_in`,
`session_routing` and `outbound.db` with `container_state`,
`messages_out`, `processing_ack`, `session_state` -- matching schema.ts
exactly, with no schema text copied into oc8.

`node` is symlinked to `bun` in the image (`ln -s /usr/local/bin/bun
/usr/local/bin/node`) so `SandboxSpec.command` in
`nanoclaw_runtime.runtime.session.provision_session` can keep naming `"node"` as
the interpreter unchanged: oven/bun's stock `docker-entrypoint.sh` execs
its argv as-is whenever argv[0] resolves on `$PATH`, and after the
symlink it does.
