import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mayMock = vi.fn();
const saveMutate = vi.fn();

vi.mock("@/lib/governance-hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/governance-hooks")>();
  return { ...actual, useMay: () => mayMock };
});

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return { ...actual, useSaveDashboardPreset: () => ({ mutate: saveMutate, isPending: false }) };
});

import { SavePresetDialog } from "./save-preset-dialog";
import type { WidgetInstance } from "@/lib/hooks";

const WIDGETS: WidgetInstance[] = [{ id: "w1", type: "chat", x: 0, y: 0, w: 6, h: 6, config: {} }];

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("SavePresetDialog", () => {
  beforeEach(() => {
    mayMock.mockReset();
    saveMutate.mockReset();
  });

  it("saves a personal preset with the entered name", async () => {
    mayMock.mockReturnValue(false);
    render(<SavePresetDialog open onOpenChange={vi.fn()} widgets={WIDGETS} />, { wrapper });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "My layout" } });
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));
    await waitFor(() =>
      expect(saveMutate).toHaveBeenCalledWith(
        { name: "My layout", widgets: WIDGETS, scope: "personal" },
        expect.anything(),
      ),
    );
  });

  it("disables the tenant-wide option without settings:manage", () => {
    mayMock.mockReturnValue(false);
    render(<SavePresetDialog open onOpenChange={vi.fn()} widgets={WIDGETS} />, { wrapper });
    expect(screen.getByLabelText(/For the whole team/i)).toBeDisabled();
  });

  it("allows choosing tenant scope when the caller holds settings:manage", async () => {
    mayMock.mockReturnValue(true);
    render(<SavePresetDialog open onOpenChange={vi.fn()} widgets={WIDGETS} />, { wrapper });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Team layout" } });
    fireEvent.click(screen.getByLabelText(/For the whole team/i));
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));
    await waitFor(() =>
      expect(saveMutate).toHaveBeenCalledWith(
        { name: "Team layout", widgets: WIDGETS, scope: "tenant" },
        expect.anything(),
      ),
    );
  });

  it("disables save until a name is entered", () => {
    mayMock.mockReturnValue(false);
    render(<SavePresetDialog open onOpenChange={vi.fn()} widgets={WIDGETS} />, { wrapper });
    expect(screen.getByRole("button", { name: /^Save$/i })).toBeDisabled();
  });
});
