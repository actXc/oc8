// Fix-round regression test (whole-branch review finding, Design System
// Consistency plan): the Knowledge page's DataSources/KnowledgeBases search
// placeholders, the "Deleted" archived-badge text, and the archived-toggle
// label were still hardcoded English literals -- every other list page in
// this plan (Skills/Agents/Departments) routes its equivalent strings
// through `useT()` (see e.g. `agents-list-toolbar.test.tsx`). Isolated in
// its own file (rather than added to `knowledge-list-toolbar.test.tsx`) so
// forcing the translator to always return German here can't leak into that
// file's English-text assertions -- Vitest gives each test file its own
// module registry, so this file's `@/lib/i18n` mock is scoped to it alone.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/i18n", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/i18n")>();
  return { ...actual, useT: () => (_en: string, de: string) => de };
});

const useDataSourcesMock = vi.fn();
const useKnowledgeBasesMock = vi.fn();

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
    useSyncSource: () => ({ mutate: vi.fn(), isPending: false }),
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

describe("Knowledge page i18n", () => {
  beforeEach(() => {
    useDataSourcesMock.mockReset();
    useKnowledgeBasesMock.mockReset();
    useDataSourcesMock.mockReturnValue({ data: { items: [], totalCount: 0 } });
    useKnowledgeBasesMock.mockReturnValue({ data: { items: [], totalCount: 0 } });
  });

  it("translates the DataSources search placeholder and archived-toggle label instead of using a hardcoded English string", () => {
    renderPage();

    expect(screen.getByPlaceholderText("Datenquellen suchen…")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/search data sources/i)).not.toBeInTheDocument();
    expect(screen.getByText("Archivierte anzeigen")).toBeInTheDocument();
    expect(screen.queryByText(/^show archived$/i)).not.toBeInTheDocument();
  });

  it("translates the KnowledgeBases search placeholder and archived-toggle label instead of using a hardcoded English string", () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /wissensdatenbanken/i }));

    expect(screen.getByPlaceholderText("Wissensdatenbanken suchen…")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/search knowledge bases/i)).not.toBeInTheDocument();
    expect(screen.getByText("Archivierte anzeigen")).toBeInTheDocument();
  });

  it("translates the archived DataSource badge instead of the hardcoded 'Deleted' string", () => {
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

    fireEvent.click(screen.getByText("Archivierte anzeigen"));

    expect(screen.getByText("Gelöscht")).toBeInTheDocument();
    expect(screen.queryByText(/^deleted$/i)).not.toBeInTheDocument();
  });

  it("translates the archived KnowledgeBase badge instead of the hardcoded 'Deleted' string", () => {
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

    fireEvent.click(screen.getByRole("button", { name: /wissensdatenbanken/i }));
    fireEvent.click(screen.getByText("Archivierte anzeigen"));

    expect(screen.getByText("Gelöscht")).toBeInTheDocument();
    expect(screen.queryByText(/^deleted$/i)).not.toBeInTheDocument();
  });
});
