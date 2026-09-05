import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ToolGuardrailEditorDrawer } from "@/components/tool-guardrail-editor-drawer";

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useConnectionToolNames: () => ({ data: { names: ["merge_pr", "delete_branch"] } }),
  };
});

describe("ToolGuardrailEditorDrawer", () => {
  it("shows the department ceiling as read-only context on the agent view", () => {
    render(
      <ToolGuardrailEditorDrawer
        toolKey="github"
        connection={undefined}
        ceiling={{
          read: true,
          modify: true,
          approvalActions: ["merge_pr"],
          approvalEur: null,
          only: [],
        }}
        value={{ read: true, modify: false, approvalActions: [], approvalEur: null, only: [] }}
        onChange={vi.fn()}
        onClose={vi.fn()}
        onSave={vi.fn()}
        saving={false}
      />,
    );
    expect(screen.getByText(/department erlaubt|department allows/i)).toBeInTheDocument();
  });

  it("shows no department-ceiling section on the department view (ceiling=null)", () => {
    render(
      <ToolGuardrailEditorDrawer
        toolKey="github"
        connection={undefined}
        ceiling={null}
        value={{ read: true, modify: true, approvalActions: [], approvalEur: null, only: [] }}
        onChange={vi.fn()}
        onClose={vi.fn()}
        onSave={vi.fn()}
        saving={false}
      />,
    );
    expect(screen.queryByText(/department erlaubt|department allows/i)).toBeNull();
  });

  it("offers autocomplete suggestions from the connection's real tool names", () => {
    render(
      <ToolGuardrailEditorDrawer
        toolKey="github"
        connection={undefined}
        ceiling={null}
        value={{ read: true, modify: true, approvalActions: [], approvalEur: null, only: [] }}
        onChange={vi.fn()}
        onClose={vi.fn()}
        onSave={vi.fn()}
        saving={false}
      />,
    );
    const input = screen.getByRole("combobox", { name: /freigabe|approval/i });
    expect(input.getAttribute("list")).toBeTruthy();
    const listId = input.getAttribute("list") as string;
    expect(document.getElementById(listId)?.innerHTML).toContain("merge_pr");
  });
});
