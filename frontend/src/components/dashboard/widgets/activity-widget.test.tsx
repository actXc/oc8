import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ActivityWidget } from "./activity-widget";
import * as hooks from "@/lib/hooks";

describe("ActivityWidget", () => {
  it("shows at most the 8 most recent entries", () => {
    const items = Array.from({ length: 12 }, (_, i) => ({
      id: `e${i}`,
      message: `Event ${i}`,
      agentId: "agent-1",
      status: "success" as const,
      time: new Date().toISOString(),
    }));
    vi.spyOn(hooks, "useActivity").mockReturnValue({ data: items, isPending: false } as never);

    render(<ActivityWidget config={{}} onConfigChange={vi.fn()} />);
    expect(screen.getByText("Event 0")).toBeInTheDocument();
    expect(screen.queryByText("Event 8")).not.toBeInTheDocument();
  });
});
