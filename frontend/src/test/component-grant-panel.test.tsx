import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const grantsMock = vi.fn();
const createGrantMock = vi.fn();
const deleteGrantMock = vi.fn();

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useComponentGrants: () => grantsMock(),
    useCreateComponentGrant: () => ({ mutate: createGrantMock, isPending: false }),
    useDeleteComponentGrant: () => ({ mutate: deleteGrantMock, isPending: false }),
  };
});

import { ComponentGrantPanel } from "@/components/component-grant-panel";

function renderPanel(props: Partial<React.ComponentProps<typeof ComponentGrantPanel>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ComponentGrantPanel
        granteeType="agent"
        granteeId="agent-1"
        mayManage
        departmentId="dept-1"
        {...props}
      />
    </QueryClientProvider>,
  );
}

describe("ComponentGrantPanel", () => {
  beforeEach(() => {
    grantsMock.mockReset();
    createGrantMock.mockReset();
    deleteGrantMock.mockReset();
    grantsMock.mockReturnValue({ data: [] });
  });

  it("lists all four catalog components, unchecked when nothing is granted", () => {
    renderPanel();
    expect(screen.getByText("Record card")).toBeInTheDocument();
    expect(screen.getByText("Data table")).toBeInTheDocument();
    expect(screen.getByText("Bar chart")).toBeInTheDocument();
    expect(screen.getByText("Line chart")).toBeInTheDocument();
    for (const box of screen.getAllByRole("checkbox")) {
      expect(box).not.toBeChecked();
    }
  });

  it("checking an ungranted component creates a grant", () => {
    renderPanel();
    fireEvent.click(screen.getByText("Data table"));
    expect(createGrantMock).toHaveBeenCalledWith(
      { componentKey: "data_table", granteeType: "agent", granteeId: "agent-1" },
      expect.anything(),
    );
  });

  it("unchecking an already-granted component revokes it", () => {
    grantsMock.mockReturnValue({
      data: [{ id: "g1", componentKey: "data_table", granteeType: "agent", granteeId: "agent-1" }],
    });
    renderPanel();
    fireEvent.click(screen.getByText("Data table"));
    expect(deleteGrantMock).toHaveBeenCalledWith("g1", expect.anything());
  });

  it("shows a component granted at the department level as inherited and locked", () => {
    grantsMock.mockReturnValue({
      data: [
        { id: "g1", componentKey: "bar_chart", granteeType: "department", granteeId: "dept-1" },
      ],
    });
    renderPanel();
    expect(screen.getByText("Inherited from department")).toBeInTheDocument();
    const checkboxes = screen.getAllByRole("checkbox");
    const barChartRow = screen.getByText("Bar chart").closest("label");
    const inheritedBox = barChartRow?.querySelector("input[type=checkbox]");
    expect(inheritedBox).toBeChecked();
    expect(inheritedBox).toBeDisabled();
    expect(checkboxes.length).toBe(4);
  });

  it("disables every checkbox when the caller may not manage this grantee", () => {
    renderPanel({ mayManage: false });
    for (const box of screen.getAllByRole("checkbox")) {
      expect(box).toBeDisabled();
    }
  });
});
