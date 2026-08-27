// `useMayManageAgent` -- the frontend half of decision 2's WRITE authority
// (department-scoped-agent-authority design). Two independent grants, and this
// hook is the one place that reconciles them for a control on the page:
//
//   1. tenant-wide `agent:manage` (`callerPermissions`), same as `useMay()`.
//   2. the seat-native `agentManage` toggle, department-scoped, carried on
//      `Governance.seats` and NEVER folded into `callerPermissions` -- a seat is
//      authority somewhere, and a flat permission list cannot say where.
//
// No test runner is wired into this project yet (no vitest/jest config, no
// `@testing-library/react`, no `test`/`build:test` script) -- this file is
// written to the convention that repo will need the day one is added
// (`vitest` pairs with this project's Vite build; `@testing-library/react`'s
// `renderHook` is the standard way to exercise a hook that itself calls
// `useQuery`, which cannot be called as a bare function outside a component
// render). Until then it fails to resolve at all, which is the correct
// "fails now" signal for a hook (`useMayManageAgent`) and a field
// (`Seat.agentManage`) that do not exist yet -- see this slice's notes for what
// still needs adding before it can run.

import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import React from "react";

import { useMayManageAgent } from "./governance-hooks";
import type { Governance } from "./governance-hooks";
import type { Seat } from "./hooks";

//: `Seat` does not carry `agentManage` yet -- it graduates onto the interface
//: alongside this hook (see the design's "Frontend" section). Extending rather
//: than casting: a stray `as Seat` here would hide the exact thing this file
//: exists to prove is missing.
type SeatWithAgentManage = Seat & { agentManage: boolean };

function governanceWith(overrides: Partial<Governance>): Governance {
  return {
    permissions: [],
    roles: [],
    callerRole: "member",
    callerPermissions: [],
    callerRoleIsKnown: true,
    seats: [],
    ...overrides,
  };
}

/** A `QueryClientProvider` with `["governance"]` pre-seeded (or deliberately
 * left empty, for the pending case) -- the supported way to feed `useQuery`
 * data synchronously without a network call, so `renderHook` sees the
 * resolved (or still-pending) state on its very first render. */
function wrapperFor(governance: Governance | undefined) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  if (governance !== undefined) {
    client.setQueryData(["governance"], governance);
  }
  return function Wrapper({ children }: { children: ReactNode }) {
    return React.createElement(QueryClientProvider, { client }, children);
  };
}

describe("useMayManageAgent", () => {
  it("admits a tenant-wide agent:manage holder in any department, and with none named at all", () => {
    const governance = governanceWith({ callerPermissions: ["agent:manage"], seats: [] });
    const { result } = renderHook(() => useMayManageAgent(), { wrapper: wrapperFor(governance) });

    expect(result.current("11111111-1111-1111-1111-111111111111")).toBe(true);
    expect(result.current(null)).toBe(true);
    expect(result.current(undefined)).toBe(true);
  });

  it("admits a department-scoped seat toggle, ONLY in the department it was granted", () => {
    const sales = "22222222-2222-2222-2222-222222222222";
    const engineering = "33333333-3333-3333-3333-333333333333";
    const seats: SeatWithAgentManage[] = [
      {
        departmentId: sales,
        departmentName: "Vertrieb",
        seatRole: "dept_viewer",
        agentManage: true,
      },
    ];
    const governance = governanceWith({ callerPermissions: [], seats });
    const { result } = renderHook(() => useMayManageAgent(), { wrapper: wrapperFor(governance) });

    expect(result.current(sales)).toBe(true);
    expect(result.current(engineering)).toBe(false);
    expect(result.current(null)).toBe(false);
    expect(result.current(undefined)).toBe(false);
  });

  it("does not admit a live seat in the right department WITHOUT the toggle", () => {
    const sales = "22222222-2222-2222-2222-222222222222";
    const seats: SeatWithAgentManage[] = [
      {
        departmentId: sales,
        departmentName: "Vertrieb",
        seatRole: "dept_approver",
        agentManage: false,
      },
    ];
    const governance = governanceWith({ callerPermissions: [], seats });
    const { result } = renderHook(() => useMayManageAgent(), { wrapper: wrapperFor(governance) });

    expect(result.current(sales)).toBe(false);
  });

  it("fails closed while useGovernance is pending or has errored -- never true before the model lands", () => {
    // No query data seeded at all: `useGovernance` starts (and, since the
    // client has no way to reach a real backend here, stays) in the state
    // `data` is `undefined` -- the same branch `useMayManageAgent`'s own logic
    // takes for "pending" and for "errored"; there is no third state its
    // `if (!data) return false` distinguishes.
    const { result } = renderHook(() => useMayManageAgent(), { wrapper: wrapperFor(undefined) });

    expect(result.current("11111111-1111-1111-1111-111111111111")).toBe(false);
    expect(result.current(null)).toBe(false);
  });
});
