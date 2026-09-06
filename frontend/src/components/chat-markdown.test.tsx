import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
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
});
