import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";

const useKbDocumentsMock = vi.fn();
const useKbChunksMock = vi.fn();
const useSimilarChunksMock = vi.fn();

vi.mock("@/lib/hooks", () => ({
  useKbDocuments: (kbId: string, opts: unknown) => useKbDocumentsMock(kbId, opts),
  useKbChunks: (kbId: string, params: unknown) => useKbChunksMock(kbId, params),
  useSimilarChunks: (kbId: string, chunkId: string | null) => useSimilarChunksMock(kbId, chunkId),
}));

// The route lives at `knowledge_.bases.$kbId.content.tsx` (trailing
// underscore on the "knowledge" segment) rather than the plan's illustrative
// `knowledge.bases.$kbId.content.tsx` -- see the file's own top-of-file
// comment for why: `knowledge.tsx` is a full page component (no `<Outlet />`),
// so a literal `knowledge.bases...` filename would be auto-nested under it
// by the TanStack Router file-based codegen and would never render.
import { KbContentView } from "@/routes/knowledge_.bases.$kbId.content";

function renderView() {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <KbContentView kbId="kb-1" />
    </QueryClientProvider>,
  );
}

describe("KbContentView", () => {
  const docA = { sourceUri: "doc-a", dataSourceId: null, kbId: "kb-1", chunks: 2, createdAt: null, deletedAt: null, deletedReason: null, reducedAt: null };
  const docB = { sourceUri: "doc-b", dataSourceId: null, kbId: "kb-1", chunks: 1, createdAt: null, deletedAt: null, deletedReason: null, reducedAt: null };
  const chunkFromA = { id: "c1", kbId: "kb-1", sourceUri: "doc-a", content: "content from doc a", classification: "internal", chunkMetadata: {}, createdAt: "2026-01-01" };
  const similar = [
    { id: "c2", kbId: "kb-1", sourceUri: "doc-b", content: "a related chunk", classification: "internal", chunkMetadata: {}, createdAt: "2026-01-02", similarity: 0.87 },
  ];

  // Stable references across renders/tests -- `mockReturnValue`, not a fresh
  // literal via `mockImplementation`, per this repo's established convention
  // (see `knowledge-list-toolbar.test.tsx`'s comment on the infinite-render
  // hazard of non-referentially-stable mocked query data).
  const docsPage = { data: [docA, docB], isLoading: false };
  const chunksPage = { data: { items: [chunkFromA], totalCount: 1 }, isLoading: false };
  const noSimilar = { data: undefined, isLoading: false };

  beforeEach(() => {
    useKbDocumentsMock.mockReset();
    useKbChunksMock.mockReset();
    useSimilarChunksMock.mockReset();
    useKbDocumentsMock.mockReturnValue(docsPage);
    useKbChunksMock.mockReturnValue(chunksPage);
    useSimilarChunksMock.mockReturnValue(noSimilar);
  });

  it("renders the document list and the chunks of the first document by default behavior is explicit selection", () => {
    renderView();
    expect(screen.getByText("doc-a")).toBeInTheDocument();
    expect(screen.getByText("doc-b")).toBeInTheDocument();
  });

  it("selecting a document filters the chunk pane to that document's sourceUri", async () => {
    renderView();
    fireEvent.click(screen.getByText("doc-a"));
    await waitFor(() =>
      expect(useKbChunksMock).toHaveBeenLastCalledWith(
        "kb-1",
        expect.objectContaining({ sourceUri: "doc-a" }),
      ),
    );
    expect(screen.getByText("content from doc a")).toBeInTheDocument();
  });

  it("typing in the search box switches to flat search mode, bypassing document selection", async () => {
    renderView();
    fireEvent.click(screen.getByText("doc-a")); // select a document first
    fireEvent.change(screen.getByPlaceholderText(/search this knowledge base/i), {
      target: { value: "invoice" },
    });
    await waitFor(() =>
      expect(useKbChunksMock).toHaveBeenLastCalledWith(
        "kb-1",
        expect.objectContaining({ search: "invoice", sourceUri: undefined }),
      ),
    );
  });

  it("clearing the search box returns to document-browse mode", async () => {
    renderView();
    const box = screen.getByPlaceholderText(/search this knowledge base/i);
    fireEvent.change(box, { target: { value: "invoice" } });
    fireEvent.change(box, { target: { value: "" } });
    fireEvent.click(screen.getByText("doc-b"));
    await waitFor(() =>
      expect(useKbChunksMock).toHaveBeenLastCalledWith(
        "kb-1",
        expect.objectContaining({ sourceUri: "doc-b", search: undefined }),
      ),
    );
  });

  it("clicking Find similar on a chunk shows its similar-chunks results", async () => {
    // Hoisted, stable references for both branches -- a fresh object/array
    // literal returned per call (even for the "no match" branch) risks the
    // same infinite-render-loop hazard this repo hit earlier if it ever ends
    // up feeding a useEffect; using fixed references here is defensive even
    // though KbContentView currently only reads this value in render.
    const similarResult = { data: similar, isLoading: false };
    useSimilarChunksMock.mockImplementation((kbId: string, chunkId: string | null) =>
      chunkId === "c1" ? similarResult : noSimilar,
    );
    renderView();
    fireEvent.click(screen.getByText("doc-a"));
    fireEvent.click(screen.getByRole("button", { name: /find similar/i }));
    await waitFor(() => expect(screen.getByText("a related chunk")).toBeInTheDocument());
    expect(screen.getByText(/87%/)).toBeInTheDocument();
  });

  // The endpoint 404s for a chunk with no embedding (an embedding model that
  // was unreachable at ingest leaves the column NULL) -- seen live on the dev
  // stack. The panel must say so rather than render nothing, which made the
  // button look dead.
  it("says so when a chunk has no similar chunks to show", async () => {
    const errored = { data: undefined, isLoading: false, isError: true };
    useSimilarChunksMock.mockImplementation((kbId: string, chunkId: string | null) =>
      chunkId === "c1" ? errored : noSimilar,
    );
    renderView();
    fireEvent.click(screen.getByText("doc-a"));
    fireEvent.click(screen.getByRole("button", { name: /find similar/i }));
    await waitFor(() =>
      expect(screen.getByText(/no similar chunks for this one yet/i)).toBeInTheDocument(),
    );
  });

  // Two data sources crawling overlapping scopes put the same source_uri in one
  // base twice, and `GET /documents` returns a row per (data_source_id,
  // source_uri) pair -- observed live. The list must not collapse or
  // mis-reconcile them (React keys on sourceUri alone were duplicated).
  it("renders both rows when two data sources hold the same source_uri", () => {
    const dupA = { ...docA, dataSourceId: "ds-1" };
    const dupB = { ...docA, dataSourceId: "ds-2" };
    useKbDocumentsMock.mockReturnValue({ data: [dupA, dupB], isLoading: false });
    renderView();
    expect(screen.getAllByTitle("doc-a")).toHaveLength(2);
  });

  // `useKbChunks` never received `page`/`pageSize` before this fix, so only
  // the first 20 chunks of any document or search result were ever reachable
  // -- with nothing on screen hinting more existed. `totalCount` beyond the
  // page size is what should surface a Previous/Next control.
  describe("pagination", () => {
    it("shows no pagination control when totalCount fits in one page", () => {
      useKbChunksMock.mockReturnValue({
        data: { items: [chunkFromA], totalCount: 1 },
        isLoading: false,
      });
      renderView();
      expect(screen.queryByRole("button", { name: /next/i })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /previous/i })).not.toBeInTheDocument();
    });

    it("shows Previous/Next and a page indicator when totalCount exceeds the page size", () => {
      useKbChunksMock.mockReturnValue({
        data: { items: [chunkFromA], totalCount: 45 },
        isLoading: false,
      });
      renderView();
      expect(screen.getByRole("button", { name: /next/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /previous/i })).toBeInTheDocument();
      expect(screen.getByText(/page 1 of 3/i)).toBeInTheDocument();
    });

    it("clicking Next advances the page and re-queries useKbChunks with the new page", async () => {
      useKbChunksMock.mockReturnValue({
        data: { items: [chunkFromA], totalCount: 45 },
        isLoading: false,
      });
      renderView();
      fireEvent.click(screen.getByRole("button", { name: /next/i }));
      await waitFor(() =>
        expect(useKbChunksMock).toHaveBeenLastCalledWith(
          "kb-1",
          expect.objectContaining({ page: 2, pageSize: 20 }),
        ),
      );
      expect(screen.getByText(/page 2 of 3/i)).toBeInTheDocument();
    });

    it("Previous is disabled on page 1 and Next is disabled on the last page", async () => {
      useKbChunksMock.mockReturnValue({
        data: { items: [chunkFromA], totalCount: 21 },
        isLoading: false,
      });
      renderView();
      expect(screen.getByRole("button", { name: /previous/i })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: /next/i }));
      await waitFor(() => expect(screen.getByText(/page 2 of 2/i)).toBeInTheDocument());
      expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
    });

    it("selecting a different document resets the page back to 1", async () => {
      useKbChunksMock.mockReturnValue({
        data: { items: [chunkFromA], totalCount: 45 },
        isLoading: false,
      });
      renderView();
      fireEvent.click(screen.getByRole("button", { name: /next/i }));
      await waitFor(() => expect(screen.getByText(/page 2 of 3/i)).toBeInTheDocument());
      fireEvent.click(screen.getByText("doc-b"));
      await waitFor(() =>
        expect(useKbChunksMock).toHaveBeenLastCalledWith(
          "kb-1",
          expect.objectContaining({ page: 1, sourceUri: "doc-b" }),
        ),
      );
    });
  });

  // `GET .../chunks` is gated at `knowledge:manage`, one tier above the
  // `knowledge:view` that gates `GET .../documents` -- a VIEW-only operator
  // reaches this page, the document list populates normally, and only the
  // chunk pane 403s. That must read as an honest permission message, not the
  // same "No chunks found." text a genuinely empty base would show.
  describe("permission-denied chunk pane", () => {
    it("shows a permission-denied message, not the empty-state message, on a 403", () => {
      useKbChunksMock.mockReturnValue({
        data: undefined,
        isLoading: false,
        isError: true,
        error: new ApiError("requires permission: knowledge:manage", 403),
      });
      renderView();
      expect(screen.getByText(/don't have permission to view this content/i)).toBeInTheDocument();
      expect(screen.queryByText(/no chunks found/i)).not.toBeInTheDocument();
    });

    it("still shows the generic empty-state message for a non-403 error", () => {
      useKbChunksMock.mockReturnValue({
        data: undefined,
        isLoading: false,
        isError: true,
        error: new ApiError("not found", 404),
      });
      renderView();
      expect(screen.getByText(/no chunks found/i)).toBeInTheDocument();
      expect(
        screen.queryByText(/don't have permission to view this content/i),
      ).not.toBeInTheDocument();
    });

    it("does not show a permission-denied message for a genuinely empty knowledge base", () => {
      useKbChunksMock.mockReturnValue({
        data: { items: [], totalCount: 0 },
        isLoading: false,
      });
      renderView();
      expect(screen.getByText(/no chunks found/i)).toBeInTheDocument();
      expect(
        screen.queryByText(/don't have permission to view this content/i),
      ).not.toBeInTheDocument();
    });
  });
});
