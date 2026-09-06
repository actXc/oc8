import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  canMock,
  assistantMock,
  sessionsMock,
  createSessionMock,
  messagesMock,
  sendMessageMock,
  renameSessionMock,
  deleteSessionMock,
  proposalsMock,
  applyProposalMock,
  rejectProposalMock,
} = vi.hoisted(() => ({
  canMock: vi.fn(() => true),
  assistantMock: vi.fn(),
  sessionsMock: vi.fn(),
  createSessionMock: vi.fn(),
  messagesMock: vi.fn(),
  sendMessageMock: vi.fn(),
  renameSessionMock: vi.fn(),
  deleteSessionMock: vi.fn(),
  proposalsMock: vi.fn(),
  applyProposalMock: vi.fn(),
  rejectProposalMock: vi.fn(),
}));

vi.mock("@/lib/governance-hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/governance-hooks")>();
  return { ...actual, useCan: () => canMock };
});

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useAssistant: () => assistantMock(),
    useCopilotProposals: (options?: unknown) => proposalsMock(options),
    useApplyCopilotProposal: () => ({ mutate: applyProposalMock, isPending: false }),
    useRejectCopilotProposal: () => ({ mutate: rejectProposalMock, isPending: false }),
  };
});

vi.mock("@/lib/hooks-chat", () => ({
  // Forwarding the real args (instead of ignoring them) lets a test give
  // different tabs -- now genuinely distinct CopilotChatTab instances, each
  // with its own sessionId -- different mocked data via mockImplementation;
  // every existing test still works unchanged since they configure these
  // with mockReturnValue, which answers the same value regardless of args.
  useChatSessions: (agentId?: string) => sessionsMock(agentId),
  useCreateChatSession: () => ({ mutate: createSessionMock, isPending: false }),
  useChatMessages: (sessionId: string | null) => messagesMock(sessionId),
  useSendChatMessage: () => ({ mutate: sendMessageMock, isPending: false }),
  useRenameChatSession: () => ({ mutate: renameSessionMock, isPending: false }),
  useDeleteChatSession: () => ({ mutate: deleteSessionMock, isPending: false }),
}));

import { CopilotDock } from "@/components/copilot-dock";

function renderDock() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(
    <QueryClientProvider client={qc}>
      <CopilotDock />
    </QueryClientProvider>,
  );
  return { ...result, qc };
}

function openDock() {
  fireEvent.click(screen.getByRole("button", { name: /oc8 copilot/i }));
}

describe("CopilotDock", () => {
  beforeEach(() => {
    // Tabs are now persisted to localStorage (scoped by member id) -- clear
    // it so one test's open tabs can't leak into the next test's initial
    // render (every test here resolves the same "anon" member id, since none
    // of them mock useAuth to return a signed-in member).
    localStorage.clear();
    canMock.mockReset();
    canMock.mockImplementation(() => true);
    assistantMock.mockReset();
    assistantMock.mockReturnValue({ data: { agentId: "assistant-1" } });
    sessionsMock.mockReset();
    sessionsMock.mockReturnValue({ data: [] });
    createSessionMock.mockReset();
    messagesMock.mockReset();
    messagesMock.mockReturnValue({ data: undefined });
    sendMessageMock.mockReset();
    renameSessionMock.mockReset();
    deleteSessionMock.mockReset();
    proposalsMock.mockReset();
    proposalsMock.mockReturnValue({ data: [] });
    applyProposalMock.mockReset();
    rejectProposalMock.mockReset();
  });

  it("renders nothing for a caller without copilot:use, and never even calls the chat-pipeline hooks", () => {
    canMock.mockImplementation(() => false);
    renderDock();
    expect(screen.queryByRole("button", { name: /oc8 copilot/i })).not.toBeInTheDocument();
    // The permission check has to happen BEFORE useAssistant/useChatSessions
    // are called, not just before their result is rendered -- otherwise
    // every signed-in user fires a GET /assistant + GET /chat/sessions (a
    // 403 for anyone without copilot:use) on every page load.
    expect(assistantMock).not.toHaveBeenCalled();
    expect(sessionsMock).not.toHaveBeenCalled();
  });

  it("shows a greeting and no session yet until the caller sends a first message", () => {
    renderDock();
    openDock();
    expect(screen.getByText(/i know your agents, departments and guardrails/i)).toBeInTheDocument();
    expect(sendMessageMock).not.toHaveBeenCalled();
    expect(createSessionMock).not.toHaveBeenCalled();
  });

  it("defaults to the Assistant's most recent existing session instead of starting a new one", () => {
    sessionsMock.mockReturnValue({
      data: [{ id: "s1", agentId: "assistant-1", title: "", createdAt: "t", lastMessageAt: null }],
    });
    messagesMock.mockReturnValue({
      data: [
        {
          id: "m1",
          sessionId: "s1",
          role: "user",
          content: "Hallo",
          runId: null,
          renderedComponents: [],
          createdAt: "t",
        },
        {
          id: "m2",
          sessionId: "s1",
          role: "assistant",
          content: "Hi, wie kann ich helfen?",
          runId: "r1",
          renderedComponents: [],
          createdAt: "t",
        },
      ],
    });
    renderDock();
    openDock();
    expect(screen.getByText("Hallo")).toBeInTheDocument();
    expect(screen.getByText("Hi, wie kann ich helfen?")).toBeInTheDocument();
  });

  it("with no existing session, sending a first message creates one and then sends the message", () => {
    createSessionMock.mockImplementation(
      (agentId: string, opts?: { onSuccess?: (s: unknown) => void }) => {
        opts?.onSuccess?.({
          id: "new-session",
          agentId,
          title: "",
          createdAt: "t",
          lastMessageAt: null,
        });
      },
    );
    renderDock();
    openDock();

    const textarea = screen.getByPlaceholderText(/configure or ask oc8/i);
    fireEvent.change(textarea, { target: { value: "What needs approval?" } });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(createSessionMock).toHaveBeenCalledWith("assistant-1", expect.anything());
    expect(sendMessageMock).toHaveBeenCalledWith("What needs approval?", expect.anything());
  });

  it("sends directly, without creating a session, once one already exists", () => {
    sessionsMock.mockReturnValue({
      data: [{ id: "s1", agentId: "assistant-1", title: "", createdAt: "t", lastMessageAt: null }],
    });
    messagesMock.mockReturnValue({ data: [] });
    renderDock();
    openDock();

    fireEvent.change(screen.getByPlaceholderText(/configure or ask oc8/i), {
      target: { value: "Cost this month?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(createSessionMock).not.toHaveBeenCalled();
    expect(sendMessageMock).toHaveBeenCalledWith("Cost this month?", expect.anything());
  });

  it("on a failed send, shows an unavailable notice and restores the typed text instead of losing it", () => {
    sessionsMock.mockReturnValue({
      data: [{ id: "s1", agentId: "assistant-1", title: "", createdAt: "t", lastMessageAt: null }],
    });
    messagesMock.mockReturnValue({ data: [] });
    sendMessageMock.mockImplementation((_text: string, opts?: { onError?: () => void }) =>
      opts?.onError?.(),
    );
    renderDock();
    openDock();

    const textarea = screen.getByPlaceholderText(/configure or ask oc8/i) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "Cost this month?" } });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(screen.getByText(/unavailable right now/i)).toBeInTheDocument();
    expect(textarea.value).toBe("Cost this month?");
  });

  it("on a failed session creation, shows an unavailable notice and restores the typed text", () => {
    createSessionMock.mockImplementation((_agentId: string, opts?: { onError?: () => void }) =>
      opts?.onError?.(),
    );
    renderDock();
    openDock();

    const textarea = screen.getByPlaceholderText(/configure or ask oc8/i) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "What needs approval?" } });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(screen.getByText(/unavailable right now/i)).toBeInTheDocument();
    expect(textarea.value).toBe("What needs approval?");
    expect(sendMessageMock).not.toHaveBeenCalled();
  });

  it("shows a thinking indicator and disables sending while the last turn is the caller's own", () => {
    sessionsMock.mockReturnValue({
      data: [{ id: "s1", agentId: "assistant-1", title: "", createdAt: "t", lastMessageAt: null }],
    });
    messagesMock.mockReturnValue({
      data: [
        {
          id: "m1",
          sessionId: "s1",
          role: "user",
          content: "Was wartet auf Freigabe?",
          runId: null,
          renderedComponents: [],
          createdAt: "t",
        },
      ],
    });
    renderDock();
    openDock();

    fireEvent.change(screen.getByPlaceholderText(/configure or ask oc8/i), {
      target: { value: "Another question" },
    });
    const sendButton = screen.getByRole("button", { name: /^send$/i });
    expect(sendButton).toBeDisabled();
    fireEvent.click(sendButton);
    expect(sendMessageMock).not.toHaveBeenCalled();
  });

  it("renders a rendered component attached to an assistant reply", () => {
    sessionsMock.mockReturnValue({
      data: [{ id: "s1", agentId: "assistant-1", title: "", createdAt: "t", lastMessageAt: null }],
    });
    messagesMock.mockReturnValue({
      data: [
        {
          id: "m2",
          sessionId: "s1",
          role: "assistant",
          content: "Hier ist dein Bericht.",
          runId: "r1",
          renderedComponents: [
            {
              componentKey: "record_card",
              props: { title: "Wochenbericht", fields: [{ label: "Stunden", value: "38.5" }] },
            },
          ],
          createdAt: "t",
        },
      ],
    });
    renderDock();
    openDock();
    expect(screen.getByText("Wochenbericht")).toBeInTheDocument();
  });

  // --- propose_change proposals are reviewable in the dock ---------------
  //
  // The Assistant's propose_change tool may only ever DRAFT. Between the dock
  // rewrite and this, nothing in the app rendered a proposal: the apply/reject
  // mutations existed with zero call sites, so every structural change the
  // Assistant proposed -- on the web or over Telegram -- sat in the database
  // with no way for anyone to answer it.

  const draft = {
    id: "p1",
    status: "draft",
    revision: 1,
    operations: [{ label: "agent.mission.set", references: { agentId: "a-1" } }],
  };

  it("renders a pending proposal with its operations", () => {
    proposalsMock.mockReturnValue({ data: [draft] });
    renderDock();
    openDock();
    expect(screen.getByRole("region", { name: /pending proposals/i })).toBeInTheDocument();
    expect(screen.getByText("agent.mission.set")).toBeInTheDocument();
    expect(screen.getByText("a-1")).toBeInTheDocument();
  });

  it("Apply calls the apply endpoint for that proposal and the card stops showing", () => {
    proposalsMock.mockReturnValue({ data: [draft] });
    renderDock();
    openDock();
    fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));
    expect(applyProposalMock).toHaveBeenCalledWith(
      "p1",
      expect.objectContaining({ onError: expect.any(Function) }),
    );
    expect(rejectProposalMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: /pending proposals/i })).not.toBeInTheDocument();
  });

  it("Reject calls the reject endpoint for that proposal and the card stops showing", () => {
    proposalsMock.mockReturnValue({ data: [draft] });
    renderDock();
    openDock();
    fireEvent.click(screen.getByRole("button", { name: /^reject$/i }));
    expect(rejectProposalMock).toHaveBeenCalledWith(
      "p1",
      expect.objectContaining({ onError: expect.any(Function) }),
    );
    expect(applyProposalMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: /pending proposals/i })).not.toBeInTheDocument();
  });

  // A failed apply/reject is the case the optimistic mark gets WRONG: the card
  // was hidden the moment the button was pressed and never came back, so the
  // reader was left believing a structural change went through while the
  // proposal was in fact still sitting there as `draft`.

  it("a failed Apply puts the still-draft card back and says so", () => {
    proposalsMock.mockReturnValue({ data: [draft] });
    applyProposalMock.mockImplementation((_id: string, options: { onError: () => void }) => {
      options.onError();
    });
    renderDock();
    openDock();
    fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));

    expect(screen.getByRole("region", { name: /pending proposals/i })).toBeInTheDocument();
    expect(screen.getByText("agent.mission.set")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^apply$/i })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be answered/i);
  });

  it("a failed Reject puts the still-draft card back and says so", () => {
    proposalsMock.mockReturnValue({ data: [draft] });
    rejectProposalMock.mockImplementation((_id: string, options: { onError: () => void }) => {
      options.onError();
    });
    renderDock();
    openDock();
    fireEvent.click(screen.getByRole("button", { name: /^reject$/i }));

    expect(screen.getByRole("region", { name: /pending proposals/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^reject$/i })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be answered/i);
  });

  it("a successful answer after a failed one clears the notice", () => {
    proposalsMock.mockReturnValue({ data: [draft] });
    applyProposalMock.mockImplementationOnce((_id: string, options: { onError: () => void }) => {
      options.onError();
    });
    renderDock();
    openDock();
    fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));
    expect(screen.getByRole("alert")).toBeInTheDocument();

    // The second press succeeds -- the once-mock is spent, so nothing calls
    // onError -- and the stale notice must not outlive it.
    fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("an already applied or rejected proposal is never offered for review", () => {
    proposalsMock.mockReturnValue({
      data: [
        { ...draft, id: "p-applied", status: "applied" },
        { ...draft, id: "p-rejected", status: "rejected" },
      ],
    });
    renderDock();
    openDock();
    expect(screen.queryByRole("region", { name: /pending proposals/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^apply$/i })).not.toBeInTheDocument();
  });

  it("shows no proposals section at all when nothing is waiting", () => {
    renderDock();
    openDock();
    expect(screen.queryByRole("region", { name: /pending proposals/i })).not.toBeInTheDocument();
  });

  it("does not fetch or poll pending proposals for a caller who only holds copilot:use", () => {
    canMock.mockImplementation((p: string) => p === "copilot:use");
    renderDock();
    openDock();
    expect(proposalsMock).toHaveBeenCalledWith(expect.objectContaining({ enabled: false }));
  });

  it("shows pending proposals but hides Apply/Reject for a caller without copilot:manage", () => {
    canMock.mockImplementation((p: string) => p === "copilot:use" || p === "copilot:view");
    proposalsMock.mockReturnValue({ data: [draft] });
    renderDock();
    openDock();
    expect(screen.getByRole("region", { name: /pending proposals/i })).toBeInTheDocument();
    expect(screen.getByText("agent.mission.set")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^apply$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^reject$/i })).not.toBeInTheDocument();
  });

  it("closing and reopening the dock keeps the same session (no reset on remount-free toggle)", () => {
    sessionsMock.mockReturnValue({
      data: [{ id: "s1", agentId: "assistant-1", title: "", createdAt: "t", lastMessageAt: null }],
    });
    messagesMock.mockReturnValue({
      data: [
        {
          id: "m1",
          sessionId: "s1",
          role: "user",
          content: "Hallo",
          runId: null,
          renderedComponents: [],
          createdAt: "t",
        },
        {
          id: "m2",
          sessionId: "s1",
          role: "assistant",
          content: "Hi, wie kann ich helfen?",
          runId: "r1",
          renderedComponents: [],
          createdAt: "t",
        },
      ],
    });
    renderDock();
    openDock();
    expect(screen.getByText("Hallo")).toBeInTheDocument();
    // Close (toggle button becomes the "close" affordance while open).
    fireEvent.click(screen.getByRole("button", { name: /oc8 copilot/i }));
    expect(screen.queryByText("Hallo")).not.toBeInTheDocument();
    // Reopen -- transcript is still there because it lives on the server,
    // not in local component state.
    openDock();
    expect(screen.getByText("Hallo")).toBeInTheDocument();
  });

  it("shows the session picker once more than one session exists, and switches on selection", () => {
    sessionsMock.mockReturnValue({
      data: [
        {
          id: "s1",
          agentId: "assistant-1",
          title: "Erste Frage",
          createdAt: "2026-01-01T00:00:00Z",
          lastMessageAt: null,
        },
        {
          id: "s2",
          agentId: "assistant-1",
          title: "Zweite Frage",
          createdAt: "2026-01-02T00:00:00Z",
          lastMessageAt: null,
        },
      ],
    });
    renderDock();
    openDock();
    // The picker's trigger renders the current session's title in a
    // `span.truncate` -- scope to that so this doesn't also match the menu
    // item once the dropdown is open (its own row also carries the title).
    expect(screen.getByText("Erste Frage", { selector: "span.truncate" })).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByText("Erste Frage", { selector: "span.truncate" }), {
      button: 0,
    });
    fireEvent.click(screen.getByText("Zweite Frage"));
    // The picker's trigger now shows the newly-selected session's title.
    expect(screen.getByText("Zweite Frage", { selector: "span.truncate" })).toBeInTheDocument();
    expect(
      screen.queryByText("Erste Frage", { selector: "span.truncate" }),
    ).not.toBeInTheDocument();
  });

  it("a new chat button resets to the lazy-creation state even when other sessions exist", () => {
    // One existing session, auto-selected on mount -- the exact case where,
    // without this button, there was previously no way back to a blank,
    // not-yet-created session at all.
    sessionsMock.mockReturnValue({
      data: [
        {
          id: "s1",
          agentId: "assistant-1",
          title: "Erste Frage",
          createdAt: "t",
          lastMessageAt: null,
        },
      ],
    });
    createSessionMock.mockImplementation(
      (agentId: string, opts?: { onSuccess?: (s: unknown) => void }) => {
        opts?.onSuccess?.({
          id: "new-session",
          agentId,
          title: "",
          createdAt: "t",
          lastMessageAt: null,
        });
      },
    );
    renderDock();
    openDock();

    fireEvent.click(screen.getByRole("button", { name: /new chat/i }));

    // Same shape as "with no existing session, sending a first message
    // creates one and then sends the message" -- proving the button put the
    // dock back into that same lazy-creation state, rather than the reset
    // being silently clobbered by the bootstrap effect that auto-selects
    // the existing session.
    const textarea = screen.getByPlaceholderText(/configure or ask oc8/i);
    fireEvent.change(textarea, { target: { value: "Fresh question" } });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(createSessionMock).toHaveBeenCalledWith("assistant-1", expect.anything());
    expect(sendMessageMock).toHaveBeenCalledWith("Fresh question", expect.anything());
  });

  it("a new chat button still works right after a tenant's very first session is created lazily", () => {
    // No sessions at all until the first message creates one -- the bootstrap
    // effect never runs (sessions.length is 0 the whole time), so it's
    // send()'s own onSuccess, not the effect, that has to arm the ref.
    // sessionsMock is stateful here to mimic the query-invalidation refetch
    // that lands the new session in `sessions` shortly after creation.
    let sessions: Array<{
      id: string;
      agentId: string;
      title: string;
      createdAt: string;
      lastMessageAt: string | null;
    }> = [];
    sessionsMock.mockImplementation(() => ({ data: sessions }));
    createSessionMock.mockImplementation(
      (agentId: string, opts?: { onSuccess?: (s: unknown) => void }) => {
        const created = {
          id: "new-session",
          agentId,
          title: "",
          createdAt: "t",
          lastMessageAt: null,
        };
        sessions = [created];
        opts?.onSuccess?.(created);
      },
    );
    renderDock();
    openDock();

    fireEvent.change(screen.getByPlaceholderText(/configure or ask oc8/i), {
      target: { value: "First message" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    expect(createSessionMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /new chat/i }));
    fireEvent.change(screen.getByPlaceholderText(/configure or ask oc8/i), {
      target: { value: "Second message" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    // Without arming the ref in send()'s onSuccess, the bootstrap effect
    // would silently re-select the just-created session on this click,
    // making this a plain send instead of a new lazy creation.
    expect(createSessionMock).toHaveBeenCalledTimes(2);
  });

  // --- Task 16: a persisted tab bar, replacing the single-session dock -----

  it("opens a new blank tab without disturbing the currently active tab's session", () => {
    sessionsMock.mockReturnValue({
      data: [
        {
          id: "s1",
          agentId: "assistant-1",
          title: "Erste Frage",
          createdAt: "t",
          lastMessageAt: null,
        },
      ],
    });
    // The two tabs will have distinct sessionId props (s1 vs null) -- key
    // the mock off that (like the real hook would key its query off it)
    // instead of a single mockReturnValue, or both tabs would show the same
    // transcript regardless of which session they're actually on.
    messagesMock.mockImplementation((sessionId: string | null) =>
      sessionId === "s1"
        ? {
            data: [
              {
                id: "m1",
                sessionId: "s1",
                role: "user",
                content: "Hallo",
                runId: null,
                renderedComponents: [],
                createdAt: "t",
              },
              {
                id: "m2",
                sessionId: "s1",
                role: "assistant",
                content: "Hi, wie kann ich helfen?",
                runId: "r1",
                renderedComponents: [],
                createdAt: "t",
              },
            ],
          }
        : { data: undefined },
    );
    renderDock();
    openDock();
    expect(screen.getByText("Hallo")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^new tab$/i }));

    // The new tab is blank and active -- the first tab's transcript is not
    // rendered while it isn't the active one.
    expect(screen.queryByText("Hallo")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText(/configure or ask oc8/i)).toBeInTheDocument();

    // Switching back to the first tab shows its session is untouched.
    fireEvent.click(screen.getByRole("button", { name: /erste frage tab/i }));
    expect(screen.getByText("Hallo")).toBeInTheDocument();
  });

  it("switching tabs preserves each tab's own composer draft text", () => {
    renderDock();
    openDock();
    fireEvent.change(screen.getByPlaceholderText(/configure or ask oc8/i), {
      target: { value: "Draft in tab one" },
    });

    fireEvent.click(screen.getByRole("button", { name: /^new tab$/i }));
    fireEvent.change(screen.getByPlaceholderText(/configure or ask oc8/i), {
      target: { value: "Draft in tab two" },
    });

    // Both tabs are still session-less, so both pills fall back to the same
    // "untitled" label -- index into DOM order (tab creation order) rather
    // than by name to tell them apart.
    const tabButtons = screen.getAllByRole("button", { name: /untitled chat tab/i });
    expect(tabButtons).toHaveLength(2);

    fireEvent.click(tabButtons[0]);
    expect(screen.getByPlaceholderText(/configure or ask oc8/i)).toHaveValue("Draft in tab one");

    fireEvent.click(tabButtons[1]);
    expect(screen.getByPlaceholderText(/configure or ask oc8/i)).toHaveValue("Draft in tab two");
  });

  it("selecting a session via ChatSessionPicker focuses its existing tab instead of duplicating it", () => {
    sessionsMock.mockReturnValue({
      data: [
        {
          id: "s1",
          agentId: "assistant-1",
          title: "Erste Frage",
          createdAt: "2026-01-01T00:00:00Z",
          lastMessageAt: null,
        },
        {
          id: "s2",
          agentId: "assistant-1",
          title: "Zweite Frage",
          createdAt: "2026-01-02T00:00:00Z",
          lastMessageAt: null,
        },
      ],
    });
    renderDock();
    openDock();
    // Bootstrap opens a first tab on the most recent session, s1.
    expect(screen.getByText("Erste Frage", { selector: "span.truncate" })).toBeInTheDocument();
    // Counted via the DOM structure (not getByRole) since the tab pills sit
    // outside the still-open dropdown's content and Radix marks the rest of
    // the page aria-hidden while it's open -- getByRole would incorrectly
    // see zero tabs whenever the picker's dropdown is open.
    expect(document.querySelectorAll("[data-copilot-tab]")).toHaveLength(1);

    // Pick s2 via the picker -- opens it into a second tab.
    fireEvent.pointerDown(screen.getByText("Erste Frage", { selector: "span.truncate" }), {
      button: 0,
    });
    fireEvent.click(screen.getByText("Zweite Frage"));
    expect(screen.getByText("Zweite Frage", { selector: "span.truncate" })).toBeInTheDocument();
    expect(document.querySelectorAll("[data-copilot-tab]")).toHaveLength(2);

    // Pick s1 again via the picker -- must focus the FIRST tab, not open a
    // third one.
    fireEvent.pointerDown(screen.getByText("Zweite Frage", { selector: "span.truncate" }), {
      button: 0,
    });
    fireEvent.click(screen.getByText("Erste Frage", { selector: "button" }));

    expect(screen.getByText("Erste Frage", { selector: "span.truncate" })).toBeInTheDocument();
    expect(document.querySelectorAll("[data-copilot-tab]")).toHaveLength(2);
  });

  it("closing a tab removes it and activates the previous tab", () => {
    renderDock();
    openDock();
    // Tab 1 (bootstrapped, blank). Open two more.
    fireEvent.click(screen.getByRole("button", { name: /^new tab$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^new tab$/i }));
    let tabButtons = screen.getAllByRole("button", { name: /untitled chat tab/i });
    expect(tabButtons).toHaveLength(3);

    // Tab 3 is active; close it via its own "close" button.
    const closeButtons = screen.getAllByRole("button", { name: /^close untitled chat$/i });
    fireEvent.click(closeButtons[closeButtons.length - 1]);

    tabButtons = screen.getAllByRole("button", { name: /untitled chat tab/i });
    expect(tabButtons).toHaveLength(2);
    // The previous tab (tab 2) is now the active one -- its composer is the
    // one rendered.
    expect(screen.getByPlaceholderText(/configure or ask oc8/i)).toBeInTheDocument();
  });

  it("persists open tabs across a remount, scoped to the current member", () => {
    sessionsMock.mockReturnValue({
      data: [
        {
          id: "s1",
          agentId: "assistant-1",
          title: "Erste Frage",
          createdAt: "t",
          lastMessageAt: null,
        },
      ],
    });
    const { unmount } = renderDock();
    openDock();
    fireEvent.click(screen.getByRole("button", { name: /^new tab$/i }));
    expect(screen.getAllByRole("button", { name: /frage tab|untitled chat tab/i })).toHaveLength(2);

    unmount();

    expect(localStorage.getItem("oc8-copilot-tabs-anon")).not.toBeNull();

    renderDock();
    openDock();
    expect(screen.getAllByRole("button", { name: /frage tab|untitled chat tab/i })).toHaveLength(2);
  });
});
