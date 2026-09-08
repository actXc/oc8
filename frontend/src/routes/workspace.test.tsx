import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import * as hooks from "@/lib/hooks";

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...actual,
    createFileRoute: () => (opts: unknown) => opts,
  };
});

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("WorkspacePage", () => {
  it("shows the template picker when the caller has no saved layout", async () => {
    vi.spyOn(hooks, "useDashboardLayout").mockReturnValue({
      data: null,
      isPending: false,
    } as never);
    vi.spyOn(hooks, "useDashboardTemplates").mockReturnValue({
      data: [{ id: "focus-chat", name: { en: "Focus Chat", de: "Fokus-Chat" }, widgets: [] }],
      isPending: false,
    } as never);

    const { WorkspacePage } = await import("./workspace");
    render(<WorkspacePage />, { wrapper });
    await waitFor(() => screen.getByText(/choose a starting layout/i));
  });

  it("shows the grid once a layout exists", async () => {
    vi.spyOn(hooks, "useDashboardLayout").mockReturnValue({
      data: {
        widgets: [{ id: "w1", type: "budget", x: 0, y: 0, w: 3, h: 3, config: {} }],
        templateId: "focus-chat",
      },
      isPending: false,
    } as never);
    vi.spyOn(hooks, "useSaveDashboardLayout").mockReturnValue({ mutate: vi.fn() } as never);
    vi.spyOn(hooks, "useBudgetStatus").mockReturnValue({
      data: undefined,
      isPending: true,
    } as never);

    const { WorkspacePage } = await import("./workspace");
    render(<WorkspacePage />, { wrapper });
    await waitFor(() => screen.getByText("Budget"));
  });

  it("does not show the template picker when the layout query errors, and never PUTs the picked-template replacement", async () => {
    vi.spyOn(hooks, "useDashboardLayout").mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      error: new Error("network error"),
      refetch: vi.fn(),
    } as never);
    vi.spyOn(hooks, "useDashboardTemplates").mockReturnValue({
      data: [{ id: "focus-chat", name: { en: "Focus Chat", de: "Fokus-Chat" }, widgets: [] }],
      isPending: false,
    } as never);
    const mutate = vi.fn();
    vi.spyOn(hooks, "useSaveDashboardLayout").mockReturnValue({ mutate } as never);

    const { WorkspacePage } = await import("./workspace");
    render(<WorkspacePage />, { wrapper });

    await waitFor(() => screen.getByRole("button", { name: /retry/i }));
    expect(screen.queryByText(/choose a starting layout/i)).not.toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("does not PUT the layout back on a page load that changes nothing", async () => {
    const mutate = vi.fn();
    vi.spyOn(hooks, "useDashboardLayout").mockReturnValue({
      data: {
        widgets: [{ id: "w1", type: "budget", x: 0, y: 0, w: 3, h: 3, config: {} }],
        templateId: "focus-chat",
      },
      isPending: false,
    } as never);
    vi.spyOn(hooks, "useSaveDashboardLayout").mockReturnValue({ mutate } as never);
    vi.spyOn(hooks, "useBudgetStatus").mockReturnValue({
      data: undefined,
      isPending: true,
    } as never);

    const { WorkspacePage } = await import("./workspace");
    render(<WorkspacePage />, { wrapper });
    await waitFor(() => screen.getByText("Budget"));

    // The debounced save fires 800ms after the value it watches changes --
    // wait past that with no user interaction and confirm it never ran.
    await new Promise((resolve) => setTimeout(resolve, 900));
    expect(mutate).not.toHaveBeenCalled();
  }, 3000);
});
