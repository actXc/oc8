import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { requestLinkMock, revokeMock, canMock } = vi.hoisted(() => ({
  requestLinkMock: vi.fn(),
  revokeMock: vi.fn(),
  canMock: vi.fn(),
}));

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useAvailableChannels: () => ({ data: [{ id: "telegram" }] }),
    useChannelBindings: () => ({ data: [{ id: "b1", channel: "telegram", userId: "u1" }] }),
    useRequestChannelLink: () => ({ mutate: requestLinkMock, isPending: false }),
    useRevokeChannelBinding: () => ({ mutate: revokeMock, isPending: false }),
  };
});
vi.mock("@/lib/governance-hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/governance-hooks")>();
  return { ...actual, useCan: () => canMock };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: vi.fn().mockResolvedValue({ enrolled: false }) } };
});
vi.mock("@/lib/push-notifications", () => ({
  isPushSupported: () => false,
  getPushSubscriptionStatus: vi.fn().mockResolvedValue(null),
  subscribeToPush: vi.fn(),
  unsubscribeFromPush: vi.fn(),
}));

import { ProfilePage } from "@/routes/profile";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ProfilePage />
    </QueryClientProvider>,
  );
}

describe("Profile page — approval channels", () => {
  beforeEach(() => {
    requestLinkMock.mockReset();
    revokeMock.mockReset();
    canMock.mockReset();
  });

  it("renders nothing when the caller holds neither channel permission", () => {
    canMock.mockReturnValue(false);
    renderPage();
    expect(screen.queryByText(/approval channels/i)).not.toBeInTheDocument();
  });

  it("lets a channel:manage holder request a link code and copy it", async () => {
    canMock.mockImplementation((p: string) => p === "channel:manage");
    requestLinkMock.mockImplementation(
      (_channel: string, opts: { onSuccess: (r: unknown) => void }) =>
        opts.onSuccess({ channel: "telegram", code: "ABC123", expiresAt: "2026-01-01T00:00:00Z" }),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText(/approval channels/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /link telegram/i }));
    await waitFor(() => expect(screen.getByText("ABC123")).toBeInTheDocument());
  });

  it("lets a channel:view holder see bindings but not revoke them", async () => {
    canMock.mockImplementation((p: string) => p === "channel:view");
    renderPage();
    await waitFor(() =>
      expect(screen.getByText("Telegram", { selector: "span" })).toBeInTheDocument(),
    );
    expect(screen.queryByLabelText(/revoke/i)).not.toBeInTheDocument();
  });
});
