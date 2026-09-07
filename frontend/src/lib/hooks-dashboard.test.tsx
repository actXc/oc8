import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { useDashboardLayout, useDashboardTemplates, useSaveDashboardLayout } from "@/lib/hooks";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), put: vi.fn() },
}));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useDashboardLayout", () => {
  it("surfaces null when the caller has no saved layout", async () => {
    vi.mocked(api.get).mockResolvedValueOnce(null);
    const { result } = renderHook(() => useDashboardLayout(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
    expect(api.get).toHaveBeenCalledWith("/dashboard/layout");
  });
});

describe("useSaveDashboardLayout", () => {
  it("PUTs the full widget list", async () => {
    const body = { widgets: [], templateId: null };
    vi.mocked(api.put).mockResolvedValueOnce(body);
    const { result } = renderHook(() => useSaveDashboardLayout(), { wrapper });
    result.current.mutate(body);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.put).toHaveBeenCalledWith("/dashboard/layout", body);
  });
});

describe("useDashboardTemplates", () => {
  it("fetches the template catalogue", async () => {
    vi.mocked(api.get).mockResolvedValueOnce([]);
    const { result } = renderHook(() => useDashboardTemplates(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.get).toHaveBeenCalledWith("/dashboard/templates");
  });
});
