import { renderHook, waitFor } from "@testing-library/react";
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

import { useLiveConnection } from "@/lib/live/connection";

// jsdom's test environment provides a real global `WebSocket`, so a
// standalone fake class only gets picked up by `connection.ts`'s
// `new WebSocket(...)` call once it's installed via `vi.stubGlobal`. This
// fake supports exactly what `connection.ts` uses: assigning the four
// `on*` handlers and calling `.close()` -- nothing else in the WebSocket
// spec is needed for these tests.
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  close(code = 1000) {
    this.onclose?.({ code } as CloseEvent);
  }
}

describe("useLiveConnection", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("calls onClose when the socket closes", async () => {
    const onEvent = vi.fn();
    const onOpen = vi.fn();
    const onClose = vi.fn();

    const { unmount } = renderHook(() => useLiveConnection(onEvent, onOpen, onClose));

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const ws = FakeWebSocket.instances[0];

    ws.close();

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();

    // Unmount before the reconnect backoff timer this close scheduled can
    // fire and create a second socket after the test has already finished.
    unmount();
  });
});
