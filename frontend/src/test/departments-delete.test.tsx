import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { DepartmentSettingsPanel } from "@/routes/departments.$id";

const { getDepartment, getDepartmentAgents, deleteDepartment, navigate, toastSuccess, toastError } =
  vi.hoisted(() => ({
    getDepartment: vi.fn(),
    getDepartmentAgents: vi.fn(),
    deleteDepartment: vi.fn(),
    navigate: vi.fn(),
    toastSuccess: vi.fn(),
    toastError: vi.fn(),
  }));

vi.mock("@/lib/api", () => ({
  api: {
    get: (url: string) => (url.includes("/agents") ? getDepartmentAgents() : getDepartment()),
    delete: () => deleteDepartment(),
  },
}));

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("sonner", async (importOriginal) => {
  const actual = await importOriginal<typeof import("sonner")>();
  return { ...actual, toast: { ...actual.toast, success: toastSuccess, error: toastError } };
});

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const dept = {
  id: "dept-1",
  name: "Vertrieb",
  icon: "sales",
  goal: "",
  okr: "",
  kpiLabel: "",
  kpiValue: "",
  activity: 0,
  accent: "",
  promptCachingEnabled: true,
};

describe("DepartmentSettingsPanel: delete department", () => {
  beforeEach(() => {
    getDepartment.mockReset();
    getDepartmentAgents.mockReset();
    deleteDepartment.mockReset();
    navigate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    getDepartment.mockResolvedValue(dept);
    getDepartmentAgents.mockResolvedValue([{ id: "a1" }, { id: "a2" }]);
  });

  it("opens a confirmation modal naming the department and its agent count", async () => {
    renderWithClient(<DepartmentSettingsPanel departmentId="dept-1" />);

    fireEvent.click(await screen.findByRole("button", { name: /Delete…/i }));

    expect(await screen.findByText(/Delete.*Vertrieb/i)).toBeInTheDocument();
    expect(screen.getByText(/all 2 agents/i)).toBeInTheDocument();
  });

  it("keeps the confirm button disabled until the typed name matches exactly", async () => {
    renderWithClient(<DepartmentSettingsPanel departmentId="dept-1" />);
    fireEvent.click(await screen.findByRole("button", { name: /Delete…/i }));

    const confirmButton = await screen.findByRole("button", { name: /Delete department/i });
    expect(confirmButton).toBeDisabled();

    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Vertrie" } });
    expect(confirmButton).toBeDisabled();

    fireEvent.change(input, { target: { value: "Vertrieb" } });
    expect(confirmButton).not.toBeDisabled();
  });

  it("deletes, toasts the outcome, and navigates away on confirm", async () => {
    deleteDepartment.mockResolvedValue({ outcome: "archived" });
    renderWithClient(<DepartmentSettingsPanel departmentId="dept-1" />);
    fireEvent.click(await screen.findByRole("button", { name: /Delete…/i }));

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Vertrieb" } });
    fireEvent.click(screen.getByRole("button", { name: /Delete department/i }));

    await waitFor(() => expect(deleteDepartment).toHaveBeenCalled());
    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: "/" }));
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("shows an error toast and does not navigate when the delete fails", async () => {
    deleteDepartment.mockRejectedValue(new Error("500"));
    renderWithClient(<DepartmentSettingsPanel departmentId="dept-1" />);
    fireEvent.click(await screen.findByRole("button", { name: /Delete…/i }));

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Vertrieb" } });
    fireEvent.click(screen.getByRole("button", { name: /Delete department/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(navigate).not.toHaveBeenCalled();
  });

  it("cancel closes the modal without deleting", async () => {
    renderWithClient(<DepartmentSettingsPanel departmentId="dept-1" />);
    fireEvent.click(await screen.findByRole("button", { name: /Delete…/i }));
    await screen.findByRole("textbox");

    fireEvent.click(screen.getByRole("button", { name: /Cancel/i }));

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(deleteDepartment).not.toHaveBeenCalled();
  });
});
