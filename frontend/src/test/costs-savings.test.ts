import { describe, expect, it } from "vitest";
import { buildDeptRows } from "@/routes/costs";
import type { UsageDTO } from "@/lib/hooks";
import type { Department } from "@/lib/mock-data";

const DEPT: Department = {
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
};

describe("buildDeptRows: cache savings", () => {
  it("converts savedCostMicros to USD the same way providerCostMicros already is", () => {
    const usage: UsageDTO[] = [
      {
        group: "dept-1",
        tokensIn: 200,
        tokensOut: 80,
        providerCostMicros: 9_000_000,
        savedTokensIn: 100,
        savedTokensOut: 50,
        savedCostMicros: 4_500_000,
      },
    ];

    const rows = buildDeptRows(usage, [DEPT], "Unassigned");

    expect(rows).toHaveLength(1);
    expect(rows[0].costUSD).toBeCloseTo(9);
    expect(rows[0].savedCostUSD).toBeCloseTo(4.5);
  });

  it("defaults savings to 0 when a department has no cache hits", () => {
    const usage: UsageDTO[] = [
      {
        group: "dept-1",
        tokensIn: 200,
        tokensOut: 80,
        providerCostMicros: 9_000_000,
        savedTokensIn: 0,
        savedTokensOut: 0,
        savedCostMicros: 0,
      },
    ];

    const rows = buildDeptRows(usage, [DEPT], "Unassigned");

    expect(rows[0].savedCostUSD).toBe(0);
  });
});
