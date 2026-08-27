import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/hooks", () => ({
  useAgentTriggers: () => ({ data: [] }),
  useAutomationEventCatalogue: () => ({
    data: [
      {
        source: "calendar",
        type: "event.created",
        label: "New calendar event",
        description: "A calendar event was created.",
      },
    ],
  }),
  useCreateAgentTrigger: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateAgentAutomation: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { MissionAutomation } from "@/components/mission-automation";

describe("MissionAutomation", () => {
  it("uses plain-language mission and start-condition fields", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MissionAutomation
          agentId="agent-1"
          agentName="Ada"
          initialMission="Prepare weekly reports"
        />
      </QueryClientProvider>,
    );

    expect(screen.getByRole("textbox", { name: "Mission" })).toHaveValue("Prepare weekly reports");
    expect(screen.getByRole("combobox", { name: "Start condition" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Mission" }), {
      target: { value: "Keep the team informed" },
    });
    expect(screen.getByRole("textbox", { name: "Mission" })).toHaveValue("Keep the team informed");
    expect(screen.getByText("Autonomy & escalation")).toBeInTheDocument();
  });
});
