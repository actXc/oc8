// Task 19 (Design System Consistency plan): the Departments list page now
// goes through <ListToolbar> (Task 14) instead of a plain "N departments"
// count -- server-side search/archive state lives in a local
// `ListQueryState` and is threaded straight into `useDepartments(params)`
// (Task 15). Unlike Skills (Task 16) and Agents (Task 18), Departments has NO
// `groupBy` -- Task 7 deliberately left `group_fields={}` for it since there's
// no sensible orderable group field -- so no groupBy config is passed and no
// grouping dropdown should render. Mirrors Task 18's
// `agents-list-toolbar.test.tsx`.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const useDepartmentsMock = vi.fn();

vi.mock("@/lib/hooks", () => ({
  useDepartments: (params: unknown) => useDepartmentsMock(params),
  useAgents: () => ({ data: { items: [], totalCount: 0 } }),
  useCreateDepartment: () => ({ mutate: vi.fn(), isPending: false }),
  useRestoreDepartment: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { DepartmentsPage } from "@/routes/departments.index";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DepartmentsPage />
    </QueryClientProvider>,
  );
}

describe("Departments list page", () => {
  beforeEach(() => {
    useDepartmentsMock.mockReset();
    useDepartmentsMock.mockReturnValue({ data: { items: [], totalCount: 0 } });
  });

  it("re-fetches with the typed search term", async () => {
    renderPage();

    fireEvent.change(screen.getByPlaceholderText(/search departments/i), {
      target: { value: "sales" },
    });

    const lastCall = useDepartmentsMock.mock.calls.at(-1)?.[0];
    expect(lastCall).toMatchObject({ search: "sales" });
  });

  it("shows the archived toggle and re-fetches with includeArchived when checked", async () => {
    renderPage();

    fireEvent.click(screen.getByText(/show archived/i));

    const lastCall = useDepartmentsMock.mock.calls.at(-1)?.[0];
    expect(lastCall).toMatchObject({ includeArchived: true });
  });

  it("does not render a grouping dropdown -- Departments has no orderable group field", () => {
    renderPage();

    expect(screen.queryByText(/no grouping/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/group by/i)).not.toBeInTheDocument();
  });

  it("shows the exact tenant-wide total from totalCount, not the capped page length", () => {
    // Deliberately `items: []` here -- the count must come straight from
    // `totalCount`, not be derived from the items array's length (which the
    // real backend caps at pageSize=20). Rendering actual department cards
    // needs a <RouterProvider> that this lightweight render tree doesn't
    // set up, so populated-items rendering is exercised elsewhere and is out
    // of scope for this assertion.
    useDepartmentsMock.mockReturnValue({
      data: { items: [], totalCount: 57 },
    });

    renderPage();

    expect(screen.getByText(/57/)).toBeInTheDocument();
  });
});
