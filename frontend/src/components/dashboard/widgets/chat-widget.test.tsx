import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatWidget } from "./chat-widget";
import * as governanceHooks from "@/lib/governance-hooks";
import * as hooks from "@/lib/hooks";
import * as hooksChat from "@/lib/hooks-chat";
import * as liveProvider from "@/lib/live/provider";

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return { ...actual, useAssistant: () => ({ data: { agentId: "agent-1" } }) };
});
vi.mock("@/lib/hooks-chat", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks-chat")>();
  return {
    ...actual,
    useChatSessions: () => ({ data: [] }),
    useChatMessages: () => ({ data: [] }),
    useSendChatMessage: () => ({ mutate: vi.fn(), isPending: false }),
    useCreateChatSession: () => ({ mutate: vi.fn(), isPending: false }),
  };
});

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("ChatWidget", () => {
  // `vi.spyOn(...).mockReturnValue(...)` otherwise leaks into later tests in
  // this file -- there is no global mock-reset config (vitest.setup.ts).
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a composer for a tile with no session yet", () => {
    render(<ChatWidget config={{}} onConfigChange={vi.fn()} />, { wrapper });
    expect(screen.getByPlaceholderText(/configure or ask oc8/i)).toBeInTheDocument();
  });

  it("does not call onConfigChange merely from mounting with an existing session id", () => {
    const onConfigChange = vi.fn();
    render(<ChatWidget config={{ sessionId: "s1" }} onConfigChange={onConfigChange} />, {
      wrapper,
    });
    // With a sessionId already in config, the composer is present and bound
    // to that session -- onConfigChange fires only when the session CHANGES,
    // not on every render, so it should not have been called yet.
    expect(onConfigChange).not.toHaveBeenCalled();
  });

  it("writes the newly created session id back into config on send", () => {
    // No sessionId in config yet -- sending a message has to create a
    // session first, same as CopilotChatTab's own `send()`.
    vi.spyOn(hooksChat, "useCreateChatSession").mockReturnValue({
      mutate: (_agentId: string, options?: { onSuccess?: (session: { id: string }) => void }) =>
        options?.onSuccess?.({ id: "new-session-1" }),
      isPending: false,
    } as never);

    const onConfigChange = vi.fn();
    render(<ChatWidget config={{}} onConfigChange={onConfigChange} />, { wrapper });

    fireEvent.change(screen.getByPlaceholderText(/configure or ask oc8/i), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(onConfigChange).toHaveBeenCalledWith({ sessionId: "new-session-1" });
  });

  it("renders a fallback instead of the composer for a member without copilot:use", () => {
    vi.spyOn(governanceHooks, "useCan").mockReturnValue(() => false);

    render(<ChatWidget config={{}} onConfigChange={vi.fn()} />, { wrapper });

    expect(screen.queryByPlaceholderText(/configure or ask oc8/i)).not.toBeInTheDocument();
    expect(screen.getByText(/don't have access to copilot chat/i)).toBeInTheDocument();
  });

  it("shows a reconnecting notice when the live connection drops, matching CopilotDock", () => {
    vi.spyOn(liveProvider, "useLiveConnectionStatus").mockReturnValue("disconnected");

    render(<ChatWidget config={{}} onConfigChange={vi.fn()} />, { wrapper });

    expect(screen.getByText(/new messages may be delayed/i)).toBeInTheDocument();
  });

  it("surfaces a pending copilot proposal with Apply/Reject, matching CopilotDock", () => {
    vi.spyOn(hooks, "useCopilotProposals").mockReturnValue({
      data: [
        {
          id: "p1",
          status: "draft",
          operations: [{ label: "create_department", references: {} }],
        },
      ],
    } as never);
    vi.spyOn(hooks, "useApplyCopilotProposal").mockReturnValue({
      isPending: false,
      mutate: vi.fn(),
    } as never);
    vi.spyOn(hooks, "useRejectCopilotProposal").mockReturnValue({
      isPending: false,
      mutate: vi.fn(),
    } as never);

    render(<ChatWidget config={{}} onConfigChange={vi.fn()} />, { wrapper });

    expect(screen.getByText(/pending proposals/i)).toBeInTheDocument();
    expect(screen.getByText("create_department")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
  });
});
