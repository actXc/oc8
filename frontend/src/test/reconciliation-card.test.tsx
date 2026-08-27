import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// Fix C1 regression test: rows in model_cost_reconciliation are created
// exclusively by clicking Refresh, so gating the card's visibility on
// rows.length (the old, buggy condition) made the Refresh button
// unreachable on first use. Visibility must instead be gated on an admin
// key existing (see routes/costs.tsx's ReconciliationCard).

const useReconciliationMock = vi.fn();

vi.mock("@/lib/hooks", () => ({
  useSecrets: vi.fn(),
}));

vi.mock("@/lib/reconciliation-hooks", () => ({
  useReconciliation: () => useReconciliationMock(),
  useRefreshReconciliation: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import { useSecrets } from "@/lib/hooks";
import { ReconciliationCard } from "@/routes/costs";

function renderCard() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ReconciliationCard currency="USD" />
    </QueryClientProvider>,
  );
}

describe("ReconciliationCard", () => {
  it("shows the Refresh button when an admin key is connected but no rows exist yet", () => {
    vi.mocked(useSecrets).mockReturnValue({
      data: [
        {
          id: "s1",
          name: "model/anthropic/admin_key",
          kind: "generic",
          keyVersion: "v1",
          createdAt: "2026-08-19T00:00:00Z",
        },
      ],
    } as never);
    useReconciliationMock.mockReturnValue({ data: [] });

    renderCard();

    expect(screen.getByRole("button", { name: /Refresh/i })).toBeInTheDocument();
    expect(screen.getByText(/Not refreshed yet/i)).toBeInTheDocument();
  });

  it("renders nothing when no admin key is connected, even with zero rows", () => {
    vi.mocked(useSecrets).mockReturnValue({ data: [] } as never);
    useReconciliationMock.mockReturnValue({ data: [] });

    const { container } = renderCard();

    expect(container).toBeEmptyDOMElement();
  });
});
