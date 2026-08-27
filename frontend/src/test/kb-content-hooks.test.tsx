import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useKbDocuments, useKbChunks, useSimilarChunks } from "@/lib/hooks";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn() } }));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient();
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

// Reset the shared `api.get` mock between tests -- without this, `mock.calls`
// accumulates across every `it()` in this file (this suite's convention: see
// knowledge-list-toolbar.test.tsx's `beforeEach`/`mockReset` pattern), and a
// later test's `mock.calls[0][0]` assertion would see an earlier test's call.
beforeEach(() => {
  vi.mocked(api.get).mockReset();
});

describe("useKbDocuments", () => {
  it("fetches the bare document array for a KB", async () => {
    const docs = [{ sourceUri: "doc-a", dataSourceId: null, kbId: "kb-1", chunks: 3, createdAt: null, deletedAt: null, deletedReason: null, reducedAt: null }];
    vi.mocked(api.get).mockResolvedValue(docs);
    const { result } = renderHook(() => useKbDocuments("kb-1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining("/knowledge/bases/kb-1/documents"));
    expect(result.current.data).toEqual(docs);
  });
});

describe("useKbChunks", () => {
  it("passes search/sourceUri/pagination params through to the query string", async () => {
    vi.mocked(api.get).mockResolvedValue({ items: [], totalCount: 0 });
    const { result } = renderHook(
      () => useKbChunks("kb-1", { search: "invoice", sourceUri: "doc-a", page: 2, pageSize: 10 }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const calledUrl = vi.mocked(api.get).mock.calls[0][0] as string;
    expect(calledUrl).toContain("/knowledge/bases/kb-1/chunks");
    expect(calledUrl).toContain("search=invoice");
    expect(calledUrl).toContain("sourceUri=doc-a");
    expect(calledUrl).toContain("offset=10");
    expect(calledUrl).toContain("limit=10");
  });
});

describe("useSimilarChunks", () => {
  it("is disabled until a chunkId is provided", () => {
    const { result } = renderHook(() => useSimilarChunks("kb-1", null), { wrapper });
    expect(result.current.fetchStatus).toBe("idle");
  });

  it("fetches similar chunks for a given chunkId", async () => {
    vi.mocked(api.get).mockResolvedValue([]);
    const { result } = renderHook(() => useSimilarChunks("kb-1", "chunk-1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.get).toHaveBeenCalledWith("/knowledge/bases/kb-1/chunks/chunk-1/similar");
  });
});
