import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentDetail } from "@/lib/hooks-agent-detail";

// Bug: the backend has had DELETE /agents/{id} (archive-or-hard-delete) and
// POST /agents/{id}/restore since before this test existed, but the frontend
// only ever wired up restore -- there was no way to delete or archive an
// agent from the UI at all, only to bring an already-archived one back.

const deleteMutate = vi.fn();
const navigate = vi.fn();

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useDeleteAgent: () => ({ mutate: deleteMutate, isPending: false }),
  };
});

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return { ...actual, useNavigate: () => navigate };
});

import { DeleteAgentButton } from "@/routes/agents.$id";

const AGENT: AgentDetail = {
  id: "agent-1",
  name: "Nora",
  role: "Sales",
  llm: "claude-sonnet-5",
  provider: "anthropic",
  status: "running",
  tools: [],
  lastAction: "Sent a follow-up email",
  lastRun: "2 hours ago",
  tasksToday: 3,
  guardrails: [],
  schedule: "",
  avatarColor: "#000",
  departmentId: "dept-1",
  modelConfigId: null,
  isLead: false,
  mission: "",
  departmentName: "Vertrieb",
  effectiveTools: {},
  departmentFrameTools: {},
  runtimeRef: null,
  currentRunId: null,
};

function renderButton(mayManage = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DeleteAgentButton agent={AGENT} mayManage={mayManage} />
    </QueryClientProvider>,
  );
}

describe("DeleteAgentButton", () => {
  beforeEach(() => {
    deleteMutate.mockReset();
    navigate.mockReset();
  });

  it("renders nothing when the caller may not manage the agent", () => {
    renderButton(false);
    expect(screen.queryByTitle("Delete agent")).not.toBeInTheDocument();
  });

  it("asks for confirmation before deleting", () => {
    renderButton();

    fireEvent.click(screen.getByTitle("Delete agent"));

    expect(screen.getByText(/Delete this agent\?/i)).toBeInTheDocument();
    expect(deleteMutate).not.toHaveBeenCalled();
  });

  it("deletes the agent and navigates back to the list on confirm", async () => {
    renderButton();

    fireEvent.click(screen.getByTitle("Delete agent"));
    fireEvent.click(screen.getByRole("button", { name: /^Delete$/i }));

    await waitFor(() => expect(deleteMutate).toHaveBeenCalledWith("agent-1", expect.anything()));

    const onSuccess = deleteMutate.mock.calls[0][1].onSuccess;
    onSuccess({ outcome: "deleted" });
    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: "/agents" }));
  });

  it("cancelling the confirmation never calls delete", () => {
    renderButton();

    fireEvent.click(screen.getByTitle("Delete agent"));
    fireEvent.click(screen.getByRole("button", { name: /Cancel/i }));

    expect(deleteMutate).not.toHaveBeenCalled();
  });
});
