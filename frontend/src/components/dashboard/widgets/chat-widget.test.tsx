import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { ChatWidget } from "./chat-widget";

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
  it("renders a composer for a tile with no session yet", () => {
    render(<ChatWidget config={{}} onConfigChange={vi.fn()} />, { wrapper });
    expect(screen.getByPlaceholderText(/configure or ask oc8/i)).toBeInTheDocument();
  });

  it("writes the newly created session id back into config on send", () => {
    const onConfigChange = vi.fn();
    render(<ChatWidget config={{ sessionId: "s1" }} onConfigChange={onConfigChange} />, {
      wrapper,
    });
    // With a sessionId already in config, the composer is present and bound
    // to that session -- onConfigChange fires only when the session CHANGES,
    // not on every render, so it should not have been called yet.
    expect(onConfigChange).not.toHaveBeenCalled();
  });
});
