import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DetailSheet } from "@/components/detail-sheet";

describe("DetailSheet", () => {
  it("renders title, description, and children when open", () => {
    render(
      <DetailSheet open title="My Title" description="My description" onOpenChange={() => {}}>
        <div>body content</div>
      </DetailSheet>,
    );
    expect(screen.getByText("My Title")).toBeInTheDocument();
    expect(screen.getByText("My description")).toBeInTheDocument();
    expect(screen.getByText("body content")).toBeInTheDocument();
  });

  it("renders footer actions when provided", () => {
    render(
      <DetailSheet open title="T" onOpenChange={() => {}} footer={<button>Archive</button>}>
        <div />
      </DetailSheet>,
    );
    expect(screen.getByRole("button", { name: "Archive" })).toBeInTheDocument();
  });

  it("calls onOpenChange(false) when closed", () => {
    const onOpenChange = vi.fn();
    render(
      <DetailSheet open title="T" onOpenChange={onOpenChange}>
        <div />
      </DetailSheet>,
    );
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
