import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { useRuntimes } from "@/lib/hooks";

const { getRuntimes } = vi.hoisted(() => ({
  getRuntimes: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    get: (path: string) => (path === "/runtimes" ? getRuntimes() : Promise.resolve([])),
  },
}));

function createWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

describe("useRuntimes", () => {
  it("fetches and returns the runtime list", async () => {
    getRuntimes.mockResolvedValue([
      {
        id: null,
        name: "oc8.agent-runtime",
        label: "Default",
        summary: "",
        capabilities: [],
        isDefault: true,
        available: true,
        unavailableReason: null,
      },
    ]);

    const { result } = renderHook(() => useRuntimes(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.data).toHaveLength(1));
    expect(result.current.data?.[0].label).toBe("Default");
  });
});
