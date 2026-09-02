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
    Odoo: { enabled: true, read: true, write: false, send: false, approvalEur: null },
  },
  departmentFrameTools: {
    Odoo: { enabled: true, read: true, write: false, send: false, approvalEur: null },
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
        Odoo: { enabled: true, read: true, write: false, send: false, approvalEur: null },
        Hubspot: { enabled: true, read: true, write: false, send: false, approvalEur: null },
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
        Hubspot: { enabled: true, read: false, write: false, send: false, approvalEur: null },
      },
    });
    expect(screen.getByText("Hubspot")).toBeInTheDocument();
    expect(screen.getByText(/not connected yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("toggling a connected tool off persists immediately, preserving its other fields", () => {
    mcpConnectionsMock.mockReturnValue({ data: [PLAIN_CONNECTION] });
    renderPanel();

    fireEvent.click(screen.getByRole("button"));

    expect(updateNarrowingMock).toHaveBeenCalledWith(
      {
        narrowing: {
          tools: {
            Odoo: {
              enabled: false,
              read: true,
              write: false,
              send: false,
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
                write: false,
                send: false,
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
});
