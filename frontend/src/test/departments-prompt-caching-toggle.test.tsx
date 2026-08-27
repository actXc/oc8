import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { DepartmentSettingsPanel } from "@/routes/departments.$id";

const { getDepartment, patchDepartment, toastError } = vi.hoisted(() => ({
  getDepartment: vi.fn(),
  patchDepartment: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    get: getDepartment,
    patch: patchDepartment,
  },
}));

vi.mock("sonner", async (importOriginal) => {
  const actual = await importOriginal<typeof import("sonner")>();
  return { ...actual, toast: { ...actual.toast, error: toastError } };
});

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("DepartmentSettingsPanel: prompt caching toggle", () => {
  beforeEach(() => {
    getDepartment.mockReset();
    patchDepartment.mockReset();
    toastError.mockReset();
  });

  it("renders ON when promptCachingEnabled is true", async () => {
    getDepartment.mockResolvedValue({
      id: "dept-1",
      name: "Support",
      icon: "support",
      goal: "",
      okr: "",
      kpiLabel: "",
      kpiValue: "",
      activity: 0,
      accent: "",
      promptCachingEnabled: true,
    });

    renderWithClient(<DepartmentSettingsPanel departmentId="dept-1" />);

    const toggle = await screen.findByRole("switch");
    expect(toggle).toHaveAttribute("aria-checked", "true");
  });

  it("clicking the toggle PATCHes promptCachingEnabled: false", async () => {
    getDepartment.mockResolvedValue({
      id: "dept-1",
      name: "Support",
      icon: "support",
      goal: "",
      okr: "",
      kpiLabel: "",
      kpiValue: "",
      activity: 0,
      accent: "",
      promptCachingEnabled: true,
    });
    patchDepartment.mockResolvedValue({});

    renderWithClient(<DepartmentSettingsPanel departmentId="dept-1" />);
    const toggle = await screen.findByRole("switch");
    fireEvent.click(toggle);

    await waitFor(() =>
      expect(patchDepartment).toHaveBeenCalledWith("/departments/dept-1", {
        promptCachingEnabled: false,
      }),
    );
  });

  it("shows an error toast when the PATCH fails", async () => {
    // Without an onError the switch simply doesn't move and the operator is
    // told nothing -- a silent no-op on a setting they believe they changed.
    getDepartment.mockResolvedValue({
      id: "dept-1",
      name: "Support",
      icon: "support",
      goal: "",
      okr: "",
      kpiLabel: "",
      kpiValue: "",
      activity: 0,
      accent: "",
      promptCachingEnabled: true,
    });
    patchDepartment.mockRejectedValue(new Error("500"));

    renderWithClient(<DepartmentSettingsPanel departmentId="dept-1" />);
    const toggle = await screen.findByRole("switch");
    fireEvent.click(toggle);

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Could not save prompt caching setting"),
    );
  });

  it("shows the translated label", async () => {
    // `useT()` falls back to English (the harness's context default -- see
    // `departments-guardrails.test.tsx`, which asserts on English strings
    // like "Assist with approval" for the same reason) since these tests
    // don't wrap the tree in `LanguageProvider`.
    getDepartment.mockResolvedValue({
      id: "dept-1",
      name: "Support",
      icon: "support",
      goal: "",
      okr: "",
      kpiLabel: "",
      kpiValue: "",
      activity: 0,
      accent: "",
      promptCachingEnabled: true,
    });

    renderWithClient(<DepartmentSettingsPanel departmentId="dept-1" />);

    expect(await screen.findByText("Prompt caching")).toBeInTheDocument();
  });
});
