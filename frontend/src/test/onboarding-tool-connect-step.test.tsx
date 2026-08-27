import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";

const { getAvailable, postSetup, testConnection } = vi.hoisted(() => ({
  getAvailable: vi.fn(),
  postSetup: vi.fn(),
  testConnection: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    // `getAvailable` still resolves the bare plugin array each test builds;
    // wrapped here into the `Page[T]` envelope `/capas/available` now returns
    // (Design System Consistency plan) so every test body stays unchanged.
    get: (path: string) =>
      path.startsWith("/capas/available")
        ? getAvailable().then((items: unknown[]) => ({ items, totalCount: items.length }))
        : Promise.resolve([]),
    post: (path: string, body?: unknown) => {
      if (path.endsWith("/setup")) return postSetup(path, body);
      if (path.endsWith("/test")) return testConnection(path, body);
      return Promise.resolve({});
    },
  },
}));

// Real `@/lib/hooks` implementations are used as-is (they call the mocked
// `api` above) -- no need to stub them individually.
import { ToolConnectStep } from "@/components/onboarding/tool-connect-step";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("ToolConnectStep", () => {
  beforeEach(() => {
    getAvailable.mockReset();
    postSetup.mockReset();
    testConnection.mockReset();
  });

  it("calls onSkip when there is nothing to connect", async () => {
    getAvailable.mockResolvedValue([]);
    const onDone = vi.fn();
    const onSkip = vi.fn();

    renderWithClient(<ToolConnectStep onDone={onDone} onSkip={onSkip} />);

    await waitFor(() => expect(onSkip).toHaveBeenCalled());
    expect(onDone).not.toHaveBeenCalled();
  });

  it("connects an already-enabled plugin and surfaces the resulting connectionId via onDone", async () => {
    getAvailable.mockResolvedValue([
      {
        pluginId: "odoo-crm",
        name: "Odoo CRM",
        version: "1.0.0",
        type: "connector",
        trust: "verified",
        summary: "",
        valid: true,
        installed: true,
        installedVersion: "1.0.0",
        databaseId: "db-1",
        installationStatus: "enabled",
        permissions: [],
        capabilities: [],
        surfaces: [],
        setup: {
          title: "Connect Odoo",
          description: "",
          submit_label: "Connect",
          fields: [
            {
              key: "url",
              label: "Server URL",
              kind: "url",
              required: true,
              default: "",
              placeholder: "",
            },
          ],
          mcp: { transport: "stdio" },
        },
      },
    ]);
    postSetup.mockResolvedValue({ connectionId: "conn-1" });
    testConnection.mockResolvedValue({ connected: true, health: {} });

    const onDone = vi.fn();
    const onSkip = vi.fn();

    renderWithClient(<ToolConnectStep onDone={onDone} onSkip={onSkip} />);

    fireEvent.click(await screen.findByRole("button", { name: /odoo crm/i }));

    await screen.findByText("Connect Odoo");
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(postSetup).toHaveBeenCalledWith("/capas/db-1/setup", {
        values: { url: "https://example.com" },
      }),
    );
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("conn-1"));
    expect(onSkip).not.toHaveBeenCalled();
  });

  const singlePlugin = (overrides: Partial<Record<string, unknown>> = {}) => [
    {
      pluginId: "odoo-crm",
      name: "Odoo CRM",
      version: "1.0.0",
      type: "connector",
      trust: "verified",
      summary: "",
      valid: true,
      installed: true,
      installedVersion: "1.0.0",
      databaseId: "db-1",
      installationStatus: "enabled",
      permissions: [],
      capabilities: [],
      surfaces: [],
      setup: {
        title: "Connect Odoo",
        description: "",
        submit_label: "Connect",
        fields: [
          {
            key: "url",
            label: "Server URL",
            kind: "url",
            required: true,
            default: "",
            placeholder: "",
          },
        ],
        mcp: { transport: "stdio" },
      },
      ...overrides,
    },
  ];

  it("blocks submission and skips the network call when a required field is empty", async () => {
    getAvailable.mockResolvedValue(singlePlugin());

    const onDone = vi.fn();
    const onSkip = vi.fn();

    renderWithClient(<ToolConnectStep onDone={onDone} onSkip={onSkip} />);

    fireEvent.click(await screen.findByRole("button", { name: /odoo crm/i }));
    await screen.findByText("Connect Odoo");

    // Leave "Server URL" empty and submit directly.
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    // Give any accidental async work a tick to run, then assert it didn't.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(postSetup).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("disables the submit button while the setup request is pending", async () => {
    getAvailable.mockResolvedValue(singlePlugin());
    let resolvePost: (value: { connectionId: string | null }) => void = () => {};
    postSetup.mockReturnValue(
      new Promise((resolve) => {
        resolvePost = resolve;
      }),
    );
    testConnection.mockResolvedValue({ connected: true, health: {} });

    const onDone = vi.fn();
    const onSkip = vi.fn();

    renderWithClient(<ToolConnectStep onDone={onDone} onSkip={onSkip} />);

    fireEvent.click(await screen.findByRole("button", { name: /odoo crm/i }));
    await screen.findByText("Connect Odoo");
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://example.com" },
    });

    const submitButton = screen.getByRole("button", { name: "Connect" });
    fireEvent.click(submitButton);

    await waitFor(() => expect(submitButton).toBeDisabled());

    resolvePost({ connectionId: "conn-2" });
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("conn-2"));
  });

  it("calls onDone with null for a non-MCP setup plugin instead of dead-ending", async () => {
    getAvailable.mockResolvedValue(
      singlePlugin({
        setup: {
          title: "Connect Odoo",
          description: "",
          submit_label: "Connect",
          fields: [
            {
              key: "url",
              label: "Server URL",
              kind: "url",
              required: true,
              default: "",
              placeholder: "",
            },
          ],
          // No `mcp` field: this plugin's setup produces no MCP connection.
        },
      }),
    );
    postSetup.mockResolvedValue({ connectionId: null });

    const onDone = vi.fn();
    const onSkip = vi.fn();

    renderWithClient(<ToolConnectStep onDone={onDone} onSkip={onSkip} />);

    fireEvent.click(await screen.findByRole("button", { name: /odoo crm/i }));
    await screen.findByText("Connect Odoo");
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() => expect(postSetup).toHaveBeenCalled());
    expect(testConnection).not.toHaveBeenCalled();
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(null));
  });
});
