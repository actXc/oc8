import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const sessionsMock = vi.fn();
const createSessionMock = vi.fn();
const messagesMock = vi.fn();
const sendMessageMock = vi.fn();
const renameSessionMock = vi.fn();
const deleteSessionMock = vi.fn();
const uploadAttachmentMock = vi.fn();

vi.mock("@/lib/hooks-chat", () => ({
  useChatSessions: () => sessionsMock(),
  useCreateChatSession: () => ({ mutate: createSessionMock, isPending: false }),
  useChatMessages: () => messagesMock(),
  useSendChatMessage: () => ({ mutate: sendMessageMock, isPending: false }),
  useRenameChatSession: () => ({ mutate: renameSessionMock }),
  useDeleteChatSession: () => ({ mutate: deleteSessionMock }),
  useUploadChatAttachment: () => ({ mutate: uploadAttachmentMock, isPending: false }),
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
    renameSessionMock.mockReset();
    deleteSessionMock.mockReset();
    uploadAttachmentMock.mockReset();
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

describe("ChatWindow session picker", () => {
  beforeEach(() => {
    sessionsMock.mockReset();
    createSessionMock.mockReset();
    messagesMock.mockReset();
    sendMessageMock.mockReset();
    renameSessionMock.mockReset();
    deleteSessionMock.mockReset();
    sessionsMock.mockReturnValue({
      data: [
        { id: "s1", agentId: "agent-1", title: "VPN Tickets", createdAt: "2026-08-27T00:00:00Z" },
        { id: "s2", agentId: "agent-1", title: "", createdAt: "2026-08-26T00:00:00Z" },
      ],
      isLoading: false,
    });
    messagesMock.mockReturnValue({ data: [], isLoading: false });
  });

  // Radix's DropdownMenuTrigger opens on pointerdown, not click -- jsdom's
  // fireEvent.click doesn't synthesize a pointerdown the way a real browser
  // click does, so a plain click silently never opens the menu.
  function openPicker() {
    fireEvent.pointerDown(screen.getByRole("button", { name: "VPN Tickets" }), { button: 0 });
  }

  it("shows the current session's title on the picker trigger, defaulting to the newest session", () => {
    renderChat();
    expect(screen.getByRole("button", { name: "VPN Tickets" })).toBeInTheDocument();
  });

  it("lists every session in the dropdown, falling back to a placeholder for an unnamed one", () => {
    renderChat();
    openPicker();
    expect(screen.getAllByText("VPN Tickets").length).toBeGreaterThan(0);
    expect(screen.getByText("Untitled chat")).toBeInTheDocument();
  });

  it("renaming a session commits the new title on Enter", async () => {
    renderChat();
    openPicker();
    fireEvent.click(screen.getAllByRole("button", { name: /rename/i })[0]);

    const input = screen.getByDisplayValue("VPN Tickets");
    fireEvent.change(input, { target: { value: "Zugriff Laufwerk" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(renameSessionMock).toHaveBeenCalledWith({
        sessionId: "s1",
        title: "Zugriff Laufwerk",
      }),
    );
  });

  it("blank input on blur does not persist a rename", () => {
    renderChat();
    openPicker();
    fireEvent.click(screen.getAllByRole("button", { name: /rename/i })[0]);

    const input = screen.getByDisplayValue("VPN Tickets");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.blur(input);

    expect(renameSessionMock).not.toHaveBeenCalled();
  });

  it("deleting a session asks for confirmation before persisting", async () => {
    renderChat();
    openPicker();
    fireEvent.click(screen.getAllByRole("button", { name: /delete/i })[0]);

    expect(deleteSessionMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => expect(deleteSessionMock).toHaveBeenCalledWith("s1", expect.anything()));
  });

  it("cancelling the delete confirmation leaves the session untouched", () => {
    renderChat();
    openPicker();
    fireEvent.click(screen.getAllByRole("button", { name: /delete/i })[0]);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(deleteSessionMock).not.toHaveBeenCalled();
  });
});

describe("ChatWindow attachments", () => {
  beforeEach(() => {
    sessionsMock.mockReset();
    createSessionMock.mockReset();
    messagesMock.mockReset();
    sendMessageMock.mockReset();
    renameSessionMock.mockReset();
    deleteSessionMock.mockReset();
    uploadAttachmentMock.mockReset();
    sessionsMock.mockReturnValue({
      data: [{ id: "s1", agentId: "agent-1", title: "", createdAt: "2026-08-27T00:00:00Z" }],
      isLoading: false,
    });
  });

  const attachmentDto = {
    id: "att1",
    filename: "notes.txt",
    contentType: "text/plain",
    sizeBytes: 12,
    isImage: false,
    createdAt: "2026-08-27T00:00:00Z",
  };

  it("uploading a file adds a pending attachment chip", () => {
    messagesMock.mockReturnValue({ data: [], isLoading: false });
    uploadAttachmentMock.mockImplementation(
      (_file: File, opts?: { onSuccess?: (dto: unknown) => void }) => {
        opts?.onSuccess?.(attachmentDto);
      },
    );
    renderChat();

    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText(/attach a file/i), { target: { files: [file] } });

    expect(uploadAttachmentMock).toHaveBeenCalledWith(file, expect.anything());
    expect(screen.getByText("notes.txt")).toBeInTheDocument();
  });

  it("refuses a file over the 25 MB cap without calling the upload", () => {
    // Client half of the cap: fast feedback only -- the server's own check is
    // authoritative. Without this the browser uploaded the whole thing just to
    // be told 413.
    messagesMock.mockReturnValue({ data: [], isLoading: false });
    renderChat();

    const huge = new File(["x"], "huge.pdf", { type: "application/pdf" });
    Object.defineProperty(huge, "size", { value: 26 * 1024 * 1024 });
    fireEvent.change(screen.getByLabelText(/attach a file/i), { target: { files: [huge] } });

    expect(uploadAttachmentMock).not.toHaveBeenCalled();
    expect(screen.queryByText("huge.pdf")).not.toBeInTheDocument();
  });

  it("removing a pending chip before send removes it", () => {
    messagesMock.mockReturnValue({ data: [], isLoading: false });
    uploadAttachmentMock.mockImplementation(
      (_file: File, opts?: { onSuccess?: (dto: unknown) => void }) => {
        opts?.onSuccess?.(attachmentDto);
      },
    );
    renderChat();

    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText(/attach a file/i), { target: { files: [file] } });
    expect(screen.getByText("notes.txt")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /remove/i }));
    expect(screen.queryByText("notes.txt")).not.toBeInTheDocument();
  });

  it("sending carries the pending attachment ids and clears them on success", () => {
    messagesMock.mockReturnValue({ data: [], isLoading: false });
    uploadAttachmentMock.mockImplementation(
      (_file: File, opts?: { onSuccess?: (dto: unknown) => void }) => {
        opts?.onSuccess?.(attachmentDto);
      },
    );
    renderChat();

    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText(/attach a file/i), { target: { files: [file] } });

    const textarea = screen.getByPlaceholderText(/type a message/i);
    fireEvent.change(textarea, { target: { value: "see attached" } });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(sendMessageMock).toHaveBeenCalledWith(
      { message: "see attached", attachmentIds: ["att1"] },
      expect.anything(),
    );
  });

  it("a sent message with an attachment renders its chip", () => {
    messagesMock.mockReturnValue({
      data: [
        {
          id: "m1",
          sessionId: "s1",
          role: "user",
          content: "see attached",
          runId: null,
          renderedComponents: [],
          createdAt: "2026-08-27T00:00:00Z",
          attachments: [attachmentDto],
        },
      ],
      isLoading: false,
    });
    renderChat();

    expect(screen.getByText("see attached")).toBeInTheDocument();
    expect(screen.getByText("notes.txt")).toBeInTheDocument();
  });
});
