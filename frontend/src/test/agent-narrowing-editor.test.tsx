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

import { NarrowingEditor } from "@/routes/agents.$id";
import type { AgentDetail } from "@/lib/hooks-agent-detail";

const AGENT: AgentDetail = {
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

function renderEditor(agent: AgentDetail = AGENT) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <NarrowingEditor agent={agent} mayManage />
    </QueryClientProvider>,
  );
}

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

const ODOO_CONNECTION = {
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
  credentialType: "odoo_login",
  guardrailPresets: [],
  guardrailLibrary: null,
  hasValueSpec: false,
};

describe("NarrowingEditor credential picker", () => {
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

  it("shows the inline credential picker for a tool whose manifest declares a credential type", () => {
    mcpConnectionsMock.mockReturnValue({ data: [ODOO_CONNECTION] });
    renderEditor();
    expect(screen.getByText(/select a credential/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create new/i })).toBeInTheDocument();
  });

  it("renders no picker for a tool whose connection has no known credential type", () => {
    mcpConnectionsMock.mockReturnValue({ data: [] });
    renderEditor();
    expect(screen.queryByText(/select a credential/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /create new/i })).not.toBeInTheDocument();
  });

  it("selecting an existing credential that already backs a login reuses it and submits its id as connection_id", async () => {
    mcpConnectionsMock.mockReturnValue({ data: [ODOO_CONNECTION] });
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
    renderEditor();

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "cred-1" } });
    await waitFor(() =>
      expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("cred-1"),
    );
    expect(createMcpLoginMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

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
    );
  });

  it("creates a new credential inline and pins its login immediately -- no separate modal", async () => {
    mcpConnectionsMock.mockReturnValue({ data: [ODOO_CONNECTION] });
    createCredentialMock.mockResolvedValue({ id: "new-cred-1" });
    createMcpLoginMock.mockResolvedValue({
      id: "login-new",
      name: "Odoo",
      credentialId: "new-cred-1",
      departmentId: null,
      connected: false,
      scopes: {},
      health: {},
    });
    renderEditor();

    fireEvent.click(screen.getByRole("button", { name: /create new/i }));
    fireEvent.change(screen.getByLabelText("Username", { exact: false }), {
      target: { value: "user1" },
    });
    fireEvent.change(screen.getByLabelText("Name", { exact: true }), {
      target: { value: "oc8-local" },
    });
    // Two "Save" buttons are on screen right now -- CredentialPicker's own
    // inline create-form save, and NarrowingEditor's own bottom Save button
    // (always rendered). The first one in DOM order is CredentialPicker's.
    fireEvent.click(screen.getAllByRole("button", { name: /^save$/i })[0]);

    await waitFor(() =>
      expect(createCredentialMock).toHaveBeenCalledWith({
        name: "oc8-local",
        credentialType: "odoo_login",
        fieldValues: { username: "user1" },
      }),
    );
    await waitFor(() =>
      expect(createMcpLoginMock).toHaveBeenCalledWith({
        name: "Odoo",
        credentialType: "odoo_login",
        credentialId: "new-cred-1",
        scopes: [],
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

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
              connection_id: "login-new",
            },
          },
        },
      },
      expect.anything(),
    );
  });
});
