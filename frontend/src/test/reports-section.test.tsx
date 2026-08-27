import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const reportsMock = vi.fn();

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useReports: () => reportsMock(),
  };
});

import { ReportsSection } from "@/components/reports-section";

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ReportsSection />
    </QueryClientProvider>,
  );
}

describe("ReportsSection", () => {
  beforeEach(() => reportsMock.mockReset());

  it("renders nothing while loading", () => {
    reportsMock.mockReturnValue({ data: undefined, isLoading: true });
    const { container } = renderSection();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when there are no reports", () => {
    reportsMock.mockReturnValue({ data: [], isLoading: false });
    const { container } = renderSection();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders each report's agent name and its rendered components", () => {
    reportsMock.mockReturnValue({
      data: [
        {
          runId: "r1",
          agentId: "a1",
          agentName: "Finance Bot",
          createdAt: "2026-08-27T08:00:00Z",
          renderedComponents: [
            {
              componentKey: "record_card",
              props: { title: "Tagesbericht", fields: [] },
            },
          ],
        },
      ],
      isLoading: false,
    });
    renderSection();
    expect(screen.getByText("Reports")).toBeInTheDocument();
    expect(screen.getByText("Finance Bot")).toBeInTheDocument();
    expect(screen.getByText("Tagesbericht")).toBeInTheDocument();
  });
});
