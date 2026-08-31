import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const departmentKpisMock = vi.fn();

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useDepartmentKpis: (...args: unknown[]) => departmentKpisMock(...args),
  };
});

import { DepartmentOverviewStats } from "@/routes/departments.$id";

function renderStats(departmentId = "dept-1") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DepartmentOverviewStats departmentId={departmentId} />
    </QueryClientProvider>,
  );
}

describe("Department overview tab KPI cards", () => {
  beforeEach(() => {
    departmentKpisMock.mockReset();
  });

  it("renders the three KPI cards with the mocked, formatted numbers", () => {
    departmentKpisMock.mockReturnValue({
      data: {
        runCount: 42,
        totalDurationMs: 200_000, // 3m 20s
        executionDurationMs: 120_000,
        approvalWaitMs: 1_200, // 1.2s
        responseTimeMs: 45_000,
        avgToolCallDurationMs: 2_500,
      },
    });
    renderStats();

    expect(screen.getByText("Runs")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();

    expect(screen.getByText("Avg. duration")).toBeInTheDocument();
    expect(screen.getByText("3m 20s")).toBeInTheDocument();

    expect(screen.getByText("Approval wait")).toBeInTheDocument();
    expect(screen.getByText("1.2s")).toBeInTheDocument();

    expect(departmentKpisMock).toHaveBeenCalledWith("dept-1");
  });

  it("falls back to an em dash for each KPI card while still loading", () => {
    departmentKpisMock.mockReturnValue({ data: undefined });
    renderStats();

    const dashes = screen.getAllByText("—");
    expect(dashes).toHaveLength(3);
  });
});
