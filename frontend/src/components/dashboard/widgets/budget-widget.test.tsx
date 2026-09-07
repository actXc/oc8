import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BudgetWidget } from "./budget-widget";
import * as hooks from "@/lib/hooks";

describe("BudgetWidget", () => {
  it("shows current vs. limit and the hard-exceeded state", () => {
    vi.spyOn(hooks, "useBudgetStatus").mockReturnValue({
      data: {
        scope: "tenant",
        departmentId: null,
        softLimitTokens: 1000,
        hardLimitTokens: 2000,
        currentTokens: 2500,
        softExceeded: true,
        hardExceeded: true,
      },
      isPending: false,
    } as never);

    render(<BudgetWidget config={{}} onConfigChange={vi.fn()} />);
    // Use locale-formatted strings to tolerate different locale settings
    const expectedCurrent = (2500).toLocaleString();
    const expectedLimit = (2000).toLocaleString();
    expect(screen.getByText(new RegExp(expectedCurrent))).toBeInTheDocument();
    expect(screen.getByText(new RegExp(expectedLimit))).toBeInTheDocument();
  });
});
