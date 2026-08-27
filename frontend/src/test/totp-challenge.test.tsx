// frontend/src/test/totp-challenge.test.tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/totp", () => ({
  verifyTotp: vi.fn(async () => ({ token: "real-session-token", principal: {}, memberId: "m1" })),
}));

import { TotpChallenge } from "@/components/totp-challenge";
import { verifyTotp } from "@/lib/totp";

describe("TotpChallenge", () => {
  it("submits the code against the challenge token and reports the resulting session", async () => {
    const onVerified = vi.fn();
    render(<TotpChallenge token="challenge-token" onVerified={onVerified} />);
    fireEvent.change(screen.getByLabelText(/code/i), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: /verify/i }));
    await waitFor(() => expect(onVerified).toHaveBeenCalledWith("real-session-token"));
    expect(verifyTotp).toHaveBeenCalledWith("challenge-token", "123456");
  });
});
