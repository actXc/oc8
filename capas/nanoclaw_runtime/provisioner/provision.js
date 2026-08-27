// Create inbound.db + outbound.db in the mounted session folder using
// nanoclaw's own schema constants. Nothing else -- oc8 fills the rows itself.
//
// Imports the TypeScript source directly (no compile step): bun's module
// loader strips types at import time, so INBOUND_SCHEMA/OUTBOUND_SCHEMA come
// straight from nanoclaw's own src/db/schema.ts -- the SQL text is never
// copied into oc8.
import { Database } from "bun:sqlite";
import { INBOUND_SCHEMA, OUTBOUND_SCHEMA } from "/src/src/db/schema.ts";
import path from "path";

const dir = process.argv[2];
if (!dir) {
  console.error("usage: provision.js <session-dir>");
  process.exit(2);
}

for (const [file, schema] of [
  ["inbound.db", INBOUND_SCHEMA],
  ["outbound.db", OUTBOUND_SCHEMA],
]) {
  const db = new Database(path.join(dir, file));
  // Upstream's ensureSchema sets this before creating the tables, and the
  // container's reader (agent-runner/src/db/connection.ts) requires it: in WAL
  // the reader can hold a stale snapshot and never see the host's writes.
  db.exec("PRAGMA journal_mode = DELETE;");
  db.exec(schema);
  db.close();
}
console.error(`[provision] created inbound.db + outbound.db in ${dir}`);
