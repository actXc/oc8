// frontend/src/test/totp-enroll.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/totp", () => ({
  enrollTotp: vi.fn(async () => ({ secret: "ABCDEFGH", provisioningUri: "otpauth://totp/x" })),
  confirmTotp: vi.fn(async () => ({ backupCodes: ["CODE1", "CODE2"] })),
  renderTotpQrDataUrl: vi.fn(async () => "data:image/png;base64,fake"),
}));

import { TotpEnroll } from "@/components/totp-enroll";

function renderComponent(onDone = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TotpEnroll token="narrow-token" onDone={onDone} />
    </QueryClientProvider>,
  );
}

describe("TotpEnroll", () => {
  it("shows the QR code and manual secret after mounting", async () => {
    renderComponent();
    await waitFor(() => expect(screen.getByText("ABCDEFGH")).toBeInTheDocument());
    expect(screen.getByRole("img", { name: /qr/i })).toBeInTheDocument();
  });

  it("confirming a code reveals the backup codes exactly once, and onDone requires acknowledgment", async () => {
    const onDone = vi.fn();
    renderComponent(onDone);
    await waitFor(() => expect(screen.getByText("ABCDEFGH")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/code/i), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));
    await waitFor(() => expect(screen.getByText("CODE1")).toBeInTheDocument());
    expect(onDone).not.toHaveBeenCalled(); // not yet -- must acknowledge first
    fireEvent.click(screen.getByRole("button", { name: /saved/i }));
    expect(onDone).toHaveBeenCalled();
  });
});
