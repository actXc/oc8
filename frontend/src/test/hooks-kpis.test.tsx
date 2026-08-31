import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useAgentKpis, useDepartmentKpis, useTenantKpis } from "@/lib/hooks";
import { api, ApiError } from "@/lib/api";

// Keeps the real `ApiError` class (needed for `instanceof` checks in
// useTenantKpis's `retry` predicate) while still mocking `api.get` itself.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { get: vi.fn() } };
});

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient();
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

// Reset the shared `api.get` mock between tests -- see kb-content-hooks.test.tsx's
// identical `beforeEach`/`mockReset` note.
beforeEach(() => {
  vi.mocked(api.get).mockReset();
});

const kpiData = {
  runCount: 12,
  totalDurationMs: 3600000,
  executionDurationMs: 1200000,
  approvalWaitMs: 900000,
  responseTimeMs: 45000,
  avgToolCallDurationMs: 2500,
};

describe("useAgentKpis", () => {
  it("fetches KPIs for an agent with no params", async () => {
    vi.mocked(api.get).mockResolvedValue(kpiData);
    const { result } = renderHook(() => useAgentKpis("agent-1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.get).toHaveBeenCalledWith("/agents/agent-1/kpis");
    expect(result.current.data).toEqual(kpiData);
  });

  it("passes dateFrom/dateTo through to the query string", async () => {
    vi.mocked(api.get).mockResolvedValue(kpiData);
    const { result } = renderHook(
      () => useAgentKpis("agent-1", { dateFrom: "2026-08-01", dateTo: "2026-08-20" }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const calledUrl = vi.mocked(api.get).mock.calls[0][0] as string;
    expect(calledUrl).toContain("/agents/agent-1/kpis?");
    expect(calledUrl).toContain("dateFrom=2026-08-01");
    expect(calledUrl).toContain("dateTo=2026-08-20");
  });
});

describe("useDepartmentKpis", () => {
  it("fetches KPIs for a department with no params", async () => {
    vi.mocked(api.get).mockResolvedValue(kpiData);
    const { result } = renderHook(() => useDepartmentKpis("dept-1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.get).toHaveBeenCalledWith("/departments/dept-1/kpis");
    expect(result.current.data).toEqual(kpiData);
  });

  it("passes dateFrom/dateTo through to the query string", async () => {
    vi.mocked(api.get).mockResolvedValue(kpiData);
    const { result } = renderHook(() => useDepartmentKpis("dept-1", { dateFrom: "2026-08-01" }), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const calledUrl = vi.mocked(api.get).mock.calls[0][0] as string;
    expect(calledUrl).toContain("/departments/dept-1/kpis?");
    expect(calledUrl).toContain("dateFrom=2026-08-01");
    expect(calledUrl).not.toContain("dateTo=");
  });
});

describe("useTenantKpis", () => {
  it("fetches tenant-wide KPIs with no params", async () => {
    vi.mocked(api.get).mockResolvedValue(kpiData);
    const { result } = renderHook(() => useTenantKpis(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.get).toHaveBeenCalledWith("/kpis");
    expect(result.current.data).toEqual(kpiData);
  });

  it("passes agentId/departmentId/groupBy/status/dateFrom/dateTo through to the query string", async () => {
    const grouped = { rows: [{ ...kpiData, groupKey: "agent-1" }] };
    vi.mocked(api.get).mockResolvedValue(grouped);
    const { result } = renderHook(
      () =>
        useTenantKpis({
          agentId: "agent-1",
          departmentId: "dept-1",
          groupBy: "day",
          status: "completed",
          dateFrom: "2026-08-01",
          dateTo: "2026-08-20",
        }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const calledUrl = vi.mocked(api.get).mock.calls[0][0] as string;
    expect(calledUrl).toContain("/kpis?");
    expect(calledUrl).toContain("agentId=agent-1");
    expect(calledUrl).toContain("departmentId=dept-1");
    expect(calledUrl).toContain("groupBy=day");
    expect(calledUrl).toContain("status=completed");
    expect(calledUrl).toContain("dateFrom=2026-08-01");
    expect(calledUrl).toContain("dateTo=2026-08-20");
    expect(result.current.data).toEqual(grouped);
  });

  it("omits undefined params from the query string", async () => {
    vi.mocked(api.get).mockResolvedValue(kpiData);
    const { result } = renderHook(() => useTenantKpis({ agentId: "agent-1" }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const calledUrl = vi.mocked(api.get).mock.calls[0][0] as string;
    expect(calledUrl).toBe("/kpis?agentId=agent-1");
  });

  it("does not retry a 422 -- the backend's deterministic MAX_BUCKETS/MAX_GROUPS cap rejection -- unlike a transient failure", async () => {
    // Deliberately uses the real QueryClient default (retry: 3, see
    // router.tsx) rather than `retry: false`, so this proves useTenantKpis's
    // own `retry` override actually suppresses the default rather than just
    // asserting behavior the test setup already forced.
    vi.mocked(api.get).mockRejectedValue(new ApiError("too many results", 422));
    const { result } = renderHook(() => useTenantKpis(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(api.get).toHaveBeenCalledTimes(1);
  });
});
