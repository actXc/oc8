import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const updateNarrowingMock = vi.fn();
const mcpLoginsMock = vi.fn();
const mcpConnectionsMock = vi.fn();

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

describe("NarrowingEditor (permissions only)", () => {
  beforeEach(() => {
    updateNarrowingMock.mockReset();
    mcpLoginsMock.mockReset();
    mcpLoginsMock.mockReturnValue({ data: [] });
    mcpConnectionsMock.mockReset();
    mcpConnectionsMock.mockReturnValue({ data: [ODOO_CONNECTION] });
  });

  it("lists a tool already enabled by the Configuration tab's panel", () => {
    renderEditor();
    expect(screen.getByText("Odoo")).toBeInTheDocument();
  });

  it("shows no login picker or Assign-tool button -- those moved to Configuration", () => {
    renderEditor();
    expect(screen.queryByText(/select a credential/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /assign tool/i })).not.toBeInTheDocument();
  });

  it("points to Configuration when no tool is enabled yet", () => {
    renderEditor({
      ...AGENT,
      effectiveTools: {
        Odoo: { enabled: false, read: false, write: false, send: false, approvalEur: null },
      },
    });
    expect(screen.getByText(/turn one on under configuration/i)).toBeInTheDocument();
  });

  it("saving preserves the tool's existing enabled state and connection_id untouched", () => {
    renderEditor({
      ...AGENT,
      effectiveTools: {
        Odoo: {
          enabled: true,
          read: true,
          write: false,
          send: false,
          approvalEur: null,
          connectionId: "login-1",
        },
      },
    });

    screen.getByRole("button", { name: /^save$/i }).click();

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
});
