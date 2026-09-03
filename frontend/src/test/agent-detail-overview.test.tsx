import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentDetail } from "@/lib/hooks-agent-detail";

const agentKpisMock = vi.fn();
const tenantKpisMock = vi.fn();

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useAgentKpis: (...args: unknown[]) => agentKpisMock(...args),
    useTenantKpis: (...args: unknown[]) => tenantKpisMock(...args),
  };
});

import { OverviewTab } from "@/routes/agents.$id";

const AGENT: AgentDetail = {
  id: "agent-1",
  name: "Nora",
  role: "Sales",
  llm: "claude-sonnet-5",
  provider: "anthropic",
  status: "running",
  tools: [],
  lastAction: "Sent a follow-up email",
  lastRun: "2 hours ago",
  tasksToday: 3,
  guardrails: [],
  schedule: "",
  avatarColor: "#000",
  departmentId: "dept-1",
  modelConfigId: null,
  isLead: false,
  mission: "",
  departmentName: "Vertrieb",
  effectiveTools: {},
  departmentFrameTools: {},
  narrowingTools: {},
  runtimeRef: null,
  currentRunId: null,
};

function renderOverview(agent: AgentDetail = AGENT) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OverviewTab agent={agent} />
    </QueryClientProvider>,
  );
}

describe("Agent overview tab KPI cards", () => {
  beforeEach(() => {
    agentKpisMock.mockReset();
    tenantKpisMock.mockReset();
    tenantKpisMock.mockReturnValue({ data: { rows: [] } });
  });

  it("renders the three KPI cards with the mocked, formatted numbers", () => {
    agentKpisMock.mockReturnValue({
      data: {
        runCount: 12,
        totalDurationMs: 200_000, // 3m 20s
        executionDurationMs: 120_000,
        approvalWaitMs: 1_200, // 1.2s
        responseTimeMs: 45_000,
        avgToolCallDurationMs: 2_500,
      },
    });
    renderOverview();

    expect(screen.getByText("Runs")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();

    expect(screen.getByText("Avg. duration")).toBeInTheDocument();
    expect(screen.getByText("3m 20s")).toBeInTheDocument();

    expect(screen.getByText("Approval wait")).toBeInTheDocument();
    expect(screen.getByText("1.2s")).toBeInTheDocument();

    // "Last action" panel is untouched by this task.
    expect(screen.getByText("Sent a follow-up email")).toBeInTheDocument();
  });

  it("falls back to an em dash for each KPI card while still loading", () => {
    agentKpisMock.mockReturnValue({ data: undefined });
    renderOverview();

    const dashes = screen.getAllByText("—");
    expect(dashes).toHaveLength(3);
  });

  it("renders the trend chart container without throwing given mocked trend rows", () => {
    agentKpisMock.mockReturnValue({ data: undefined });
    tenantKpisMock.mockReturnValue({
      data: {
        rows: [
          {
            groupKey: "2026-08-01",
            runCount: 3,
            totalDurationMs: null,
            executionDurationMs: null,
            approvalWaitMs: null,
            responseTimeMs: null,
            avgToolCallDurationMs: null,
          },
          {
            groupKey: "2026-08-02",
            runCount: 5,
            totalDurationMs: null,
            executionDurationMs: null,
            approvalWaitMs: null,
            responseTimeMs: null,
            avgToolCallDurationMs: null,
          },
        ],
      },
    });

    expect(() => renderOverview()).not.toThrow();
    expect(screen.getByText("Runs, last 30 days")).toBeInTheDocument();
    expect(tenantKpisMock).toHaveBeenCalledWith({ agentId: "agent-1", groupBy: "day" });
  });
});
