import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { PermissionsPanel } from "@/components/permissions-panel";
import type { Department, Agent } from "@/lib/mock-data";

const { getConnections, getLogins, getCredentials, getCredentialTypes, postLogin } = vi.hoisted(
  () => ({
    getConnections: vi.fn(),
    getLogins: vi.fn(() => []),
    getCredentials: vi.fn(() => []),
    getCredentialTypes: vi.fn(() => []),
    postLogin: vi.fn(),
  }),
);

vi.mock("@/lib/api", () => ({
  api: {
    get: (url: string) => {
      if (url.startsWith("/mcp/logins")) return getLogins();
      if (url.startsWith("/credential-types")) return getCredentialTypes();
      if (url.startsWith("/credentials")) return getCredentials();
      return getConnections();
    },
    post: (url: string, body: unknown) => {
      if (url === "/mcp/logins") return postLogin(body);
      throw new Error(`unexpected POST ${url}`);
    },
  },
}));

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const dept: Department = {
  id: "dept-1",
  name: "Sales",
  icon: "sales",
  goal: "Fill the pipeline",
  okr: "okr",
  kpiLabel: "Leads",
  kpiValue: "1",
  activity: 50,
  accent: "#000",
};

describe("PermissionsPanel", () => {
  beforeEach(() => {
    getConnections.mockReset();
    getLogins.mockReset();
    getLogins.mockReturnValue([]);
    getCredentials.mockReset();
    getCredentials.mockReturnValue([]);
    getCredentialTypes.mockReset();
    getCredentialTypes.mockReturnValue([]);
    postLogin.mockReset();
  });

  it("recognizes a tool as enabled when the saved policy is keyed by the connection's NAME, not its database id", async () => {
    // Mirrors Task 9's "attached-by-name recognition" test for the
    // guardrails panel: a connection whose row id and name differ, with a
    // deptPolicy keyed by the NAME (as a correctly-fixed writer would save
    // it). If mcpToolsFromConnections regresses back to keying by c.id, this
    // tile would render as OFF instead of ON.
    getConnections.mockResolvedValue([
      {
        id: "uuid-999",
        name: "Odoo CRM",
        transport: "http",
        serverUrl: "https://example.test/mcp",
        command: "",
        args: [],
        departmentId: null,
        connected: true,
        scopes: ["read"],
        health: {},
        guardrailPresets: [],
        guardrailLibrary: null,
        hasValueSpec: false,
      },
    ]);

    const deptPolicy = {
      "Odoo CRM": {
        enabled: true,
        perms: { read: true, write: false, send: false },
        approvalEUR: null,
      },
    };

    renderWithClient(
      <PermissionsPanel
        mode="department"
        dept={dept}
        deptPolicy={deptPolicy}
        members={[] as Agent[]}
        agentOverrides={{}}
      />,
    );

    // InheritanceHeader's activeCount is `deptPolicy[t.id]?.enabled` summed
    // over `tools` -- if mcpToolsFromConnections regressed to keying by the
    // connection's row id, this lookup would miss the name-keyed deptPolicy
    // entirely and read "0 of 1".
    expect(await screen.findByText(/1 of 1 MCP interfaces active/i)).toBeInTheDocument();

    // The Permissions panel only lists tools whose *effective* policy is
    // enabled (same name-keyed lookup) -- proving the read side resolves by
    // name, not just the header count.
    expect(screen.queryByText(/No active interfaces\. Enable a tool first\./i)).toBeNull();
    expect(screen.getByRole("button", { name: "Read" })).toBeInTheDocument();
  });

  it("shows no default-login picker for a tool whose manifest doesn't declare a credential type", async () => {
    getConnections.mockResolvedValue([
      {
        id: "conn-own",
        name: "Odoo",
        transport: "stdio",
        serverUrl: "",
        command: "",
        args: [],
        departmentId: "dept-1",
        connected: true,
        scopes: [],
        health: {},
        guardrailPresets: [],
        guardrailLibrary: null,
        hasValueSpec: false,
        credentialType: null,
      },
    ]);

    const deptPolicy = {
      Odoo: {
        enabled: true,
        perms: { read: true, write: false, send: false },
        approvalEUR: null,
        defaultConnectionId: null,
      },
    };

    renderWithClient(
      <PermissionsPanel
        mode="department"
        dept={dept}
        deptPolicy={deptPolicy}
        members={[] as Agent[]}
        agentOverrides={{}}
      />,
    );

    expect((await screen.findAllByText("Odoo")).length).toBeGreaterThan(0);
    expect(screen.queryByText(/default login/i)).not.toBeInTheDocument();
  });

  it("offers the same inline credential picker the Hire dialog uses for the department-wide default login, contained in the tile, and persists the choice", async () => {
    getConnections.mockResolvedValue([
      {
        id: "conn-own",
        name: "Odoo",
        transport: "stdio",
        serverUrl: "",
        command: "",
        args: [],
        departmentId: "dept-1",
        connected: true,
        scopes: [],
        health: {},
        guardrailPresets: [],
        guardrailLibrary: null,
        hasValueSpec: false,
        credentialType: "odoo_login",
      },
    ]);
    getCredentials.mockReturnValue([
      { id: "cred-1", name: "oc8-local", credentialType: "odoo_login" },
    ]);
    postLogin.mockResolvedValue({
      id: "login-new",
      name: "Odoo",
      credentialId: "cred-1",
      departmentId: null,
      connected: false,
      scopes: {},
      health: {},
    });

    const deptPolicy = {
      Odoo: {
        enabled: true,
        perms: { read: true, write: false, send: false },
        approvalEUR: null,
        defaultConnectionId: null,
      },
    };
    const onChange = vi.fn();

    renderWithClient(
      <PermissionsPanel
        mode="department"
        dept={dept}
        deptPolicy={deptPolicy}
        members={[] as Agent[]}
        agentOverrides={{}}
        onDepartmentPolicyChange={onChange}
      />,
    );

    expect(await screen.findByText(/default login/i)).toBeInTheDocument();
    const picker = screen.getByRole("combobox");
    expect(await screen.findByText("oc8-local")).toBeInTheDocument();

    fireEvent.change(picker, { target: { value: "cred-1" } });

    await waitFor(() =>
      expect(postLogin).toHaveBeenCalledWith({
        name: "Odoo",
        credentialType: "odoo_login",
        credentialId: "cred-1",
        scopes: [],
      }),
    );
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({
          Odoo: expect.objectContaining({ defaultConnectionId: "login-new" }),
        }),
      ),
    );
  });
});
