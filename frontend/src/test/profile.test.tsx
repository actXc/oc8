import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// vi.hoisted: `vi.mock` factories below are hoisted above ordinary `const`s
// by vitest, so a plain top-level `const apiGetMock = vi.fn()` referenced
// inside the factory throws "Cannot access before initialization" -- see
// the identical pattern in `hooks.test.tsx`/`credential-hooks.test.tsx`.
const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, get: apiGetMock, post: apiPostMock },
    getToken: vi.fn().mockResolvedValue("fake-token"),
  };
});
vi.mock("@/lib/push-notifications", () => ({
  isPushSupported: () => false,
  getPushSubscriptionStatus: vi.fn().mockResolvedValue(null),
  subscribeToPush: vi.fn(),
  unsubscribeFromPush: vi.fn(),
}));
// <TotpEnroll> (Task 11) talks to the three shared TOTP endpoints via
// `@/lib/totp`'s raw fetch client, not `@/lib/api` -- mocked the same way
// `totp-enroll.test.tsx` already mocks it, so mounting <TotpEnroll> here
// doesn't depend on a real backend being reachable.
vi.mock("@/lib/totp", () => ({
  enrollTotp: vi.fn(async () => ({ secret: "ABCDEFGH", provisioningUri: "otpauth://totp/x" })),
  confirmTotp: vi.fn(async () => ({ backupCodes: ["CODE1", "CODE2"] })),
  renderTotpQrDataUrl: vi.fn(async () => "data:image/png;base64,fake"),
}));
// ApprovalChannelsPanel's own hooks -- mocked the same way the panel above
// mocks TOTP/push, so this file's real target (the TOTP card) doesn't have
// to route unrelated network calls through the blanket `apiGetMock`, which
// answers every GET the same way and would otherwise resolve `useAvailableChannels`
// to a non-array value.
vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useAvailableChannels: () => ({ data: [] }),
    useChannelBindings: () => ({ data: [] }),
    useRequestChannelLink: () => ({ mutate: vi.fn(), isPending: false }),
    useRevokeChannelBinding: () => ({ mutate: vi.fn(), isPending: false }),
  };
});
vi.mock("@/lib/governance-hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/governance-hooks")>();
  return { ...actual, useCan: () => () => false };
});

import { ProfilePage } from "@/routes/profile";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ProfilePage />
    </QueryClientProvider>,
  );
}

describe("Profile page — TOTP card", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiPostMock.mockReset();
  });

  it("shows a Turn on button and starts enrollment when not enrolled", async () => {
    apiGetMock.mockResolvedValue({ enrolled: false });
    renderPage();
    await waitFor(() => expect(screen.getByText(/not enabled/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /turn on/i }));
    // TotpEnroll (Task 11) is now mounted -- assert on its own visible text
    // (e.g. a heading it renders), not implementation details.
    await waitFor(() =>
      expect(screen.getByText(/scan with your authenticator app/i)).toBeInTheDocument(),
    );
  });

  it("shows a regenerate button when already enrolled, and displays fresh codes", async () => {
    apiGetMock.mockResolvedValue({ enrolled: true });
    apiPostMock.mockResolvedValue({ backupCodes: ["AAAA-1111", "BBBB-2222"] });
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /regenerate/i })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));
    await waitFor(() => expect(screen.getByText("AAAA-1111")).toBeInTheDocument());
  });
});
