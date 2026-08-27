import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentIdentityFields, type AgentIdentity } from "@/components/agent-identity-fields";

const departments = [{ id: "dept-1", name: "Sales" }];

function baseValue(): AgentIdentity {
  return { name: "", role: "", description: "", departmentId: "dept-1", avatarIdx: 0 };
}

describe("AgentIdentityFields", () => {
  it("reports the typed name back through onChange", () => {
    const onChange = vi.fn();
    render(
      <AgentIdentityFields value={baseValue()} onChange={onChange} departments={departments} />,
    );
    fireEvent.change(screen.getByPlaceholderText(/Nova, Atlas, Miro/), {
      target: { value: "Astra" },
    });
    expect(onChange).toHaveBeenCalledWith({ ...baseValue(), name: "Astra" });
  });

  it("lists every given department as a selectable option", () => {
    render(
      <AgentIdentityFields
        value={baseValue()}
        onChange={vi.fn()}
        departments={[{ id: "dept-1", name: "Sales" }, { id: "dept-2", name: "Support" }]}
      />,
    );
    expect(screen.getByRole("option", { name: "Sales" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Support" })).toBeInTheDocument();
  });
});
