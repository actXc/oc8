// Task 20 (Design System Consistency plan): the Knowledge page's two
// independent lists -- DataSources and KnowledgeBases -- now each go through
// their own <ListToolbar> (Task 14) with their own local `ListQueryState`,
// threaded straight into `useDataSources(params)`/`useKnowledgeBases(params)`
// (Task 15). Unlike Skills/Agents/Departments (Tasks 16/18/19), archived rows
// here are READ-ONLY per the documented exception (DataSource/KnowledgeBase
// DELETE is irreversible -- it reduces the row's KbChunks in the same
// transaction, §12.5.1): a muted row with a "Deleted" badge, and NO Restore
// button/mutation for either model.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const useDataSourcesMock = vi.fn();
const useKnowledgeBasesMock = vi.fn();
const useSyncSourceMock = vi.fn();

vi.mock("@/lib/hooks", () => ({
  useDataSources: (params: unknown) => useDataSourcesMock(params),
  useKnowledgeBases: (params: unknown) => useKnowledgeBasesMock(params),
  useAgents: () => ({ data: { items: [], totalCount: 0 } }),
  useDepartments: () => ({ data: { items: [], totalCount: 0 } }),
  useCreateKnowledgeBase: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/lib/knowledge-connector-hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/knowledge-connector-hooks")>();
  return {
    ...actual,
    useKnowledgeConnectors: () => ({ data: [], isLoading: false }),
    useOAuthConnections: () => ({ data: [] }),
    useCreateSource: () => ({ mutate: vi.fn(), isPending: false }),
    useSyncSource: () => useSyncSourceMock(),
    useIngestionJob: () => ({ data: undefined }),
  };
});

import { KnowledgePage } from "@/routes/knowledge";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <KnowledgePage />
    </QueryClientProvider>,
  );
}

describe("Knowledge page", () => {
  beforeEach(() => {
    useDataSourcesMock.mockReset();
    useKnowledgeBasesMock.mockReset();
    useSyncSourceMock.mockReset();
    useDataSourcesMock.mockReturnValue({ data: { items: [], totalCount: 0 } });
    useKnowledgeBasesMock.mockReturnValue({ data: { items: [], totalCount: 0 } });
    useSyncSourceMock.mockReturnValue({ mutate: vi.fn(), isPending: false });
  });

  it("re-fetches DataSources with the typed search term", () => {
    renderPage();

    fireEvent.change(screen.getByPlaceholderText(/search data sources/i), {
      target: { value: "acme" },
    });

    const lastCall = useDataSourcesMock.mock.calls.at(-1)?.[0];
    expect(lastCall).toMatchObject({ search: "acme" });
  });

  it("re-fetches KnowledgeBases with the typed search term", () => {
    renderPage();

    // Switch to the Knowledge Bases tab -- its <ListToolbar> only mounts
    // while that tab is active.
    fireEvent.click(screen.getByRole("button", { name: /knowledge bases/i }));

    fireEvent.change(screen.getByPlaceholderText(/search knowledge bases/i), {
      target: { value: "sales" },
    });

    const lastCall = useKnowledgeBasesMock.mock.calls.at(-1)?.[0];
    expect(lastCall).toMatchObject({ search: "sales" });
  });

  it("shows a muted archived DataSource row with a Deleted badge and no Restore button", () => {
    useDataSourcesMock.mockReturnValue({
      data: {
        items: [
          {
            id: "ds-deleted",
            kind: "website",
            name: "Retired source",
            connected: true,
            deletedAt: "2026-08-01T00:00:00Z",
          },
        ],
        totalCount: 1,
      },
    });

    renderPage();

    fireEvent.click(screen.getByText(/show archived/i));

    expect(screen.getByText("Retired source")).toBeInTheDocument();
    expect(screen.getByText(/deleted/i)).toBeInTheDocument();
    expect(screen.queryByText(/restore/i)).not.toBeInTheDocument();
  });

  it("shows a muted archived KnowledgeBase row with a Deleted badge and no Restore button", () => {
    useKnowledgeBasesMock.mockReturnValue({
      data: {
        items: [
          {
            id: "kb-deleted",
            name: "Retired base",
            description: "",
            sourceIds: [],
            docs: 0,
            chunks: 0,
            embeddingModel: "google/gemini-embedding-2",
            sensitivity: "internal",
            updated: "—",
            status: "current",
            linkedDepartments: [],
            linkedAgents: [],
            roles: [],
            deletedAt: "2026-08-01T00:00:00Z",
          },
        ],
        totalCount: 1,
      },
    });

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /knowledge bases/i }));
    fireEvent.click(screen.getByText(/show archived/i));

    expect(screen.getByText("Retired base")).toBeInTheDocument();
    expect(screen.getByText(/deleted/i)).toBeInTheDocument();
    expect(screen.queryByText(/restore/i)).not.toBeInTheDocument();
  });

  // Fix-round regression test (Task 20 review finding): `bases` used to be a
  // single, page-level, unfiltered list shared by both tabs, and the Sources
  // tab's sync-target fallback (`kbForSource`) read straight from it. Task
  // 20 made the Bases tab's own list filterable/searchable via its
  // <ListToolbar> and backed by that same shared state, which meant filtering
  // it down to zero or one result would corrupt the Sources tab's sync
  // target too (false "create a KB first" error, or silently syncing into
  // the wrong KB). The fix gives SourcesTab its own dedicated, uncapped
  // `useKnowledgeBases({ pageSize: 200 })` lookup for this resolution,
  // completely decoupled from the Bases tab's filtered query.
  it("resolves the Sources tab's sync-target KB correctly even when the Bases tab's own filter has emptied its list", () => {
    const syncMutate = vi.fn();
    useSyncSourceMock.mockReturnValue({ mutate: syncMutate, isPending: false });

    useDataSourcesMock.mockReturnValue({
      data: {
        items: [
          {
            id: "ds-1",
            kind: "website",
            name: "Marketing site",
            connected: true,
          },
        ],
        totalCount: 1,
      },
    });

    const salesKb = {
      id: "kb-1",
      name: "Sales KB",
      description: "",
      sourceIds: ["ds-1"],
      docs: 0,
      chunks: 0,
      embeddingModel: "google/gemini-embedding-2",
      sensitivity: "internal",
      updated: "—",
      status: "current",
      linkedDepartments: [],
      linkedAgents: [],
      roles: [],
    };

    // Stable references across renders: `KnowledgePage`'s
    // `useEffect(() => setBases(fetchedBases), [fetchedBases])` assumes
    // `data.items` is referentially stable between identical calls (true for
    // the real React-Query-backed hook, but a naive `mockImplementation`
    // that builds a fresh object/array literal on every invocation breaks
    // that assumption and causes an infinite render loop in this test
    // environment). Hoist each branch's return value once and reuse it.
    const fullKbPage = { data: { items: [salesKb], totalCount: 1 } };
    const emptyKbPage = { data: { items: [], totalCount: 0 } };
    useKnowledgeBasesMock.mockImplementation(
      (params: { pageSize?: number; search?: string } = {}) => {
        // SourcesTab's dedicated, uncapped sync-target lookup -- always sees
        // the full, unfiltered set of knowledge bases.
        if (params.pageSize === 200) {
          return fullKbPage;
        }
        // The Bases tab's own paginated/filtered list, driven by its
        // <ListToolbar> search box.
        if (params.search === "no-such-kb") {
          return emptyKbPage;
        }
        return fullKbPage;
      },
    );

    renderPage();

    // Filter the Bases tab down to zero results.
    fireEvent.click(screen.getByRole("button", { name: /knowledge bases/i }));
    fireEvent.change(screen.getByPlaceholderText(/search knowledge bases/i), {
      target: { value: "no-such-kb" },
    });
    expect(screen.queryByText("Sales KB")).not.toBeInTheDocument();

    // Switch back to Sources and sync -- resolution must still find kb-1,
    // unaffected by the Bases tab's now-empty filtered list.
    fireEvent.click(screen.getByRole("button", { name: /data sources/i }));
    fireEvent.click(screen.getByRole("button", { name: /^sync$/i }));

    expect(syncMutate).toHaveBeenCalledWith(
      { sourceId: "ds-1", kbId: "kb-1" },
      expect.anything(),
    );
  });
});
