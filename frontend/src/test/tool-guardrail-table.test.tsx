import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ToolGuardrailTable } from "@/components/tool-guardrail-table";

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useConnectionToolNames: () => ({ data: { names: [] } }),
    useMcpLogins: () => ({ data: [] }),
  };
});

const BLANK = { read: false, modify: false, approvalActions: [], approvalEur: null, only: [] };

describe("ToolGuardrailTable", () => {
  it("renders the Wie Department badge for an inherited row", () => {
    render(
      <ToolGuardrailTable
        level="agent"
        rows={[
          {
            toolKey: "github",
            connection: undefined,
            ceilingPolicy: BLANK,
            ownValue: BLANK,
            status: "inherited",
          },
        ]}
        addableNames={[]}
        connections={[]}
        onSave={vi.fn()}
        onAdd={vi.fn()}
        saving={false}
      />,
    );
    expect(screen.getByText(/wie department|same as department/i)).toBeInTheDocument();
  });

  it("renders the Eingeschränkt badge for a narrowed row", () => {
    render(
      <ToolGuardrailTable
        level="agent"
        rows={[
          {
            toolKey: "github",
            connection: undefined,
            ceilingPolicy: BLANK,
            ownValue: BLANK,
            status: "narrowed",
          },
        ]}
        addableNames={[]}
        connections={[]}
        onSave={vi.fn()}
        onAdd={vi.fn()}
        saving={false}
      />,
    );
    expect(screen.getByText(/eingeschränkt|narrowed/i)).toBeInTheDocument();
  });

  it("renders the Nur dieser Agent badge for an agent-exclusive row", () => {
    render(
      <ToolGuardrailTable
        level="agent"
        rows={[
          {
            toolKey: "internal-wiki",
            connection: undefined,
            ceilingPolicy: null,
            ownValue: BLANK,
            status: "agent-only",
          },
        ]}
        addableNames={[]}
        connections={[]}
        onSave={vi.fn()}
        onAdd={vi.fn()}
        saving={false}
      />,
    );
    expect(screen.getByText(/nur dieser agent|this agent only/i)).toBeInTheDocument();
  });

  it("renders the deviation count on the department view", () => {
    render(
      <ToolGuardrailTable
        level="department"
        rows={[
          {
            toolKey: "github",
            connection: undefined,
            ceilingPolicy: null,
            ownValue: BLANK,
            status: null,
            deviationCount: { count: 3, total: 12 },
          },
        ]}
        addableNames={[]}
        connections={[]}
        onSave={vi.fn()}
        onAdd={vi.fn()}
        saving={false}
      />,
    );
    expect(screen.getByText(/3.*12|3 von 12|3 of 12/i)).toBeInTheDocument();
  });

  it("opens the drawer and calls onSave with the edited value", () => {
    const onSave = vi.fn();
    render(
      <ToolGuardrailTable
        level="agent"
        rows={[
          {
            toolKey: "github",
            connection: undefined,
            ceilingPolicy: BLANK,
            ownValue: BLANK,
            status: "inherited",
          },
        ]}
        addableNames={[]}
        connections={[]}
        onSave={onSave}
        onAdd={vi.fn()}
        saving={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /bearbeiten|edit/i }));
    fireEvent.click(screen.getByRole("button", { name: /speichern|save/i }));
    expect(onSave).toHaveBeenCalledWith("github", expect.any(Object));
  });
});
