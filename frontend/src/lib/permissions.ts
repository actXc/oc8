// Permission algebra for the department/agent frame view.
//
// This module carries NO vendor catalog. The interfaces a department can grant
// are whatever MCP connections the tenant has actually configured — resolved at
// render time from the productive `/mcp/connections` layer — and the granted
// state is the department's real saved frame. Nothing here names a
// specific product; those arrive only as plugins/MCP connections.

import { Plug, type LucideIcon } from "lucide-react";
import type { McpConnection } from "@/lib/hooks";

export type PermKey = "read" | "write" | "send";

export const PERM_LABEL: Record<PermKey, string> = {
  read: "Read",
  write: "Write",
  send: "Send",
};

export interface McpTool {
  id: string;
  name: string;
  icon: LucideIcon;
  desc: string;
}

/** A real MCP connection, described for the permission view. */
export function mcpToolsFromConnections(connections: McpConnection[]): McpTool[] {
  // Deduped by NAME, keeping the first occurrence -- department.frame is
  // keyed by connection name, so two connections sharing a name (e.g. each
  // department's own "Odoo" setup, or a default-login candidate from a
  // DIFFERENT department, per the department default-login picker) name the
  // exact same tile. Rendering one entry per row here would give React two
  // elements with the same key, which -- worse than the console warning --
  // silently duplicates the tile in the grid.
  const seen = new Set<string>();
  const tools: McpTool[] = [];
  for (const c of connections) {
    if (seen.has(c.name)) continue;
    seen.add(c.name);
    tools.push({
      // `id` here means "the frame's storage key", not "this connection's
      // database row id" -- department.frame["tools"] is keyed by connection
      // NAME at runtime (agent/engine.py, api/mcp_gateway.py), never by the
      // row's UUID. Using c.id would silently desync the grid from the
      // runtime's actual grant.
      id: c.name,
      name: c.name,
      icon: Plug,
      desc: c.scopes.length ? c.scopes.join(" · ") : c.serverUrl || c.transport,
    });
  }
  return tools;
}

export interface ToolPolicy {
  enabled: boolean;
  perms: Record<PermKey, boolean>;
  /** Approval threshold in €; null = no threshold / always allowed */
  approvalEUR: number | null;
  /** The McpConnection this department's agents use for this tool when they
   *  have no login pin of their own (backend/runtime/executor.py's
   *  `_resolve_mcp_connection`, step 2). null = no explicit default, falls
   *  back to the legacy department_id lookup. Only meaningful in department
   *  mode -- an agent overrides it via its own narrowing (agents.$id.tsx),
   *  not via this map. */
  defaultConnectionId?: string | null;
}

export type PolicyMap = Record<string, ToolPolicy>;

export function pol(
  enabled: boolean,
  read = false,
  write = false,
  send = false,
  approvalEUR: number | null = null,
  defaultConnectionId: string | null = null,
): ToolPolicy {
  return { enabled, perms: { read, write, send }, approvalEUR, defaultConnectionId };
}

/** No interface enabled — the honest default for a fresh department. */
export function emptyPolicy(tools: McpTool[]): PolicyMap {
  return Object.fromEntries(tools.map((t) => [t.id, pol(false)]));
}

/** Map a department's saved frame (the `/departments/{id}/tools` shape) onto a
 *  PolicyMap keyed by connection id. Unknown/legacy keys are ignored so a stale
 *  frame never resurrects an interface the tenant no longer has. */
export function frameToPolicy(
  tools: McpTool[],
  frame: Record<string, Record<string, unknown>> | undefined,
): PolicyMap {
  const out: PolicyMap = {};
  for (const t of tools) {
    const f = frame?.[t.id];
    out[t.id] = f
      ? pol(
          Boolean(f.enabled),
          Boolean(f.read),
          Boolean(f.write),
          Boolean(f.send),
          typeof f.approval_eur === "number" ? f.approval_eur : null,
          typeof f.default_connection_id === "string" ? f.default_connection_id : null,
        )
      : pol(false);
  }
  return out;
}

/**
 * Effective agent policy: the agent value is clamped to the department frame
 * (an agent can only tighten, never expand).
 */
export function effectiveAgentPolicy(
  tools: McpTool[],
  deptPolicy: PolicyMap,
  override: PolicyMap | undefined,
): PolicyMap {
  const out: PolicyMap = {};
  for (const tool of tools) {
    const d = deptPolicy[tool.id] ?? pol(false);
    const o = override?.[tool.id];
    if (!o) {
      out[tool.id] = { ...d, perms: { ...d.perms } };
      continue;
    }
    out[tool.id] = {
      enabled: d.enabled && o.enabled,
      perms: {
        read: d.perms.read && o.perms.read,
        write: d.perms.write && o.perms.write,
        send: d.perms.send && o.perms.send,
      },
      // Agent can only lower the threshold. null (dept) = no limit.
      approvalEUR:
        d.approvalEUR == null
          ? o.approvalEUR
          : o.approvalEUR == null
            ? d.approvalEUR
            : Math.min(d.approvalEUR, o.approvalEUR),
    };
  }
  return out;
}

/** Is the agent stricter than the department on a tool? */
export function isRestricted(deptTool: ToolPolicy, effTool: ToolPolicy): boolean {
  if (deptTool.enabled && !effTool.enabled) return true;
  for (const k of ["read", "write", "send"] as const) {
    if (deptTool.perms[k] && !effTool.perms[k]) return true;
  }
  if (
    deptTool.approvalEUR != null &&
    effTool.approvalEUR != null &&
    effTool.approvalEUR < deptTool.approvalEUR
  )
    return true;
  if (deptTool.approvalEUR == null && effTool.approvalEUR != null) return true;
  return false;
}
