import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vi.hoisted: `vi.mock` factories are hoisted above ordinary top-level
// `const`s by vitest -- see the identical pattern/comment in
// `src/test/profile.test.tsx`.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getToken: vi.fn().mockResolvedValue("fake-token"),
  };
});

import { LiveUpdatesProvider, useLiveConnectionStatus } from "@/lib/live/provider";

// Same minimal fake as `connection.test.ts` -- see its comment for why
// `vi.stubGlobal` is required for `connection.ts`'s `new WebSocket(...)` to
// pick it up. This one also supports `.open()` since this file drives the
// socket through open -> close -> open.
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  open() {
    this.onopen?.();
  }

  close(code = 1000) {
    this.onclose?.({ code } as CloseEvent);
  }
}

function StatusProbe() {
  const status = useLiveConnectionStatus();
  return <div data-testid="status">{status}</div>;
}

function renderProvider() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LiveUpdatesProvider>
        <StatusProbe />
      </LiveUpdatesProvider>
    </QueryClientProvider>,
  );
}

describe("useLiveConnectionStatus", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reports disconnected after the socket closes and connected again after it reopens", async () => {
    const { unmount } = renderProvider();

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const ws = FakeWebSocket.instances[0];

    act(() => ws.open());
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("connected"));

    act(() => ws.close());
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("disconnected"));

    act(() => ws.open());
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("connected"));

    // Unmount before the reconnect backoff timer this close scheduled can
    // fire and create another socket after the test has already finished.
    unmount();
  });
});
