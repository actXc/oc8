import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { AgentIdentityStep } from "@/components/onboarding/agent-identity-step";

const { getDepartments } = vi.hoisted(() => ({ getDepartments: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { get: getDepartments } }));

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("AgentIdentityStep", () => {
  it("calls onDone with the entered identity, pre-filled to the given department", async () => {
    getDepartments.mockResolvedValue([{ id: "dept-1", name: "Sales" }]);
    const onDone = vi.fn();
    renderWithClient(<AgentIdentityStep departmentId="dept-1" onDone={onDone} />);

    fireEvent.change(await screen.findByPlaceholderText(/Nova, Atlas, Miro/), {
      target: { value: "Astra" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Customer service/), {
      target: { value: "Support" },
    });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(onDone).toHaveBeenCalledWith({
      name: "Astra",
      role: "Support",
      description: "",
      departmentId: "dept-1",
      avatarIdx: 0,
    });
  });
});
