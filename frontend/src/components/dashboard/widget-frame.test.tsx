import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WidgetFrame } from "./widget-frame";
import type { WidgetInstance } from "@/lib/hooks";

function makeInstance(overrides: Partial<WidgetInstance> = {}): WidgetInstance {
  return { id: "w1", type: "budget", x: 0, y: 0, w: 3, h: 3, config: {}, ...overrides };
}

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return { ...actual, useBudgetStatus: () => ({ data: undefined, isPending: true }) };
});

// Makes the "activity" slot in the registry throw during render, so the
// "catches a rendering exception" test below exercises the real error
// boundary against a real registry lookup -- no other test in this file
// renders an "activity" widget, so this is scoped to that one test only.
vi.mock("@/components/dashboard/widgets/activity-widget", () => ({
  ActivityWidget: () => {
    throw new Error("boom");
  },
}));

describe("WidgetFrame", () => {
  it("renders the registered widget's title and a remove button", () => {
    render(<WidgetFrame instance={makeInstance()} onConfigChange={vi.fn()} onRemove={vi.fn()} />);
    expect(screen.getByText("Budget")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /remove/i })).toBeInTheDocument();
  });

  it("shows an unknown-widget fallback for an unregistered type, never throwing", () => {
    render(
      <WidgetFrame
        instance={makeInstance({ type: "future-type" as never })}
        onConfigChange={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    expect(screen.getByText(/unknown widget/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /remove/i })).toBeInTheDocument();
  });

  it("calls onRemove when the remove button is clicked", () => {
    const onRemove = vi.fn();
    render(<WidgetFrame instance={makeInstance()} onConfigChange={vi.fn()} onRemove={onRemove} />);
    fireEvent.click(screen.getByRole("button", { name: /remove/i }));
    expect(onRemove).toHaveBeenCalled();
  });

  it("catches a rendering exception in the widget without unmounting the frame", () => {
    render(
      <WidgetFrame
        instance={makeInstance({ type: "activity" })}
        onConfigChange={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    // The mocked ActivityWidget above throws during render -- the error
    // boundary only swallows the widget's own subtree, so the frame's
    // chrome (label, remove button) stays mounted around the fallback.
    expect(screen.getByText("Activity")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /remove/i })).toBeInTheDocument();
    expect(screen.getByText(/could not be displayed/i)).toBeInTheDocument();
  });
});
