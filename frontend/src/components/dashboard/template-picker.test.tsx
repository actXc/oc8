import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { TemplatePicker } from "./template-picker";
import * as hooks from "@/lib/hooks";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("TemplatePicker", () => {
  it("calls onPick with the chosen template", async () => {
    const template = {
      id: "focus-chat",
      name: { en: "Focus Chat", de: "Fokus-Chat" },
      widgets: [],
    };
    vi.spyOn(hooks, "useDashboardTemplates").mockReturnValue({
      data: [template],
      isPending: false,
    } as never);
    const onPick = vi.fn();
    render(<TemplatePicker onPick={onPick} />, { wrapper });
    await waitFor(() => screen.getByText("Focus Chat"));
    fireEvent.click(screen.getByText("Focus Chat"));
    expect(onPick).toHaveBeenCalledWith(template);
  });
});
