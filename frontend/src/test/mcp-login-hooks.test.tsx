import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(async (path: string) => {
      if (path.startsWith("/mcp/logins")) {
        return [
          {
            id: "l1",
            name: "Odoo (User 1)",
            credentialId: "c1",
            departmentId: null,
            connected: false,
            scopes: [],
            health: {},
          },
        ];
      }
      throw new Error(`unexpected GET ${path}`);
    }),
    post: vi.fn(),
  },
}));

import { useMcpLogins } from "@/lib/hooks";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useMcpLogins", () => {
  it("fetches the login list", async () => {
    const { result } = renderHook(() => useMcpLogins("odoo_login"), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.[0].name).toBe("Odoo (User 1)");
  });

  it("requests the unfiltered list when no credentialType is passed", async () => {
    const { api } = await import("@/lib/api");
    const { result } = renderHook(() => useMcpLogins(), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(api.get).toHaveBeenCalledWith("/mcp/logins");
  });
});
