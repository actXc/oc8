import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const sessionsMock = vi.fn();
const createSessionMock = vi.fn();
const messagesMock = vi.fn();
const sendMessageMock = vi.fn();

vi.mock("@/lib/hooks-chat", () => ({
  useChatSessions: () => sessionsMock(),
  useCreateChatSession: () => ({ mutate: createSessionMock, isPending: false }),
  useChatMessages: () => messagesMock(),
  useSendChatMessage: () => ({ mutate: sendMessageMock, isPending: false }),
}));

import { ChatWindow } from "@/components/chat-window";

function renderChat() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ChatWindow agentId="agent-1" agentName="Nora" />
    </QueryClientProvider>,
  );
}

describe("ChatWindow", () => {
  beforeEach(() => {
    sessionsMock.mockReset();
    createSessionMock.mockReset();
    messagesMock.mockReset();
    sendMessageMock.mockReset();
  });

  it("offers to start a chat when the agent has no sessions yet", () => {
    sessionsMock.mockReturnValue({ data: [], isLoading: false });
    messagesMock.mockReturnValue({ data: undefined, isLoading: false });
    renderChat();
    expect(screen.getByRole("button", { name: /start chat/i })).toBeInTheDocument();
  });

  it("starting a chat creates a new session for this agent", () => {
    sessionsMock.mockReturnValue({ data: [], isLoading: false });
    messagesMock.mockReturnValue({ data: undefined, isLoading: false });
    renderChat();
    fireEvent.click(screen.getByRole("button", { name: /start chat/i }));
    expect(createSessionMock).toHaveBeenCalledWith("agent-1", expect.anything());
  });

  it("renders the transcript once a session and its messages exist", () => {
    sessionsMock.mockReturnValue({
      data: [{ id: "s1", agentId: "agent-1", title: "", createdAt: "2026-08-27T00:00:00Z" }],
      isLoading: false,
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
          createdAt: "2026-08-27T00:00:00Z",
        },
        {
          id: "m2",
          sessionId: "s1",
          role: "assistant",
          content: "Hi, wie kann ich helfen?",
          runId: "r1",
          renderedComponents: [],
          createdAt: "2026-08-27T00:00:01Z",
        },
      ],
      isLoading: false,
    });
    renderChat();
    expect(screen.getByText("Hallo")).toBeInTheDocument();
    expect(screen.getByText("Hi, wie kann ich helfen?")).toBeInTheDocument();
  });

  it("shows a thinking indicator and disables sending while the last turn is the user's own", () => {
    sessionsMock.mockReturnValue({
      data: [{ id: "s1", agentId: "agent-1", title: "", createdAt: "2026-08-27T00:00:00Z" }],
      isLoading: false,
    });
    messagesMock.mockReturnValue({
      data: [
        {
          id: "m1",
          sessionId: "s1",
          role: "user",
          content: "Wie viele Stunden diese Woche?",
          runId: null,
          renderedComponents: [],
          createdAt: "2026-08-27T00:00:00Z",
        },
      ],
      isLoading: false,
    });
    renderChat();
    expect(screen.getByText(/is thinking/i)).toBeInTheDocument();
  });

  it("renders a rendered component attached to an assistant reply", () => {
    sessionsMock.mockReturnValue({
      data: [{ id: "s1", agentId: "agent-1", title: "", createdAt: "2026-08-27T00:00:00Z" }],
      isLoading: false,
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
          createdAt: "2026-08-27T00:00:01Z",
        },
      ],
      isLoading: false,
    });
    renderChat();
    expect(screen.getByText("Wochenbericht")).toBeInTheDocument();
  });
});
