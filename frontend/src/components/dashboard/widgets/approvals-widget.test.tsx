// frontend/src/components/dashboard/widgets/approvals-widget.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { ApprovalsWidget } from "./approvals-widget";
import * as hooks from "@/lib/hooks";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("ApprovalsWidget", () => {
  it("shows the empty state when nothing is waiting", () => {
    vi.spyOn(hooks, "useApprovals").mockReturnValue({ data: [], isPending: false } as never);
    vi.spyOn(hooks, "useClarifications").mockReturnValue({ data: [], isPending: false } as never);
    vi.spyOn(hooks, "useStanding").mockReturnValue({
      seats: [],
      decidesEverywhere: true,
      unrestricted: false,
      unassigned: false,
      pending: false,
    } as never);
    vi.spyOn(hooks, "useDecideApproval").mockReturnValue({ isPending: false } as never);
    vi.spyOn(hooks, "useAnswerClarification").mockReturnValue({ isPending: false } as never);

    render(<ApprovalsWidget config={{}} onConfigChange={vi.fn()} />, { wrapper });
    expect(screen.getByText(/nothing is waiting for you/i)).toBeInTheDocument();
  });

  it("opens the detail dialog for a row on click", async () => {
    const approval = {
      id: "a1",
      title: "Refund a customer",
      agentId: "agent-1",
      agentName: "Agent One",
      departmentId: "dept-1",
      departmentName: "Sales",
      amount: "€50",
      createdAt: new Date().toISOString(),
      toolArguments: {},
      options: [],
      recommendation: null,
      actionType: "tool_send",
      detail: null,
      taskTitle: null,
      toolName: null,
    };
    vi.spyOn(hooks, "useApprovals").mockReturnValue({
      data: [approval],
      isPending: false,
    } as never);
    vi.spyOn(hooks, "useClarifications").mockReturnValue({ data: [], isPending: false } as never);
    vi.spyOn(hooks, "useStanding").mockReturnValue({
      seats: [{ departmentId: "dept-1", seatRole: "dept_approver" }],
      decidesEverywhere: false,
      unrestricted: false,
      unassigned: false,
      pending: false,
    } as never);
    vi.spyOn(hooks, "useDecideApproval").mockReturnValue({ isPending: false } as never);
    vi.spyOn(hooks, "useAnswerClarification").mockReturnValue({ isPending: false } as never);

    render(<ApprovalsWidget config={{}} onConfigChange={vi.fn()} />, { wrapper });
    fireEvent.click(screen.getByText("Refund a customer"));
    await waitFor(() => expect(screen.getByText("Approve")).toBeInTheDocument());
  });
});
