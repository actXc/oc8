import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mayMock = vi.fn();
const deletePresetMutate = vi.fn();

vi.mock("@/lib/governance-hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/governance-hooks")>();
  return { ...actual, useMay: () => mayMock };
});

import { TemplatePicker } from "./template-picker";
import * as hooks from "@/lib/hooks";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const TEMPLATE = {
  id: "focus-chat",
  name: { en: "Focus Chat", de: "Fokus-Chat" },
  widgets: [{ id: "t1", type: "chat", x: 0, y: 0, w: 6, h: 6, config: {} }],
};

function preset(overrides: Partial<hooks.DashboardPresetDTO> = {}): hooks.DashboardPresetDTO {
  return {
    id: "preset-1",
    name: "My layout",
    widgets: [{ id: "w1", type: "chat", x: 0, y: 0, w: 6, h: 6, config: {} }],
    scope: "personal",
    mine: true,
    ...overrides,
  };
}

describe("TemplatePicker", () => {
  beforeEach(() => {
    mayMock.mockReset();
    mayMock.mockReturnValue(false);
    deletePresetMutate.mockReset();
    vi.spyOn(hooks, "useDeleteDashboardPreset").mockReturnValue({
      mutate: deletePresetMutate,
      isPending: false,
    } as never);
  });

  it("calls onPick with the chosen template", async () => {
    vi.spyOn(hooks, "useDashboardTemplates").mockReturnValue({
      data: [TEMPLATE],
      isPending: false,
    } as never);
    vi.spyOn(hooks, "useDashboardPresets").mockReturnValue({ data: [] } as never);
    const onPick = vi.fn();
    render(<TemplatePicker onPick={onPick} />, { wrapper });
    await waitFor(() => screen.getByText("Focus Chat"));
    fireEvent.click(screen.getByText("Focus Chat"));
    expect(onPick).toHaveBeenCalledWith(TEMPLATE);
  });

  it("lists saved presets and calls onPickPreset when one is chosen", async () => {
    vi.spyOn(hooks, "useDashboardTemplates").mockReturnValue({
      data: [],
      isPending: false,
    } as never);
    vi.spyOn(hooks, "useDashboardPresets").mockReturnValue({
      data: [preset({ name: "Team layout", scope: "tenant", mine: false })],
    } as never);
    const onPickPreset = vi.fn();
    render(<TemplatePicker onPick={vi.fn()} onPickPreset={onPickPreset} />, { wrapper });
    await waitFor(() => screen.getByText("Team layout"));
    expect(screen.getByText("Shared with the team")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Team layout" }));
    expect(onPickPreset).toHaveBeenCalled();
  });

  it("shows a delete control for the caller's own preset", async () => {
    vi.spyOn(hooks, "useDashboardTemplates").mockReturnValue({
      data: [],
      isPending: false,
    } as never);
    vi.spyOn(hooks, "useDashboardPresets").mockReturnValue({
      data: [preset({ mine: true })],
    } as never);
    render(<TemplatePicker onPick={vi.fn()} />, { wrapper });
    await waitFor(() => screen.getByText("My layout"));
    expect(screen.getByTitle("Delete preset")).toBeInTheDocument();
  });

  it("hides the delete control for a tenant preset the caller may not manage", async () => {
    vi.spyOn(hooks, "useDashboardTemplates").mockReturnValue({
      data: [],
      isPending: false,
    } as never);
    vi.spyOn(hooks, "useDashboardPresets").mockReturnValue({
      data: [preset({ scope: "tenant", mine: false })],
    } as never);
    mayMock.mockReturnValue(false);
    render(<TemplatePicker onPick={vi.fn()} />, { wrapper });
    await waitFor(() => screen.getByText("My layout"));
    expect(screen.queryByTitle("Delete preset")).not.toBeInTheDocument();
  });

  it("shows a delete control for a tenant preset when the caller holds settings:manage", async () => {
    vi.spyOn(hooks, "useDashboardTemplates").mockReturnValue({
      data: [],
      isPending: false,
    } as never);
    vi.spyOn(hooks, "useDashboardPresets").mockReturnValue({
      data: [preset({ scope: "tenant", mine: false })],
    } as never);
    mayMock.mockReturnValue(true);
    render(<TemplatePicker onPick={vi.fn()} />, { wrapper });
    await waitFor(() => screen.getByText("My layout"));
    expect(screen.getByTitle("Delete preset")).toBeInTheDocument();
  });

  it("asks for confirmation before deleting a preset", async () => {
    vi.spyOn(hooks, "useDashboardTemplates").mockReturnValue({
      data: [],
      isPending: false,
    } as never);
    vi.spyOn(hooks, "useDashboardPresets").mockReturnValue({
      data: [preset()],
    } as never);
    render(<TemplatePicker onPick={vi.fn()} />, { wrapper });
    await waitFor(() => screen.getByTitle("Delete preset"));
    fireEvent.click(screen.getByTitle("Delete preset"));
    expect(await screen.findByText(/Delete this preset\?/i)).toBeInTheDocument();
    expect(deletePresetMutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /^Delete$/i }));
    await waitFor(() => expect(deletePresetMutate).toHaveBeenCalledWith("preset-1"));
  });
});
