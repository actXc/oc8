import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ListToolbar, groupItems, type ListQueryState } from "@/components/list-toolbar";

const baseState: ListQueryState = {
  search: "",
  filters: {},
  groupBy: null,
  includeArchived: false,
  page: 1,
  pageSize: 20,
};

describe("ListToolbar", () => {
  it("calls onStateChange with updated search text", () => {
    const onStateChange = vi.fn();
    render(
      <ListToolbar
        config={{ searchPlaceholder: "Search skills..." }}
        state={baseState}
        onStateChange={onStateChange}
        totalCount={0}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText("Search skills..."), {
      target: { value: "email" },
    });
    expect(onStateChange).toHaveBeenCalledWith({ ...baseState, search: "email", page: 1 });
  });

  it("shows the archived toggle only when configured", () => {
    const { rerender } = render(
      <ListToolbar
        config={{ searchPlaceholder: "x" }}
        state={baseState}
        onStateChange={() => {}}
        totalCount={0}
      />,
    );
    expect(screen.queryByText(/show archived/i)).not.toBeInTheDocument();
    rerender(
      <ListToolbar
        config={{ searchPlaceholder: "x", showArchivedToggle: true }}
        state={baseState}
        onStateChange={() => {}}
        totalCount={0}
      />,
    );
    expect(screen.getByText(/show archived/i)).toBeInTheDocument();
  });

  it("renders custom archived-toggle label when provided", () => {
    render(
      <ListToolbar
        config={{
          searchPlaceholder: "x",
          showArchivedToggle: true,
          archivedToggleLabel: "Show disabled",
        }}
        state={baseState}
        onStateChange={() => {}}
        totalCount={0}
      />,
    );
    expect(screen.getByText("Show disabled")).toBeInTheDocument();
  });
});

describe("groupItems", () => {
  it("buckets items by the resolved group key, preserving item order within a group", () => {
    const items = [
      { id: 1, cat: "a" },
      { id: 2, cat: "b" },
      { id: 3, cat: "a" },
    ];
    const grouped = groupItems(items, "category", (i) => i.cat);
    expect(grouped).toEqual([
      { group: "a", items: [items[0], items[2]] },
      { group: "b", items: [items[1]] },
    ]);
  });

  it("returns a single ungrouped bucket when groupBy is null", () => {
    const items = [{ id: 1 }];
    const grouped = groupItems(items, null, () => "x");
    expect(grouped).toEqual([{ group: null, items }]);
  });
});
