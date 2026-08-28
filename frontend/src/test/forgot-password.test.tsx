import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ForgotPasswordPage } from "../routes/forgot-password";

vi.mock("@/lib/i18n", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/i18n")>();
  return { ...actual, useT: () => (en: string) => en };
});

describe("Forgot-password route", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ status: 202, ok: true, json: async () => ({ message: "ok" }) }),
    );
  });

  it("posts the email with no bearer token (this caller has none) and shows the same sent state", async () => {
    const fetchMock = vi.mocked(fetch);
    render(<ForgotPasswordPage />);

    fireEvent.change(screen.getByLabelText("Your email"), {
      target: { value: "person@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    await waitFor(() =>
      expect(
        screen.getByText(
          "If that email exists and a mail server is configured, a reset link was sent.",
        ),
      ).toBeInTheDocument(),
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/auth/password/forgot");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ email: "person@example.com" });
    // No Authorization header at all: `getToken()`/`api.post` would throw
    // "not signed in" for this caller before the request even left the
    // browser, since a person on this route by definition has no session.
    expect((init?.headers as Record<string, string> | undefined)?.authorization).toBeUndefined();
  });

  it("shows the same sent state even when the request fails outright, never leaking account existence", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));

    render(<ForgotPasswordPage />);

    fireEvent.change(screen.getByLabelText("Your email"), {
      target: { value: "nobody@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    await waitFor(() =>
      expect(
        screen.getByText(
          "If that email exists and a mail server is configured, a reset link was sent.",
        ),
      ).toBeInTheDocument(),
    );
    // The form is gone -- there is no separate error state to distinguish it from.
    expect(screen.queryByLabelText("Your email")).not.toBeInTheDocument();
  });

  it("disables the submit button until an email is entered", () => {
    render(<ForgotPasswordPage />);
    expect(screen.getByRole("button", { name: "Send reset link" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Your email"), {
      target: { value: "person@example.com" },
    });
    expect(screen.getByRole("button", { name: "Send reset link" })).not.toBeDisabled();
  });
});
