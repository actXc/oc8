import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AddToolPicker } from "@/components/add-tool-picker";
import type { McpConnection } from "@/lib/hooks";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function connection(overrides: Partial<McpConnection> = {}): McpConnection {
  return {
    id: "c1",
    name: "github",
    transport: "stdio",
    serverUrl: "",
    command: "",
    args: [],
    departmentId: null,
    connected: true,
    scopes: [],
    health: {},
    guardrailPresets: [],
    guardrailLibrary: null,
    hasValueSpec: false,
    pluginName: null,
    credentialType: null,
    ...overrides,
  };
}

describe("AddToolPicker", () => {
  it("shows a checked recommendation checkbox for a connection with a preset", () => {
    const onAdd = vi.fn();
    render(
      <AddToolPicker
        addableNames={["github"]}
        connections={[
          connection({
            name: "github",
            guardrailPresets: [
              {
                key: "dev_standard",
                label: "Developer standard",
                labelTranslations: {},
                summary: "",
                summaryTranslations: {},
                recommended: true,
                read: true,
                modify: true,
                approvalActions: [],
                approvalEur: null,
                only: [],
              },
            ],
          }),
        ]}
        onAdd={onAdd}
        onClose={vi.fn()}
      />,
    );
    const checkbox = screen.getByRole("checkbox", { name: /mit empfehlung|with recommendation/i });
    expect(checkbox).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: /hinzufügen|add/i }));
    expect(onAdd).toHaveBeenCalledWith(
      "github",
      expect.objectContaining({ read: true, modify: true }),
    );
  });

  it("shows no checkbox for a connection with no preset, and adds a blank policy", () => {
    const onAdd = vi.fn();
    render(
      <AddToolPicker
        addableNames={["internal-wiki"]}
        connections={[connection({ name: "internal-wiki", guardrailPresets: [] })]}
        onAdd={onAdd}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByRole("checkbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /hinzufügen|add/i }));
    expect(onAdd).toHaveBeenCalledWith("internal-wiki", null);
  });

  it("unchecking the recommendation checkbox adds a blank policy instead", () => {
    const onAdd = vi.fn();
    render(
      <AddToolPicker
        addableNames={["github"]}
        connections={[
          connection({
            name: "github",
            guardrailPresets: [
              {
                key: "dev_standard",
                label: "Developer standard",
                labelTranslations: {},
                summary: "",
                summaryTranslations: {},
                recommended: true,
                read: true,
                modify: true,
                approvalActions: [],
                approvalEur: null,
                only: [],
              },
            ],
          }),
        ]}
        onAdd={onAdd}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: /mit empfehlung|with recommendation/i }));
    fireEvent.click(screen.getByRole("button", { name: /hinzufügen|add/i }));
    expect(onAdd).toHaveBeenCalledWith("github", null);
  });
});
