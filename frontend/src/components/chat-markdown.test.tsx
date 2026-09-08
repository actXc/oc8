import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { ChatMarkdown } from "@/components/chat-markdown";

describe("ChatMarkdown", () => {
  it("renders a fenced code block distinctly from inline code", () => {
    const { container } = render(<ChatMarkdown text={"```\nconst x = 1;\n```"} />);
    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre?.className).toContain("overflow-x-auto");
  });

  it("renders a GFM table with horizontal-scroll overflow handling", () => {
    const table = "| A | B |\n| - | - |\n| 1 | 2 |";
    const { container } = render(<ChatMarkdown text={table} />);
    const tableEl = container.querySelector("table");
    expect(tableEl).not.toBeNull();
    expect(container.querySelector("th")).not.toBeNull();
  });

  it("shows a language label read from the fence info string", () => {
    const { container } = render(<ChatMarkdown text={"```ts\nconst x = 1;\n```"} />);
    expect(container.textContent).toContain("ts");
  });

  it("copies a code block's text to the clipboard via the copy button", () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const { getByRole } = render(<ChatMarkdown text={"```\nconst x = 1;\n```"} />);
    fireEvent.click(getByRole("button", { name: /copy/i }));
    expect(writeText).toHaveBeenCalledWith("const x = 1;");
  });
});
