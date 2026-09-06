import { describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn() },
}));

import { api } from "@/lib/api";
import { useCopilotRunActivity } from "@/lib/hooks-chat";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useCopilotRunActivity", () => {
  it("does not fetch when runId is null", () => {
    renderHook(() => useCopilotRunActivity("session-1", null), { wrapper });
    expect(api.get).not.toHaveBeenCalled();
  });

  it("fetches the run under the session/run path once both ids are present", async () => {
    vi.mocked(api.get).mockResolvedValue({
      id: "run-1",
      agentId: "agent-1",
      state: "running",
      phase: null,
      output: null,
      steps: 0,
      toolCalls: [],
      taskId: null,
      question: null,
      renderedComponents: [],
    });
    const { result } = renderHook(() => useCopilotRunActivity("session-1", "run-1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.get).toHaveBeenCalledWith("/chat/sessions/session-1/runs/run-1");
  });
});
