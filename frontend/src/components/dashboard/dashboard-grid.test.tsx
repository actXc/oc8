import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DashboardGrid } from "./dashboard-grid";
import type { WidgetInstance } from "@/lib/hooks";

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return { ...actual, useBudgetStatus: () => ({ data: undefined, isPending: true }) };
});

const widgets: WidgetInstance[] = [
  { id: "w1", type: "budget", x: 0, y: 0, w: 3, h: 3, config: {} },
  { id: "w2", type: "activity", x: 3, y: 0, w: 3, h: 4, config: {} },
];

describe("DashboardGrid", () => {
  it("renders one WidgetFrame per instance", () => {
    render(<DashboardGrid widgets={widgets} onChange={vi.fn()} />);
    expect(screen.getByText("Budget")).toBeInTheDocument();
    expect(screen.getByText("Activity")).toBeInTheDocument();
  });

  it("removing a widget calls onChange with it filtered out", () => {
    const onChange = vi.fn();
    render(<DashboardGrid widgets={widgets} onChange={onChange} />);
    const removeButtons = screen.getAllByRole("button", { name: /remove widget/i });
    fireEvent.click(removeButtons[0]);
    expect(onChange).toHaveBeenCalledWith([widgets[1]]);
  });
});
