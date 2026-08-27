import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";

// `@/lib/api` is mocked rather than `@/lib/hooks` (the convention in
// credential-picker.test.tsx) on purpose: the two device-login hooks are new
// in this slice and worth exercising for real, including the exact request
// body the stateless poll endpoint depends on.
const { getCredentials, startLogin, pollLogin } = vi.hoisted(() => ({
  getCredentials: vi.fn(),
  startLogin: vi.fn(),
  pollLogin: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    get: (path: string) =>
      path.startsWith("/credentials") ? getCredentials() : Promise.reject(new Error(path)),
    post: (path: string, body?: unknown) => {
      if (path === "/models/chatgpt-subscription/device/start") return startLogin();
      if (path === "/models/chatgpt-subscription/device/poll") return pollLogin(body);
      return Promise.reject(new Error(`unexpected POST ${path}`));
    },
  },
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { ChatGptSubscriptionPicker } from "@/components/chatgpt-subscription-picker";

function renderPicker(onChange = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    onChange,
    ...render(
      <QueryClientProvider client={qc}>
        <ChatGptSubscriptionPicker value="" onChange={onChange} />
      </QueryClientProvider>,
    ),
  };
}

const START_RESPONSE = {
  deviceAuthId: "da1",
  userCode: "ABCD-1234",
  verificationUri: "https://auth.openai.com/codex/device",
  expiresIn: 900,
  interval: 1,
};

describe("ChatGptSubscriptionPicker", () => {
  beforeEach(() => {
    getCredentials.mockReset().mockResolvedValue([]);
    startLogin.mockReset();
    pollLogin.mockReset();
  });

  it("shows the user code and a fixed verification link (not pre-filled) after starting a login", async () => {
    startLogin.mockResolvedValue(START_RESPONSE);
    pollLogin.mockResolvedValue({ status: "pending", credentialId: null, error: null });
    renderPicker();

    fireEvent.click(await screen.findByRole("button", { name: /sign in with chatgpt/i }));

    expect(await screen.findByText("ABCD-1234")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /auth\.openai\.com\/codex\/device/i });
    expect(link).toHaveAttribute("href", "https://auth.openai.com/codex/device");
    // The link must NOT carry the code -- there is no pre-filled variant on
    // this flow, and pretending otherwise would leave the user stuck on a
    // page asking for a code they were never told to type.
    expect(link.getAttribute("href")).not.toContain("ABCD-1234");
  });

  it("spells out the two manual steps: open the link, then type the code", async () => {
    startLogin.mockResolvedValue(START_RESPONSE);
    pollLogin.mockResolvedValue({ status: "pending", credentialId: null, error: null });
    renderPicker();

    fireEvent.click(await screen.findByRole("button", { name: /sign in with chatgpt/i }));
    await screen.findByText("ABCD-1234");

    // Two numbered steps, not one do-everything link.
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getAllByText(/open/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/enter this code/i)).toBeInTheDocument();
    expect(screen.getByText(/doesn't fill the code in for you/i)).toBeInTheDocument();
    expect(screen.getByText(/waiting for/i)).toBeInTheDocument();
  });

  it("calls onChange with the new credential id once polling reports complete", async () => {
    const onChange = vi.fn();
    startLogin.mockResolvedValue(START_RESPONSE);
    pollLogin.mockResolvedValue({ status: "complete", credentialId: "cred-99", error: null });
    const { onChange: cb } = renderPicker(onChange);

    fireEvent.click(await screen.findByRole("button", { name: /sign in with chatgpt/i }));
    await screen.findByText("ABCD-1234");

    await waitFor(() => expect(cb).toHaveBeenCalledWith("cred-99"), { timeout: 3000 });
    expect(pollLogin).toHaveBeenCalledWith(
      expect.objectContaining({ deviceAuthId: "da1", userCode: "ABCD-1234" }),
    );
    // The deadline the stateless poll endpoint cannot compute for itself.
    const body = pollLogin.mock.calls[0][0] as { expiresAt: string };
    expect(Date.parse(body.expiresAt)).toBeGreaterThan(Date.now());
  });

  it("shows an error and lets the user retry when the device code expires", async () => {
    startLogin.mockResolvedValue(START_RESPONSE);
    pollLogin.mockResolvedValue({ status: "expired", credentialId: null, error: null });
    renderPicker();

    fireEvent.click(await screen.findByRole("button", { name: /sign in with chatgpt/i }));
    await screen.findByText("ABCD-1234");

    expect(await screen.findByText(/expired/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in with chatgpt/i })).toBeInTheDocument();
  });

  it("restarts cleanly after a failure -- new attempt, new code, no stuck state", async () => {
    startLogin
      .mockResolvedValueOnce(START_RESPONSE)
      .mockResolvedValueOnce({ ...START_RESPONSE, deviceAuthId: "da2", userCode: "WXYZ-9999" });
    pollLogin
      .mockResolvedValueOnce({ status: "error", credentialId: null, error: "access_denied" })
      .mockResolvedValue({ status: "pending", credentialId: null, error: null });
    renderPicker();

    fireEvent.click(await screen.findByRole("button", { name: /sign in with chatgpt/i }));
    await screen.findByText("ABCD-1234");
    await screen.findByText(/sign-in failed/i);

    fireEvent.click(screen.getByRole("button", { name: /sign in with chatgpt/i }));

    expect(await screen.findByText("WXYZ-9999")).toBeInTheDocument();
    expect(screen.queryByText("ABCD-1234")).not.toBeInTheDocument();
    expect(screen.queryByText(/sign-in failed/i)).not.toBeInTheDocument();
    await waitFor(() => expect(startLogin).toHaveBeenCalledTimes(2));
  });

  it("surfaces a failure to even start the login", async () => {
    startLogin.mockRejectedValue(new Error("device sign-in is disabled for this account"));
    renderPicker();

    fireEvent.click(await screen.findByRole("button", { name: /sign in with chatgpt/i }));

    expect(await screen.findByText(/device sign-in is disabled/i)).toBeInTheDocument();
    expect(pollLogin).not.toHaveBeenCalled();
  });

  it("stops polling on unmount instead of leaking a timer", async () => {
    startLogin.mockResolvedValue(START_RESPONSE);
    pollLogin.mockResolvedValue({ status: "pending", credentialId: null, error: null });
    const { unmount } = renderPicker();

    fireEvent.click(await screen.findByRole("button", { name: /sign in with chatgpt/i }));
    await screen.findByText("ABCD-1234");
    unmount();

    const callsAtUnmount = pollLogin.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 2200));
    expect(pollLogin.mock.calls.length).toBe(callsAtUnmount);
  });

  it("lists already-connected ChatGPT accounts of the right credential type", async () => {
    getCredentials.mockResolvedValue([{ id: "c1", name: "user@example.com" }]);
    renderPicker();

    expect(await screen.findByText("user@example.com")).toBeInTheDocument();
  });

  it("says the connection is a personal subscription with manual-only runs", async () => {
    renderPicker();
    expect(await screen.findByText(/manual/i)).toBeInTheDocument();
  });
});
