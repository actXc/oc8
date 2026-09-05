import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

const setToolsMutate = vi.fn();
vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useDepartmentTools: () => ({
      data: {
        tools: { github: { enabled: true, read: true, modify: true } },
        deviationCounts: { github: 3 },
        agentCount: 12,
      },
      isLoading: false,
      isError: false,
    }),
    useSetDepartmentTools: () => ({ mutate: setToolsMutate, isPending: false }),
    useMcpConnections: () => ({
      data: [
        {
          id: "c1",
          name: "github",
          transport: "stdio",
          serverUrl: "",
          command: "",
          args: [],
          departmentId: null,
          connected: true,
          scopes: [],
          health: {},
          guardrailPresets: [],
          guardrailLibrary: null,
          hasValueSpec: false,
          pluginName: null,
          credentialType: null,
        },
      ],
    }),
  };
});

import { DepartmentGuardrailsPanel } from "@/routes/departments.$id";

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DepartmentGuardrailsPanel departmentId="dept-1" />
    </QueryClientProvider>,
  );
}

describe("DepartmentGuardrailsPanel", () => {
  it("shows the deviation count for a tool", () => {
    renderPanel();
    expect(screen.getByText(/3.*12|3 von 12|3 of 12/i)).toBeInTheDocument();
  });
});
