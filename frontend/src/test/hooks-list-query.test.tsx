// Task 15 (Design System Consistency plan): `useSkills` and its five siblings
// now take a `ListQueryParams` object and thread it into the query string
// `toQueryString` builds, consuming the shared `Page[T]` envelope every
// backend list-query endpoint returns (Tasks 3-9).
//
// `groupBy=category` here, NOT `group_by=category`: this asserts against
// `useSkills`'s OWN param object shape (camelCase, matching every other
// hook's `ListQueryParams`), not the wire. The wire assertion below is what
// actually confirms `/skills` gets the unaliased `group_by` its FastAPI route
// declares (`backend/src/oc8/api/v1/catalog.py::list_skills`) -- `/agents` and
// `/departments` alias it `groupBy` instead, which is why `toQueryString`
// takes a per-call-site wire name rather than a single hardcoded default.
import { describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useSkills } from "@/lib/hooks";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn() } }));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useSkills", () => {
  it("passes search/groupBy/page params through to the query string", async () => {
    vi.mocked(api.get).mockResolvedValue({ items: [], totalCount: 0 });
    const { result } = renderHook(
      () => useSkills({ search: "email", groupBy: "category", page: 2, pageSize: 10 }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining("search=email"));
    // `/skills` accepts `group_by` unaliased -- see the module doc comment.
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining("group_by=category"));
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining("offset=10"));
  });

  it("resolves .data to the Page[T] envelope, not a bare array", async () => {
    vi.mocked(api.get).mockResolvedValue({
      items: [{ id: "s1", name: "Skill 1" }],
      totalCount: 1,
    });
    const { result } = renderHook(() => useSkills(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.items).toHaveLength(1);
    expect(result.current.data?.totalCount).toBe(1);
  });
});
