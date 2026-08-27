import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// Task 11: SkillDrawer used to be a hand-rolled `fixed inset-0 ... flex
// justify-end` div; it now goes through the shared <DetailSheet> (Task 10),
// which renders Radix Sheet content with role="dialog". The amendment adds
// inline-editable name/description/category fields + a Save button that
// calls useUpdateSkill().

const useUpdateSkillMock = vi.fn();

vi.mock("@/lib/hooks", () => ({
  useAgents: () => ({ data: [] }),
  useAssignSkill: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateSkill: () => useUpdateSkillMock(),
}));

import type { Skill } from "@/lib/skills";
import { SkillDrawer } from "@/routes/skills";

const skill: Skill = {
  id: "sk-1",
  name: "Invoice Review",
  description: "Validates invoices against POs.",
  category: "finance",
  origin: "local",
  version: "1.0.0",
  author: "oc8 core",
  tools: ["Accounting"],
  guardrails: ["Amounts above €10 000 require human approval"],
  instructions: "Extract line items and book them.",
  currentVersionId: "v-1",
  usedByAgents: 2,
  updatedAt: "2026-08-01",
};

function renderDrawer(onClose: () => void = vi.fn()) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <SkillDrawer skill={skill} categoryLabel="Finance" onClose={onClose} />
    </QueryClientProvider>,
  );
}

describe("SkillDrawer", () => {
  it("renders inside a DetailSheet (right-side sheet chrome)", () => {
    useUpdateSkillMock.mockReturnValue({ mutate: vi.fn(), isPending: false });

    renderDrawer();

    // A Radix Sheet's content renders with role="dialog" via SheetContent;
    // asserting that role is what proves this now goes through DetailSheet
    // rather than the old hand-rolled `fixed inset-0 ... flex justify-end` div.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("edits the name field and calls useUpdateSkill() when Save is clicked", () => {
    const mutate = vi.fn();
    useUpdateSkillMock.mockReturnValue({ mutate, isPending: false });

    renderDrawer();

    const nameInput = screen.getByLabelText("Name");
    fireEvent.change(nameInput, { target: { value: "Invoice Review v2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "sk-1",
        name: "Invoice Review v2",
        description: skill.description,
        category: skill.category,
      }),
      expect.anything(),
    );
  });
});
