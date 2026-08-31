// Extends vitest's `expect` with jest-dom matchers (`toBeDisabled()`,
// `toBeInTheDocument()`, etc.) used throughout `src/test/*.test.tsx` -- none
// of those files import this themselves, so it has to happen once, here, via
// `vitest.config.ts`'s `setupFiles`.
import "@testing-library/jest-dom/vitest";

// jsdom has no ResizeObserver -- recharts' <ResponsiveContainer> (used by
// ui/chart.tsx's <ChartContainer>, first consumed by the agent-overview KPI
// trend graph) calls `new ResizeObserver(...)` on mount and throws
// "ResizeObserver is not defined" without this. A no-op stub is enough:
// tests only assert the chart renders, not that it resizes.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
