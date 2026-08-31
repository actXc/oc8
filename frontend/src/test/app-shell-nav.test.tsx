import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// `useCan()` deliberately answers TRUE while `/governance`'s permissions
// query is still in flight -- otherwise every admin's sidebar would flash
// empty on cold load. For an item most callers do NOT hold (`/statistics`
// and `/audit` share `_NOT_VIEWABLE_BY_DEFAULT` on the backend), that same
// optimism means the link renders, then disappears once the real answer
// lands -- a visible flash rather than a silent absence. `strict: true`
// routes the item through `useMay()` instead, which fails safe (hidden)
// during that same window. See the `NavLink.strict` doc comment in
// app-shell.tsx for the full rationale and `/audit`'s prior art.
//
// There is no exported nav-link array to import and inspect directly --
// the list is built inline inside `AppShell()` -- so this reads the source
// itself, the same pattern `departments-guardrails.test.tsx`'s "guardrail
// preset picker identity" test uses for an equivalent structural check.
describe("app-shell nav: strict-hide for not-viewable-by-default items", () => {
  const src = readFileSync(join(process.cwd(), "src/components/app-shell.tsx"), "utf-8");

  function entryFor(to: string): string {
    const marker = `to: "${to}",`;
    const start = src.indexOf(marker);
    expect(start, `no nav entry found for ${to}`).toBeGreaterThan(-1);
    const end = src.indexOf("},", start);
    return src.slice(start, end);
  }

  it("/statistics sets strict: true, matching /audit's fail-safe-hide precedent", () => {
    expect(entryFor("/statistics")).toContain("strict: true");
  });

  it("/audit still sets strict: true (the precedent this mirrors)", () => {
    expect(entryFor("/audit")).toContain("strict: true");
  });
});
