import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Skill } from "@/lib/skills";

// Bug: AgentSkillsTab used to keep assigned skills in a local, session-only
// useState that started empty every render, so a reload showed no skills even
// though the assignment was persisted server-side. remove() only updated that
// local state too, with no API call at all -- "removing" a skill in the UI
// left the assignment active in Postgres. Both are covered here by asserting
// the tab reads from useAgentSkills() and remove() calls useUnassignSkill().

const useSkillsMock = vi.fn();
const useAgentSkillsMock = vi.fn();
const assignMutate = vi.fn();
const unassignMutate = vi.fn();

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useSkills: (...args: unknown[]) => useSkillsMock(...args),
    useAgentSkills: (...args: unknown[]) => useAgentSkillsMock(...args),
    useAssignSkill: () => ({ mutate: assignMutate, isPending: false }),
    useUnassignSkill: () => ({ mutate: unassignMutate, isPending: false }),
  };
});

import { AgentSkillsTab } from "@/routes/agents.$id";

const SKILL_A: Skill = {
  id: "sk-1",
  name: "Invoice Review",
  description: "Validates invoices against POs.",
  category: "finance",
  origin: "local",
  version: "1.0.0",
  author: "oc8 core",
  tools: [],
  guardrails: [],
  instructions: "",
  currentVersionId: "v-1",
  usedByAgents: 1,
  updatedAt: "2026-08-01",
};

const SKILL_B: Skill = {
  ...SKILL_A,
  id: "sk-2",
  name: "Contract Drafting",
  currentVersionId: "v-2",
};

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AgentSkillsTab agentId="agent-1" agentName="Nora" mayManage={true} />
    </QueryClientProvider>,
  );
}

describe("AgentSkillsTab", () => {
  beforeEach(() => {
    useSkillsMock.mockReset();
    useAgentSkillsMock.mockReset();
    assignMutate.mockReset();
    unassignMutate.mockReset();
    useSkillsMock.mockReturnValue({ data: { items: [SKILL_A, SKILL_B] } });
  });

  it("shows a skill assigned server-side even before any in-session assign click (survives reload)", () => {
    useAgentSkillsMock.mockReturnValue({
      data: [
        {
          id: "assignment-1",
          agentId: "agent-1",
          skillId: "sk-1",
          skillName: SKILL_A.name,
          skillVersionId: "v-1",
          semver: "1.0.0",
          enabled: true,
        },
      ],
    });

    renderTab();

    expect(screen.getByText("Invoice Review")).toBeInTheDocument();
    expect(screen.queryByText("Contract Drafting")).not.toBeInTheDocument();
  });

  it("calls useUnassignSkill() with the real assignment id when a skill is removed", () => {
    useAgentSkillsMock.mockReturnValue({
      data: [
        {
          id: "assignment-1",
          agentId: "agent-1",
          skillId: "sk-1",
          skillName: SKILL_A.name,
          skillVersionId: "v-1",
          semver: "1.0.0",
          enabled: true,
        },
      ],
    });

    renderTab();

    screen.getByLabelText("Remove").click();

    expect(unassignMutate).toHaveBeenCalledTimes(1);
    expect(unassignMutate).toHaveBeenCalledWith(
      { agentId: "agent-1", assignmentId: "assignment-1" },
      expect.anything(),
    );
  });
});
