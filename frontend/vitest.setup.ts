// Extends vitest's `expect` with jest-dom matchers (`toBeDisabled()`,
// `toBeInTheDocument()`, etc.) used throughout `src/test/*.test.tsx` -- none
// of those files import this themselves, so it has to happen once, here, via
// `vitest.config.ts`'s `setupFiles`.
import "@testing-library/jest-dom/vitest";
