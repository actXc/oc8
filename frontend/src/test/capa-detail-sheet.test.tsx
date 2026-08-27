import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { DiscoveredCapa } from "@/lib/hooks";
import { CapaDetailSheet } from "@/routes/capas";

// Task 13: CapaDetailModal used to be a hand-rolled centered dialog
// (`fixed inset-0 z-50 grid place-items-center ...`). It now goes through
// the shared <DetailSheet> (Task 10), which renders a Radix Sheet with
// role="dialog" positioned on the right edge of the screen, consistent
// with SkillDrawer (Task 11) and the Knowledge detail sheet (Task 12).

const plugin: DiscoveredCapa = {
  pluginId: "github_mcp",
  name: "github_mcp",
  label: "GitHub",
  version: "1.0.0",
  type: "connector",
  trust: "first_party",
  summary: "Connects to GitHub.",
  valid: true,
  installed: true,
  installedVersion: "1.0.0",
  databaseId: null,
  installationStatus: "enabled",
  permissions: [],
  capabilities: [],
  surfaces: [],
  setup: null,
};

function renderSheet() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <CapaDetailSheet
        plugin={plugin}
        connections={[]}
        onConfigure={vi.fn()}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

describe("CapaDetailSheet", () => {
  it("renders as a right-side sheet, not a centered dialog", () => {
    renderSheet();

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();

    // Regression test for the actual UX change: the OLD component's root
    // className was "fixed inset-0 z-50 grid place-items-center bg-black/70
    // p-4" (a centered dialog). That exact combo must be gone from whatever
    // DOM DetailSheet renders. (Selecting on the full combo, not just
    // ".grid.place-items-center" alone, since CapaIcon's own wrapper
    // legitimately carries "grid place-items-center" for icon centering and
    // would otherwise cause a false positive.)
    expect(
      document.querySelector(".fixed.inset-0.z-50.grid.place-items-center"),
    ).not.toBeInTheDocument();

    // DetailSheet's own Radix SheetContent carries side="right" positioning
    // classes instead (see ui/sheet.tsx's sheetVariants "right" entry).
    expect(dialog.className).toContain("inset-y-0");
    expect(dialog.className).toContain("right-0");
    expect(dialog.className).toContain("data-[state=open]:slide-in-from-right");
  });
});
