import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const updateNarrowingMock = vi.fn();
const mcpLoginsMock = vi.fn();
const mcpConnectionsMock = vi.fn();
const credentialTypesMock = vi.fn();
const credentialsMock = vi.fn();
const createMcpLoginMock = vi.fn();
const createCredentialMock = vi.fn();

vi.mock("@/lib/hooks-agent-detail", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks-agent-detail")>();
  return {
    ...actual,
    useUpdateNarrowing: () => ({ mutate: updateNarrowingMock, isPending: false }),
  };
});

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useMcpLogins: () => mcpLoginsMock(),
    useMcpConnections: () => mcpConnectionsMock(),
    useCredentialTypes: () => credentialTypesMock(),
    useCredentials: () => credentialsMock(),
    useCreateCredential: () => ({ mutateAsync: createCredentialMock, isPending: false }),
    useCreateMcpLogin: () => ({ mutateAsync: createMcpLoginMock, isPending: false }),
  };
});

import { AgentToolAccessPanel } from "@/routes/agents.$id";
import type { AgentDetail } from "@/lib/hooks-agent-detail";

const ONE_TOOL_AGENT: AgentDetail = {
  id: "agent-1",
  name: "Nora",
  role: "Sales",
  llm: "claude-sonnet-5",
  provider: "anthropic",
  status: "running",
  tools: [],
  lastAction: "",
  lastRun: "",
  tasksToday: 0,
  guardrails: [],
  schedule: "",
  avatarColor: "#000",
  departmentId: "dept-1",
  modelConfigId: null,
  isLead: false,
  mission: "",
  departmentName: "Vertrieb",
  effectiveTools: {
    Odoo: { enabled: true, read: true, modify: false, approvalEur: null },
  },
  departmentFrameTools: {
    Odoo: { enabled: true, read: true, modify: false, approvalEur: null },
  },
  narrowingTools: {
    Odoo: { enabled: true, read: true, modify: false, approvalEur: null },
  },
  runtimeRef: null,
  currentRunId: null,
};

function renderPanel(agent: AgentDetail = ONE_TOOL_AGENT) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AgentToolAccessPanel agent={agent} mayManage />
    </QueryClientProvider>,
  );
}

const PLAIN_CONNECTION = {
  id: "conn-1",
  name: "Odoo",
  transport: "stdio",
  serverUrl: "",
  command: "",
  args: [],
  departmentId: null,
  connected: true,
  scopes: [],
  health: {},
  credentialType: null,
  guardrailPresets: [],
  guardrailLibrary: null,
  hasValueSpec: false,
};

const ODOO_CREDENTIAL_TYPE = {
  name: "odoo_login",
  displayName: "Odoo login",
  fields: [
    {
      key: "username",
      label: "Username",
      kind: "text",
      required: true,
      default: "",
      placeholder: "",
      help: "",
    },
  ],
};

const ODOO_CONNECTION_WITH_CREDENTIAL = { ...PLAIN_CONNECTION, credentialType: "odoo_login" };

describe("AgentToolAccessPanel", () => {
  beforeEach(() => {
    updateNarrowingMock.mockReset();
    mcpLoginsMock.mockReset();
    mcpLoginsMock.mockReturnValue({ data: [] });
    mcpConnectionsMock.mockReset();
    mcpConnectionsMock.mockReturnValue({ data: [] });
    credentialTypesMock.mockReset();
    credentialTypesMock.mockReturnValue({ data: [ODOO_CREDENTIAL_TYPE] });
    credentialsMock.mockReset();
    credentialsMock.mockReturnValue({ data: [] });
    createMcpLoginMock.mockReset();
    createCredentialMock.mockReset();
  });

  it("shows the active-count badge against the department frame", () => {
    mcpConnectionsMock.mockReturnValue({ data: [PLAIN_CONNECTION] });
    renderPanel({
      ...ONE_TOOL_AGENT,
      departmentFrameTools: {
        Odoo: { enabled: true, read: true, modify: false, approvalEur: null },
        Hubspot: { enabled: true, read: true, modify: false, approvalEur: null },
      },
    });
    expect(screen.getByText(/1 of 2 MCP interfaces active/i)).toBeInTheDocument();
  });

  it("renders a locked tile with no toggle for a frame tool that has no live connection", () => {
    mcpConnectionsMock.mockReturnValue({ data: [] });
    renderPanel({
      ...ONE_TOOL_AGENT,
      effectiveTools: {},
      departmentFrameTools: {
        Hubspot: { enabled: true, read: false, modify: false, approvalEur: null },
      },
    });
    expect(screen.getByText("Hubspot")).toBeInTheDocument();
    expect(screen.getByText(/not connected yet/i)).toBeInTheDocument();
    // No tenant connections at all -- nothing to toggle and nothing left to
    // add, so the only button on screen is a disabled "Add tool".
    expect(screen.getByRole("button", { name: /add tool/i })).toBeDisabled();
  });

  it("toggling a connected tool off persists immediately, preserving its other fields", () => {
    mcpConnectionsMock.mockReturnValue({ data: [PLAIN_CONNECTION] });
    renderPanel();

    // The Toggle button carries no accessible name (icon-only); "Add tool"
    // does. Filter it out rather than assuming exactly one button exists.
    const toggle = screen
      .getAllByRole("button")
      .find((b) => !/add tool/i.test(b.textContent ?? ""));
    if (!toggle) throw new Error("toggle button not found");
    fireEvent.click(toggle);

    expect(updateNarrowingMock).toHaveBeenCalledWith(
      {
        narrowing: {
          tools: {
            Odoo: {
              enabled: false,
              read: true,
              modify: false,
              approval_eur: null,
              approval_actions: [],
              only: [],
              connection_id: null,
            },
          },
        },
      },
      expect.anything(),
    );
  });

  it("shows the inline credential picker for a connected, enabled tool with a credential type", () => {
    mcpConnectionsMock.mockReturnValue({ data: [ODOO_CONNECTION_WITH_CREDENTIAL] });
    renderPanel();
    expect(screen.getByText(/select a credential/i)).toBeInTheDocument();
  });

  it("selecting an existing credential persists its login id as connection_id immediately", async () => {
    mcpConnectionsMock.mockReturnValue({ data: [ODOO_CONNECTION_WITH_CREDENTIAL] });
    mcpLoginsMock.mockReturnValue({
      data: [
        {
          id: "login-1",
          name: "Odoo",
          credentialId: "cred-1",
          departmentId: null,
          connected: true,
          scopes: [],
          health: {},
        },
      ],
    });
    credentialsMock.mockReturnValue({
      data: [{ id: "cred-1", name: "oc8-local", credentialType: "odoo_login" }],
    });
    renderPanel();

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "cred-1" } });

    await waitFor(() =>
      expect(updateNarrowingMock).toHaveBeenCalledWith(
        {
          narrowing: {
            tools: {
              Odoo: {
                enabled: true,
                read: true,
                modify: false,
                approval_eur: null,
                approval_actions: [],
                only: [],
                connection_id: "login-1",
              },
            },
          },
        },
        expect.anything(),
      ),
    );
  });

  it("toggling off does not bake in a role-rights dip in effectiveTools -- narrowing/frame stay the source of truth", () => {
    // Regression test: effectiveTools = role_rights ∩ frame ∩ narrowing, so a
    // bad/missing role reference can zero out read/modify there even
    // though the agent's own stored narrowing (and the department frame) are
    // both still clean. Toggling enabled off must never resave that degraded
    // value for the fields this panel doesn't itself edit -- doing so
    // previously baked a transient role problem into narrowing forever.
    mcpConnectionsMock.mockReturnValue({ data: [PLAIN_CONNECTION] });
    renderPanel({
      ...ONE_TOOL_AGENT,
      effectiveTools: {
        Odoo: { enabled: true, read: false, modify: false, approvalEur: null },
      },
      departmentFrameTools: {
        Odoo: { enabled: true, read: true, modify: true, approvalEur: null },
      },
      narrowingTools: {
        Odoo: { enabled: true, read: true, modify: true, approvalEur: null },
      },
    });

    const toggle = screen
      .getAllByRole("button")
      .find((b) => !/add tool/i.test(b.textContent ?? ""));
    if (!toggle) throw new Error("toggle button not found");
    fireEvent.click(toggle);

    expect(updateNarrowingMock).toHaveBeenCalledWith(
      {
        narrowing: {
          tools: {
            Odoo: {
              enabled: false,
              read: true,
              modify: true,
              approval_eur: null,
              approval_actions: [],
              only: [],
              connection_id: null,
            },
          },
        },
      },
      expect.anything(),
    );
  });

  describe("agent-exclusive grants (tool not in the department frame)", () => {
    const SALESFORCE_CONNECTION = {
      ...PLAIN_CONNECTION,
      id: "conn-2",
      name: "Salesforce",
    };

    it("Add tool lists only tenant connections not already a tile here", () => {
      mcpConnectionsMock.mockReturnValue({ data: [PLAIN_CONNECTION, SALESFORCE_CONNECTION] });
      renderPanel();

      fireEvent.click(screen.getByRole("button", { name: /add tool/i }));

      expect(screen.getByText("Salesforce")).toBeInTheDocument();
      // "Odoo" already has a tile (it's in the frame) -- it must not also
      // appear as an addable option in the picker.
      expect(screen.queryAllByText("Odoo")).toHaveLength(1);
    });

    it("picking a connection in Add tool grants it read-only, enabled, with no login pin", () => {
      mcpConnectionsMock.mockReturnValue({ data: [PLAIN_CONNECTION, SALESFORCE_CONNECTION] });
      renderPanel();

      fireEvent.click(screen.getByRole("button", { name: /add tool/i }));
      fireEvent.click(screen.getByText("Salesforce"));

      expect(updateNarrowingMock).toHaveBeenCalledWith(
        {
          narrowing: {
            tools: {
              Odoo: {
                enabled: true,
                read: true,
                modify: false,
                approval_eur: null,
                approval_actions: [],
                only: [],
                connection_id: null,
              },
              Salesforce: {
                enabled: true,
                read: true,
                modify: false,
                approval_eur: null,
                approval_actions: [],
                only: [],
                connection_id: null,
              },
            },
          },
        },
        expect.anything(),
      );
    });

    it("Add tool is disabled once every tenant connection already has a tile", () => {
      mcpConnectionsMock.mockReturnValue({ data: [PLAIN_CONNECTION] });
      renderPanel();
      expect(screen.getByRole("button", { name: /add tool/i })).toBeDisabled();
    });

    it("excludes a connection the frame already mentions but left disabled -- adding it there always 422s", () => {
      // backend/src/oc8/authz/pdp.py's narrowing_within_frame keys off
      // presence in frame_tools, not its `enabled` flag: a tool the
      // department explicitly turned off is still frame-governed, never
      // agent-exclusive. Offering it in Add tool would just fail on save.
      mcpConnectionsMock.mockReturnValue({ data: [PLAIN_CONNECTION, SALESFORCE_CONNECTION] });
      renderPanel({
        ...ONE_TOOL_AGENT,
        departmentFrameTools: {
          ...ONE_TOOL_AGENT.departmentFrameTools,
          Salesforce: { enabled: false, read: false, modify: false, approvalEur: null },
        },
      });

      // Salesforce is the only other tenant connection and it's excluded --
      // nothing left to offer, so the button itself is disabled (clicking a
      // disabled button never opens the picker).
      expect(screen.getByRole("button", { name: /add tool/i })).toBeDisabled();
    });

    it("shows no remove button on a frame-inherited tile", () => {
      mcpConnectionsMock.mockReturnValue({ data: [PLAIN_CONNECTION, SALESFORCE_CONNECTION] });
      renderPanel();

      // Only Odoo (a frame tile) is on screen -- Add tool / Toggle are the
      // only two buttons, neither is a remove action.
      const buttons = screen.getAllByRole("button");
      expect(buttons).toHaveLength(2);
    });

    it("removing an agent-exclusive tile asks for confirmation, then persists it dropped entirely", async () => {
      mcpConnectionsMock.mockReturnValue({ data: [PLAIN_CONNECTION, SALESFORCE_CONNECTION] });
      renderPanel({
        ...ONE_TOOL_AGENT,
        effectiveTools: {
          ...ONE_TOOL_AGENT.effectiveTools,
          Salesforce: { enabled: true, read: true, modify: false, approvalEur: null },
        },
      });

      fireEvent.click(screen.getByRole("button", { name: /remove tool/i }));
      // Confirming must not have persisted yet -- the dialog is a real gate,
      // not a no-op wrapper around the click.
      expect(updateNarrowingMock).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole("button", { name: /^remove$/i }));

      await waitFor(() =>
        expect(updateNarrowingMock).toHaveBeenCalledWith(
          {
            narrowing: {
              tools: {
                Odoo: {
                  enabled: true,
                  read: true,
                  modify: false,
                  approval_eur: null,
                  approval_actions: [],
                  only: [],
                  connection_id: null,
                },
              },
            },
          },
          expect.anything(),
        ),
      );
    });

    it("cancelling the remove confirmation leaves the tile and never persists", () => {
      mcpConnectionsMock.mockReturnValue({ data: [PLAIN_CONNECTION, SALESFORCE_CONNECTION] });
      renderPanel({
        ...ONE_TOOL_AGENT,
        effectiveTools: {
          ...ONE_TOOL_AGENT.effectiveTools,
          Salesforce: { enabled: true, read: true, modify: false, approvalEur: null },
        },
      });

      fireEvent.click(screen.getByRole("button", { name: /remove tool/i }));
      fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

      expect(updateNarrowingMock).not.toHaveBeenCalled();
      expect(screen.getByText("Salesforce")).toBeInTheDocument();
    });

    it("marks an agent-exclusive tile 'Agent only', leaving frame tiles unmarked", () => {
      mcpConnectionsMock.mockReturnValue({ data: [PLAIN_CONNECTION, SALESFORCE_CONNECTION] });
      renderPanel({
        ...ONE_TOOL_AGENT,
        effectiveTools: {
          ...ONE_TOOL_AGENT.effectiveTools,
          Salesforce: { enabled: true, read: true, modify: false, approvalEur: null },
        },
        // departmentFrameTools deliberately still only has "Odoo" -- Salesforce
        // reaches effectiveTools purely via the agent's own narrowing.
      });

      // Exactly one tile (Salesforce, the agent-exclusive one) carries the
      // badge -- the frame tile (Odoo) must not also get it.
      expect(screen.getAllByText("Agent only")).toHaveLength(1);
      expect(screen.getByText("Odoo")).toBeInTheDocument();
      expect(screen.getByText("Salesforce")).toBeInTheDocument();
    });
  });
});
