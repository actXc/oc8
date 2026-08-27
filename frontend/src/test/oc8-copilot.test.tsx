import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CopilotProposalCard } from "@/components/oc8-copilot";

describe("CopilotProposalCard", () => {
  it("does not apply a proposal before the person explicitly clicks Apply", () => {
    const apply = vi.fn();
    render(
      <CopilotProposalCard
        proposal={{
          id: "proposal-1",
          status: "draft",
          revision: 1,
          operations: [{ label: "agent.mission.set", references: { agentId: "agent-1" } }],
        }}
        onApply={apply}
        onReject={vi.fn()}
      />,
    );

    expect(apply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Apply proposal" }));
    expect(apply).toHaveBeenCalledTimes(1);
  });

  it("requires an additional confirmation for a critical proposal", () => {
    const apply = vi.fn();
    render(
      <CopilotProposalCard
        proposal={{
          id: "proposal-2",
          status: "draft",
          revision: 1,
          operations: [
            { label: "integration.prepare", references: { integrationId: "integration-1" } },
          ],
        }}
        onApply={apply}
        onReject={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Review before applying" }));
    expect(apply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Apply critical proposal" }));
    expect(apply).toHaveBeenCalledTimes(1);
  });
});
