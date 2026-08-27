import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { AgentInstructionsPanel } from "@/components/agent-instructions-panel";

const { getHistory, patchInstructions } = vi.hoisted(() => ({
  getHistory: vi.fn((_url: string) => ({ revisions: [], totalCount: 0, nextBeforeSeq: null })),
  patchInstructions: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    get: (url: string) => {
      if (url.includes("/instructions/history")) return getHistory(url);
      throw new Error(`unexpected GET ${url}`);
    },
    patch: (url: string, body: unknown) => {
      if (url.endsWith("/instructions")) return patchInstructions(body);
      throw new Error(`unexpected PATCH ${url}`);
    },
  },
}));

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("AgentInstructionsPanel", () => {
  beforeEach(() => {
    getHistory.mockReset();
    getHistory.mockReturnValue({ revisions: [] });
    patchInstructions.mockReset();
  });

  it("saves an edited instructions text and disables Save until something changes", async () => {
    patchInstructions.mockResolvedValue({ id: "agent-1", mission: "Work IT helpdesk tickets" });

    renderWithClient(
      <AgentInstructionsPanel agentId="agent-1" mission="Old mission" mayManage={true} />,
    );

    const saveButton = screen.getByRole("button", { name: /save/i });
    expect(saveButton).toBeDisabled();

    const textarea = screen.getByPlaceholderText(/sent with every run/i);
    fireEvent.change(textarea, { target: { value: "Work IT helpdesk tickets" } });
    expect(saveButton).not.toBeDisabled();

    fireEvent.click(saveButton);

    await waitFor(() =>
      expect(patchInstructions).toHaveBeenCalledWith({
        instructions: "Work IT helpdesk tickets",
      }),
    );
  });

  it("is read-only when the caller lacks agent:manage, with no Save or Restore controls", async () => {
    getHistory.mockReturnValue({
      revisions: [
        { ts: "2026-08-19T10:00:00Z", before: "legacy text", after: "Old mission", by: "justin" },
      ],
    });

    renderWithClient(
      <AgentInstructionsPanel agentId="agent-1" mission="Old mission" mayManage={false} />,
    );

    expect(screen.getByPlaceholderText(/sent with every run/i)).toBeDisabled();
    expect(screen.queryByRole("button", { name: /save/i })).not.toBeInTheDocument();
    await screen.findByText("legacy text");
    expect(screen.queryByRole("button", { name: /restore/i })).not.toBeInTheDocument();
  });

  it("numbers versions newest-first (current = highest, Default) down to v1", async () => {
    getHistory.mockReturnValue({
      revisions: [
        { ts: "2026-08-20T10:00:00Z", before: "beta text", after: "current text", by: "justin" },
        { ts: "2026-08-19T10:00:00Z", before: "alpha text", after: "beta text", by: "justin" },
      ],
    });

    renderWithClient(
      <AgentInstructionsPanel agentId="agent-1" mission="current text" mayManage={true} />,
    );

    await screen.findByText("alpha text");
    expect(screen.getByText("beta text")).toBeInTheDocument();
    // "current text" also appears in the editor's own textarea value, so
    // scope this assertion to the version list's preview paragraphs only.
    const previews = screen.getAllByText(
      (_, el) => el?.tagName === "P" && el.className.includes("line-clamp-2"),
    );
    expect(previews.map((p) => p.textContent)).toContain("current text");
    expect(screen.getAllByText("v1")).toHaveLength(1);
    expect(screen.getAllByText("v2")).toHaveLength(1);
    expect(screen.getAllByText("v3")).toHaveLength(1);
    expect(screen.getByText(/default/i)).toBeInTheDocument();
    expect(screen.getByText(/since creation/i)).toBeInTheDocument();
  });

  it("restoring a version loads its text into the editor without saving immediately", async () => {
    getHistory.mockReturnValue({
      revisions: [
        { ts: "2026-08-20T10:00:00Z", before: "beta text", after: "current text", by: "justin" },
        { ts: "2026-08-19T10:00:00Z", before: "alpha text", after: "beta text", by: "justin" },
      ],
    });

    renderWithClient(
      <AgentInstructionsPanel agentId="agent-1" mission="current text" mayManage={true} />,
    );

    await screen.findByText("beta text");
    const restoreButtons = screen.getAllByRole("button", { name: /restore/i });
    expect(restoreButtons).toHaveLength(2); // beta text (v2) and alpha text (v1), not current (v3)
    fireEvent.click(restoreButtons[0]);

    const textarea = screen.getByPlaceholderText(/sent with every run/i) as HTMLTextAreaElement;
    expect(textarea.value).toBe("beta text");
    expect(screen.getByRole("button", { name: /^save$/i })).not.toBeDisabled();
    expect(patchInstructions).not.toHaveBeenCalled();
  });

  it("lets you jump back to the still-current (Default) version after loading an older one", async () => {
    // Regression: the Default row's Restore button was gated on `!isCurrent`,
    // so once an older version was loaded into the editor there was no way
    // back to the actual current version without retyping it by hand.
    getHistory.mockReturnValue({
      revisions: [
        { ts: "2026-08-20T10:00:00Z", before: "beta text", after: "current text", by: "justin" },
        { ts: "2026-08-19T10:00:00Z", before: "alpha text", after: "beta text", by: "justin" },
      ],
    });

    renderWithClient(
      <AgentInstructionsPanel agentId="agent-1" mission="current text" mayManage={true} />,
    );

    await screen.findByText("alpha text");
    // Jump to the oldest version first, like the reported bug: v1, then try
    // to get back to v2 (the still-current/Default one).
    fireEvent.click(screen.getAllByRole("button", { name: /restore/i })[1]);

    const textarea = screen.getByPlaceholderText(/sent with every run/i) as HTMLTextAreaElement;
    expect(textarea.value).toBe("alpha text");

    // Two rows now offer Restore (beta text and the still-current default) --
    // pick the one sitting in the Default row specifically.
    const defaultBadge = screen.getByText(/default/i);
    const defaultRow = defaultBadge.closest("li")!;
    const backToCurrent = within(defaultRow).getByRole("button", { name: /restore/i });
    fireEvent.click(backToCurrent);
    expect(textarea.value).toBe("current text");
    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
    expect(patchInstructions).not.toHaveBeenCalled();
  });

  it("shows a single Default v1 entry with no Restore button when the agent has never been edited", async () => {
    renderWithClient(<AgentInstructionsPanel agentId="agent-1" mission="" mayManage={true} />);
    expect(await screen.findByText("v1")).toBeInTheDocument();
    expect(screen.getByText(/default/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /restore/i })).not.toBeInTheDocument();
  });

  it("loads history a page at a time and keeps version numbers correct across pages", async () => {
    // Guards the perf concern that prompted pagination: a hundred edits
    // must not all load at once. Two revisions total, split across two
    // pages of one each, with the true total (2) reported from page one --
    // v-numbering must stay 3/2/1 even before the older page is fetched.
    getHistory.mockImplementation((url: string) =>
      url.includes("beforeSeq")
        ? {
            revisions: [
              { ts: "2026-08-19T10:00:00Z", before: "old text", after: "mid text", by: "justin" },
            ],
            totalCount: 2,
            nextBeforeSeq: null,
          }
        : {
            revisions: [
              {
                ts: "2026-08-20T10:00:00Z",
                before: "mid text",
                after: "current text",
                by: "justin",
              },
            ],
            totalCount: 2,
            nextBeforeSeq: 555,
          },
    );

    renderWithClient(
      <AgentInstructionsPanel agentId="agent-1" mission="current text" mayManage={true} />,
    );

    await screen.findByText("mid text");
    expect(screen.getByText("v3")).toBeInTheDocument(); // current, Default
    expect(screen.getByText("v2")).toBeInTheDocument(); // mid text
    expect(screen.queryByText("v1")).not.toBeInTheDocument(); // not loaded yet
    expect(screen.queryByText(/since creation/i)).not.toBeInTheDocument();

    const loadMore = screen.getByRole("button", { name: /load older versions/i });
    fireEvent.click(loadMore);

    await screen.findByText("old text");
    expect(screen.getByText("v1")).toBeInTheDocument();
    expect(screen.getByText(/since creation/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /load older versions/i })).not.toBeInTheDocument();
  });
});
