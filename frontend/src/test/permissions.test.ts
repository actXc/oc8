import { describe, expect, it } from "vitest";
import { mcpToolsFromConnections } from "@/lib/permissions";
import type { McpConnection } from "@/lib/hooks";

function makeConnection(overrides: Partial<McpConnection> = {}): McpConnection {
  return {
    id: "uuid-123",
    name: "Odoo CRM",
    transport: "http",
    serverUrl: "https://example.test/mcp",
    command: "",
    args: [],
    departmentId: null,
    connected: true,
    scopes: [],
    health: {},
    guardrailPresets: [],
    guardrailLibrary: null,
    hasValueSpec: false,
    ...overrides,
  } as McpConnection;
}

describe("mcpToolsFromConnections", () => {
  it("keys the tool by the connection's NAME, not its database row id", () => {
    // `department.frame["tools"]` is read by the runtime keyed by connection
    // name (agent/engine.py, api/mcp_gateway.py) -- not by the connection's
    // database row UUID. McpTool.id is what PermissionsPanel uses as the
    // PolicyMap key, so it must be the name.
    const connection = makeConnection({ id: "uuid-123", name: "Odoo CRM" });

    const [tool] = mcpToolsFromConnections([connection]);

    expect(tool.id).toBe("Odoo CRM");
    expect(tool.id).not.toBe("uuid-123");
  });

  it("still carries the display name and description through unchanged", () => {
    const connection = makeConnection({
      id: "uuid-456",
      name: "Website Scraper",
      scopes: ["read", "write"],
    });

    const [tool] = mcpToolsFromConnections([connection]);

    expect(tool.name).toBe("Website Scraper");
    expect(tool.desc).toBe("read · write");
  });

  it("falls back to serverUrl, then transport, when a connection has no scopes", () => {
    const withUrl = mcpToolsFromConnections([
      makeConnection({ scopes: [], serverUrl: "https://example.test/mcp" }),
    ]);
    expect(withUrl[0].desc).toBe("https://example.test/mcp");

    const withoutUrl = mcpToolsFromConnections([
      makeConnection({ scopes: [], serverUrl: "", transport: "stdio" }),
    ]);
    expect(withoutUrl[0].desc).toBe("stdio");
  });
});
