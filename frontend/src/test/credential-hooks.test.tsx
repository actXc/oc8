// frontend/src/test/credential-hooks.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(async (path: string) => {
      if (path.startsWith("/credentials")) return [{ id: "c1", name: "Prod S3", credentialType: "s3_api" }];
      if (path === "/credential-types") return [{ name: "s3_api", displayName: "S3", fields: [] }];
      throw new Error(`unexpected GET ${path}`);
    }),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

import { useCredentials, useCredentialTypes } from "@/lib/hooks";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("credential hooks", () => {
  it("useCredentials fetches the list", async () => {
    const { result } = renderHook(() => useCredentials(), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.[0].name).toBe("Prod S3");
  });

  it("useCredentialTypes fetches the type catalog", async () => {
    const { result } = renderHook(() => useCredentialTypes(), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.[0].name).toBe("s3_api");
  });
});
