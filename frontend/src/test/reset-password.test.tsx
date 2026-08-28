import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider } from "@tanstack/react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getRouter } from "../router";

// `Route.useSearch()` reads real router state, so these render through the
// full router (like `login.test.tsx`'s "outside the application shell"
// tests) rather than mounting the bare component -- there is no context to
// read `?token=` from otherwise.
const { navigate, toastSuccess, toastError } = vi.hoisted(() => ({
  navigate: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("@/lib/i18n", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/i18n")>();
  return { ...actual, useT: () => (en: string) => en };
});

vi.mock("sonner", async (importOriginal) => {
  const actual = await importOriginal<typeof import("sonner")>();
  return {
    ...actual,
    toast: { ...actual.toast, success: toastSuccess, error: toastError },
  };
});

async function renderAt(pathname: string) {
  window.history.replaceState({}, "", pathname);
  const router = getRouter();
  await router.load();
  render(<RouterProvider router={router} />);
}

/** `__root.tsx`'s `AuthGate` fetches `/auth/config` on every route, including
 *  this one -- it has to answer "community" (and PUBLIC_ROUTES has to list
 *  `/reset-password`, which it does) or a session-less visit here bounces to
 *  DevSignIn/`/login` before the route under test ever renders. Anything
 *  else routes to `endpointResponse`, the one this test actually cares about. */
function stubFetch(endpointResponse: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/auth/config")) {
        return { ok: true, json: async () => ({ mode: "community", initialized: true }) };
      }
      return endpointResponse;
    }),
  );
}

describe("Reset-password route", () => {
  beforeEach(() => {
    navigate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  it("submits the token from the query string with the new password, no bearer header, and navigates to /login", async () => {
    stubFetch({ status: 204, ok: true, json: async () => ({}) });
    const fetchMock = vi.mocked(fetch);

    await renderAt("/reset-password?token=abc123");
    await screen.findByRole("button", { name: "Set new password" });

    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "a-strong-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Set new password" }));

    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(navigate).toHaveBeenCalledWith({ to: "/login" });

    const resetCall = fetchMock.mock.calls.find(([u]) =>
      String(u).includes("/auth/password/reset"),
    );
    expect(resetCall).toBeDefined();
    const [url, init] = resetCall!;
    expect(String(url)).toContain("/auth/password/reset");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({
      token: "abc123",
      newPassword: "a-strong-password",
    });
    // No Authorization header: this endpoint is `unguarded` on the backend --
    // the mailed token is the credential, not a bearer session.
    expect((init?.headers as Record<string, string> | undefined)?.authorization).toBeUndefined();
  });

  it("keeps submit disabled when there is no token in the URL at all", async () => {
    stubFetch({ status: 204, ok: true, json: async () => ({}) });
    await renderAt("/reset-password");
    await screen.findByLabelText("New password");

    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "a-strong-password" },
    });

    expect(screen.getByRole("button", { name: "Set new password" })).toBeDisabled();
  });

  it("shows a generic invalid-or-expired message on failure, never the backend's specific reason", async () => {
    stubFetch({ status: 400, ok: false, json: async () => ({ detail: "token already used" }) });

    await renderAt("/reset-password?token=stale-token");
    await screen.findByRole("button", { name: "Set new password" });

    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "a-strong-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Set new password" }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("This link is invalid or has expired."),
    );
    // Not the backend's actual detail text ("token already used") -- that
    // would let a caller distinguish reasons the endpoint deliberately hides.
    expect(toastError).not.toHaveBeenCalledWith(expect.stringContaining("already used"));
    expect(navigate).not.toHaveBeenCalled();
  });
});
