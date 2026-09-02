// Task 6 (Capa Exporter plan): covers the wizard built in Task 5
// (`capa-export-wizard.tsx`) -- selection correctness (a department's own
// agents drop out of the standalone-agent list once that department is
// checked), the local-only skill picker (store-origin skills never render),
// the preview step's wiring to `usePreviewCapaExport` (including
// `warnings`/`extra_files` rendering), and the final Export step's download
// trigger. Follows `mission-automation.test.tsx`'s full `vi.mock("@/lib/hooks"
// ...)` + bare `QueryClientProvider` convention, and `backup-panel.test.tsx`'s
// approach to the identical `URL.createObjectURL` + anchor-click download
// pattern (stub the URL statics, since jsdom doesn't implement them).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { previewMutate, downloadCapaExport } = vi.hoisted(() => ({
  previewMutate: vi.fn(),
  downloadCapaExport: vi.fn(),
}));

const DEPARTMENTS = [{ id: "dept-1", name: "Sales" }];
const AGENTS = [
  { id: "agent-1", name: "Ada", departmentId: "dept-1" },
  { id: "agent-2", name: "Bob", departmentId: undefined },
];
const SKILLS = [
  { id: "skill-1", name: "Local Skill", origin: "local" },
  { id: "skill-2", name: "Store Skill", origin: "store" },
];

// One fixed, already-resolved preview result -- reused by every test that
// reaches the Vorschau/Export steps. `isSuccess: true` is what gates the
// "Continue to export" and final "Export" buttons enabled, so it has to be
// present even in tests that only care about the download trigger.
const PREVIEW_RESULT = {
  isPending: false,
  isError: false,
  isSuccess: true,
  error: null,
  data: {
    items: [
      {
        folder_name: "dept_sales",
        manifest_toml: '[capa]\nname = "sales"\n',
        warnings: ["No agents will be included from an empty department"],
        extra_files: { "README.md": "This capa was exported from the Sales department." },
      },
    ],
    errors: [],
  },
};

vi.mock("@/lib/hooks", () => ({
  useDepartments: () => ({ data: { items: DEPARTMENTS } }),
  useAgents: () => ({ data: { items: AGENTS } }),
  useSkills: () => ({ data: { items: SKILLS } }),
  usePreviewCapaExport: () => ({ mutate: previewMutate, ...PREVIEW_RESULT }),
}));

vi.mock("@/lib/api", () => ({
  downloadCapaExport,
}));

import { CapaExportWizard } from "@/components/capa-export-wizard";

function renderWizard(onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <CapaExportWizard onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

describe("CapaExportWizard", () => {
  beforeEach(() => {
    previewMutate.mockReset();
    downloadCapaExport.mockReset();
    // jsdom does not implement these -- stubbed the same way
    // `backup-panel.test.tsx` stubs them for the identical download pattern.
    URL.createObjectURL = vi.fn(() => "blob:mock");
    URL.revokeObjectURL = vi.fn();
  });

  it("renders departments and standalone agents as checkboxes, and drops a covered agent once its department is checked", () => {
    renderWizard();

    expect(screen.getByRole("checkbox", { name: "Sales" })).toBeInTheDocument();
    // Before the department is checked, its agent is still a standalone pick.
    expect(screen.getByRole("checkbox", { name: "Ada" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Bob" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: "Sales" }));

    // Ada belongs to Sales -- now that Sales itself is selected, Ada would be
    // double-exported if she also stayed listed as a standalone agent.
    expect(screen.queryByRole("checkbox", { name: "Ada" })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Bob" })).toBeInTheDocument();
  });

  it("renders only local-origin skills as checkboxes; a store-origin skill never appears", () => {
    renderWizard();

    expect(screen.getByRole("checkbox", { name: "Local Skill" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Store Skill" })).not.toBeInTheDocument();
    // Not just absent as a checkbox -- absent anywhere on the step, proving
    // this is the origin filter and not, say, a disabled/unlabeled input.
    expect(screen.queryByText("Store Skill")).not.toBeInTheDocument();
  });

  it("calls the preview mutation with the built items and renders warnings and extra_files from the result", () => {
    renderWizard();

    fireEvent.click(screen.getByRole("checkbox", { name: "Sales" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    // Details step: one row for the selected department, defaulted name/version.
    expect(screen.getByText("Sales")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));

    expect(previewMutate).toHaveBeenCalledWith([
      { kind: "department", id: "dept-1", name: "sales", version: "1.0.0", summary: "" },
    ]);

    // Vorschau step, rendered straight from the mocked preview result.
    expect(screen.getByText("dept_sales/")).toBeInTheDocument();
    expect(
      screen.getByText("⚠ No agents will be included from an empty department"),
    ).toBeInTheDocument();
    expect(screen.getByText("README.md")).toBeInTheDocument();
    expect(
      screen.getByText("This capa was exported from the Sales department."),
    ).toBeInTheDocument();
  });

  it("clicking Export downloads the built items and triggers the browser download", async () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    downloadCapaExport.mockResolvedValue({
      blob: new Blob(["zip bytes"]),
      filename: "sales.zip",
    });
    const { onClose } = renderWizard();

    fireEvent.click(screen.getByRole("checkbox", { name: "Sales" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue to export" }));
    fireEvent.click(screen.getByRole("button", { name: "Export" }));

    await waitFor(() =>
      expect(downloadCapaExport).toHaveBeenCalledWith([
        { kind: "department", id: "dept-1", name: "sales", version: "1.0.0", summary: "" },
      ]),
    );
    expect(clickSpy).toHaveBeenCalled();
    expect(URL.createObjectURL).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalled();
    await waitFor(() => expect(onClose).toHaveBeenCalled());

    clickSpy.mockRestore();
  });
});
