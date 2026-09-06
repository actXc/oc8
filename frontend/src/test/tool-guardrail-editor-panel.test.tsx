import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ToolGuardrailEditorPanel } from "@/components/tool-guardrail-editor-panel";

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useConnectionToolNames: () => ({ data: { names: ["merge_pr", "delete_branch"] } }),
  };
});

describe("ToolGuardrailEditorPanel", () => {
  it("shows the department ceiling as read-only context on the agent view", () => {
    render(
      <ToolGuardrailEditorPanel
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
        onCancel={vi.fn()}
        onSave={vi.fn()}
        saving={false}
      />,
    );
    expect(screen.getByText(/department erlaubt|department allows/i)).toBeInTheDocument();
  });

  it("shows no department-ceiling section on the department view (ceiling=null)", () => {
    render(
      <ToolGuardrailEditorPanel
        toolKey="github"
        connection={undefined}
        ceiling={null}
        value={{ read: true, modify: true, approvalActions: [], approvalEur: null, only: [] }}
        onChange={vi.fn()}
        onCancel={vi.fn()}
        onSave={vi.fn()}
        saving={false}
      />,
    );
    expect(screen.queryByText(/department erlaubt|department allows/i)).toBeNull();
  });

  it("offers autocomplete suggestions from the connection's real tool names", () => {
    render(
      <ToolGuardrailEditorPanel
        toolKey="github"
        connection={undefined}
        ceiling={null}
        value={{ read: true, modify: true, approvalActions: [], approvalEur: null, only: [] }}
        onChange={vi.fn()}
        onCancel={vi.fn()}
        onSave={vi.fn()}
        saving={false}
      />,
    );
    const input = screen.getByRole("combobox", { name: /freigabe|approval/i });
    expect(input.getAttribute("list")).toBeTruthy();
    const listId = input.getAttribute("list") as string;
    expect(document.getElementById(listId)?.innerHTML).toContain("merge_pr");
  });

  it("has a Cancel action that isn't the Save button", () => {
    const onCancel = vi.fn();
    render(
      <ToolGuardrailEditorPanel
        toolKey="github"
        connection={undefined}
        ceiling={null}
        value={{ read: true, modify: true, approvalActions: [], approvalEur: null, only: [] }}
        onChange={vi.fn()}
        onCancel={onCancel}
        onSave={vi.fn()}
        saving={false}
      />,
    );
    screen.getByRole("button", { name: /abbrechen|cancel/i }).click();
    expect(onCancel).toHaveBeenCalled();
  });
});
