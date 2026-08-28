import { render, screen } from "@testing-library/react";
import { RouterProvider } from "@tanstack/react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getRouter } from "../router";

// Pins `AuthGate`'s community-mode redirect check in `routes/__root.tsx`.
//
// Task 7 added `GATE_PUBLIC_ROUTES` (`/login` + the three new self-service
// routes) so a session-less visitor can actually reach forgot-password /
// reset-password / confirm-email instead of being bounced to /login. An
// earlier version of that change collapsed the gate-check list and the
// AppShell-skip list into one shared set, which had the side effect of
// ALSO admitting `/welcome` to the gate-check -- a route that has always
// required a session by construction (it's only ever reached right after
// setup/login mints one) and was never meant to become reachable without
// one. This file exists so that regression cannot come back silently: it
// pins /welcome's ORIGINAL behavior (redirected away when session-less in
// community mode) side by side with one of the genuinely-new public routes
// (reachable, no redirect).

vi.mock("@/lib/i18n", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/i18n")>();
  return { ...actual, useT: () => (en: string) => en };
});

function stubCommunityConfig() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ mode: "community", initialized: true }),
    }),
  );
}

async function renderAt(pathname: string) {
  window.history.replaceState({}, "", pathname);
  const router = getRouter();
  await router.load();
  render(<RouterProvider router={router} />);
}

describe("AuthGate: which routes a session-less community-mode visitor may reach", () => {
  beforeEach(() => {
    window.localStorage.removeItem("oc8-community-token");
    window.localStorage.removeItem("oc8-dev-token");
  });

  it("still redirects a session-less visitor away from /welcome -- unchanged by Task 7", async () => {
    stubCommunityConfig();
    await renderAt("/welcome");

    // `AuthGate` sets `window.location.href = "/login"` and renders this
    // placeholder in the same branch, without ever mounting `children` (the
    // onboarding wizard `/welcome` would otherwise show).
    expect(await screen.findByText("Redirecting…")).toBeInTheDocument();
  });

  it("lets a session-less visitor reach /forgot-password with no redirect", async () => {
    stubCommunityConfig();
    await renderAt("/forgot-password");

    expect(await screen.findByLabelText("Your email")).toBeInTheDocument();
    expect(screen.queryByText("Redirecting…")).not.toBeInTheDocument();
  });
});
