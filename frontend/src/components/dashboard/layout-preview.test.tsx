import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LayoutPreview } from "./layout-preview";
import type { WidgetInstance } from "@/lib/hooks";

describe("LayoutPreview", () => {
  it("renders one tile per widget", () => {
    const widgets: WidgetInstance[] = [
      { id: "w1", type: "chat", x: 0, y: 0, w: 8, h: 8, config: {} },
      { id: "w2", type: "approvals", x: 8, y: 0, w: 4, h: 8, config: {} },
    ];
    const { container } = render(<LayoutPreview widgets={widgets} />);
    expect(container.querySelectorAll(":scope > div > div")).toHaveLength(2);
  });

  it("renders an empty sketch without crashing for an empty layout", () => {
    const { container } = render(<LayoutPreview widgets={[]} />);
    expect(container.querySelectorAll(":scope > div > div")).toHaveLength(0);
  });
});
