import { act, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider } from "@tanstack/react-router";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getRouter } from "../router";

// The bug this file guards: `routes/login.tsx` raises a toast (login success,
// and Task 12's 2FA grace-period nag) and navigates /login -> / in the same
// breath. While `PublicAuthLayout` and `AppShell` each mounted their OWN
// <Toaster/>, the toast was dispatched into a Toaster that unmounted with the
// route, and `AppShell`'s freshly mounted replacement seeds from empty state --
// `sonner` never replays toasts raised before a Toaster existed. So the operator
// never saw it. The fix hoists a single <Toaster/> into `RootComponent`, which
// is the one wrapper that does not unmount across that swap.

vi.mock("@/lib/i18n", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/i18n")>();
  return { ...actual, useT: () => (en: string) => en };
});

describe("app-wide Toaster placement", () => {
  beforeEach(() => {
    // A live session, so `AuthGate` renders the shell on "/" instead of
    // bouncing back to /login -- this test is about the /login -> / swap.
    window.localStorage.setItem("oc8-community-token", "session-token");
    // `/` mounts the whole application shell, which fans out into a lot of
    // unrelated data hooks; hand each of them a shape it can survive so this
    // test fails only on the Toaster question it is actually asking.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(typeof input === "string" ? input : input.toString());
        const body: unknown = url.includes("/auth/config")
          ? { mode: "community", initialized: true }
          : [];
        return { ok: true, status: 200, json: async () => body, text: async () => "[]" };
      }),
    );
  });

  it("keeps a toast raised on /login alive across the navigation to /", async () => {
    window.history.replaceState({}, "", "/login");
    const router = getRouter();
    await router.load();

    render(<RouterProvider router={router} />);
    await screen.findByRole("button", { name: "Sign In" });

    // Exactly what `handleLogin` does: raise the nag, then navigate away.
    await act(async () => {
      toast.warning("Set up two-factor authentication within 7 day(s)");
      await router.navigate({ to: "/" });
    });

    // The docking station is gone -- the route really did swap.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Sign In" })).not.toBeInTheDocument(),
    );
    // ...and the toast raised before the swap is still on screen.
    expect(
      screen.getByText("Set up two-factor authentication within 7 day(s)"),
    ).toBeInTheDocument();
  });

  it("renders the Toaster outside the swapped route content, on both sides of the swap", async () => {
    window.history.replaceState({}, "", "/login");
    const router = getRouter();
    await router.load();

    const { container } = render(<RouterProvider router={router} />);
    await screen.findByRole("button", { name: "Sign In" });

    // `sonner` only mounts its list element once something is in it.
    act(() => {
      toast.success("Logged in successfully");
    });
    await screen.findByText("Logged in successfully");
    const toasterOnLogin = container.querySelector("[data-sonner-toaster]");
    expect(toasterOnLogin).not.toBeNull();
    // Structural proof it is NOT inside the content that gets replaced: the
    // docking station is the whole of `/login`'s route content.
    const dockingStation = container.querySelector(".oc8-docking-station");
    expect(dockingStation).not.toBeNull();
    expect(dockingStation?.contains(toasterOnLogin as Node)).toBe(false);

    await act(async () => {
      await router.navigate({ to: "/" });
    });

    // Same element instance after the swap -- it was never unmounted.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Sign In" })).not.toBeInTheDocument(),
    );
    expect(container.querySelector("[data-sonner-toaster]")).toBe(toasterOnLogin);
  });
});
