import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { AgentModelStep } from "@/components/onboarding/agent-model-step";
import { AVATAR_COLORS } from "@/components/agent-identity-fields";

const { getModels, getProviders, getRuntimes, postAgent } = vi.hoisted(() => ({
  getModels: vi.fn(),
  getProviders: vi.fn(),
  getRuntimes: vi.fn(),
  postAgent: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    get: (path: string) => {
      if (path === "/models") return getModels();
      if (path === "/runtimes") return getRuntimes();
      return getProviders();
    },
    post: postAgent,
  },
}));

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("AgentModelStep", () => {
  it("creates the agent with the selected model and reports it via onDone", async () => {
    getModels.mockResolvedValue([
      {
        id: "m1",
        provider: "anthropic",
        name: "Claude",
        status: "healthy",
        costTier: "$$",
        latency: "fast",
        assignedTo: [],
        note: "",
        model: "claude",
        locality: "cloud",
        displayName: null,
        usedByCopilot: true,
      },
    ]);
    getProviders.mockResolvedValue([]);
    getRuntimes.mockResolvedValue([
      {
        id: null,
        name: "oc8.agent-runtime",
        label: "Default",
        summary: "In-process",
        capabilities: [],
        isDefault: true,
        available: true,
        unavailableReason: null,
      },
    ]);
    postAgent.mockResolvedValue({ id: "agent-1", departmentId: "dept-1" });
    const onDone = vi.fn();

    renderWithClient(
      <AgentModelStep
        identity={{
          name: "Astra",
          role: "Support",
          description: "",
          departmentId: "dept-1",
          avatarIdx: 0,
        }}
        onDone={onDone}
      />,
    );

    fireEvent.click(await screen.findByRole("radio", { name: /Claude/ }));
    fireEvent.click(screen.getByRole("button", { name: /create agent/i }));

    await waitFor(() =>
      expect(postAgent).toHaveBeenCalledWith("/agents", {
        name: "Astra",
        departmentId: "dept-1",
        roleTitle: "Support",
        mission: "",
        modelConfigId: "m1",
        runtimePluginId: null,
        presentation: {
          provider: "anthropic",
          llm: "Claude",
          tools: [],
          guardrails: [],
          schedule: "on-demand",
          avatar_color: AVATAR_COLORS[0],
        },
      }),
    );
    await waitFor(() =>
      expect(onDone).toHaveBeenCalledWith({ id: "agent-1", departmentId: "dept-1" }),
    );
  });

  it("includes the selected runtimePluginId in the create-agent payload", async () => {
    getModels.mockResolvedValue([
      {
        id: "m1",
        provider: "anthropic",
        name: "Claude",
        status: "healthy",
        costTier: "$$",
        latency: "fast",
        assignedTo: [],
        note: "",
        model: "claude",
        locality: "cloud",
        displayName: null,
        usedByCopilot: true,
      },
    ]);
    getProviders.mockResolvedValue([]);
    getRuntimes.mockResolvedValue([
      {
        id: null,
        name: "oc8.agent-runtime",
        label: "Default",
        summary: "In-process",
        capabilities: [],
        isDefault: true,
        available: true,
        unavailableReason: null,
      },
      {
        id: "nanoclaw",
        name: "nanoclaw_runtime",
        label: "nanoclaw",
        summary: "Container runtime",
        capabilities: ["skills"],
        isDefault: false,
        available: true,
        unavailableReason: null,
      },
    ]);
    postAgent.mockResolvedValue({ id: "agent-2", departmentId: "dept-1" });
    const onDone = vi.fn();

    renderWithClient(
      <AgentModelStep
        identity={{
          name: "Astra",
          role: "Support",
          description: "",
          departmentId: "dept-1",
          avatarIdx: 0,
        }}
        onDone={onDone}
      />,
    );

    fireEvent.click(await screen.findByRole("radio", { name: /Claude/ }));
    fireEvent.click(screen.getByRole("radio", { name: /nanoclaw/ }));
    fireEvent.click(screen.getByRole("button", { name: /create agent/i }));

    await waitFor(() =>
      expect(postAgent).toHaveBeenCalledWith(
        "/agents",
        expect.objectContaining({ runtimePluginId: "nanoclaw" }),
      ),
    );
  });
});
