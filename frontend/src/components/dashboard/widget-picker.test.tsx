import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WidgetPicker } from "./widget-picker";

describe("WidgetPicker", () => {
  it("calls onAdd with the picked widget type", () => {
    const onAdd = vi.fn();
    render(<WidgetPicker onAdd={onAdd} />);
    fireEvent.pointerDown(screen.getByRole("button", { name: /add widget/i }), { button: 0 });
    fireEvent.click(screen.getByText("Budget"));
    expect(onAdd).toHaveBeenCalledWith("budget");
  });
});
