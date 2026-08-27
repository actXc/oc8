import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { GuardrailsStep } from "@/components/onboarding/guardrails-step";

const { getTools, putTools, getConnections } = vi.hoisted(() => ({
  getTools: vi.fn(),
  putTools: vi.fn(),
  getConnections: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    get: (path: string) => (path.endsWith("/tools") ? getTools() : getConnections()),
    put: putTools,
  },
}));

function renderWithClient(ui: React.ReactElement) {
  // `retry: false` -- without it, a rejected `getTools()` in the error-path
  // test below would go through react-query's default 3 retries with
  // exponential backoff before `isError` ever flips true, making that test
  // slow and flaky under `waitFor`'s default timeout. Matches the pattern
  // already established in `src/lib/governance-hooks.test.ts`.
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("GuardrailsStep", () => {
  beforeEach(() => {
    getTools.mockReset();
    putTools.mockReset();
    getConnections.mockReset();
  });

  it("merges into the existing frame instead of replacing it", async () => {
    // A pre-existing, unrelated tool already granted to this department --
    // this must still be present in the PUT body after saving guardrails
    // for the newly connected one.
    getTools.mockResolvedValue({
      tools: {
        "existing-connection-id": {
          enabled: true,
          read: true,
          write: true,
          send: false,
          approval_eur: null,
        },
      },
    });
    getConnections.mockResolvedValue([{ id: "new-connection-id", name: "Odoo CRM" }]);
    putTools.mockResolvedValue({ tools: {} });
    const onDone = vi.fn();

    renderWithClient(
      <GuardrailsStep departmentId="dept-1" connectionId="new-connection-id" onDone={onDone} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /continue|save/i }));

    await waitFor(() => expect(putTools).toHaveBeenCalled());
    const [, body] = putTools.mock.calls[0];
    expect(body.tools).toHaveProperty("existing-connection-id");
    // Keyed by the connection's name ("Odoo CRM"), the key the runtime
    // actually reads -- not its database id ("new-connection-id").
    expect(body.tools).toHaveProperty("Odoo CRM");
    expect(body.tools).not.toHaveProperty("new-connection-id");
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it("renders the shared preset picker for a connection whose plugin ships presets", async () => {
    getTools.mockResolvedValue({ tools: {} });
    getConnections.mockResolvedValue([
      {
        id: "new-connection-id",
        name: "Odoo CRM",
        guardrailPresets: [
          {
            key: "assist_with_approval",
            label: "Unterstützen",
            labelEn: "Assist with approval",
            summary: "s",
            summaryEn: "Every send needs approval.",
            recommended: true,
            read: true,
            write: true,
            send: true,
            approvalActions: ["send"],
            approvalEur: null,
            only: [],
          },
        ],
        hasValueSpec: true,
      },
    ]);

    renderWithClient(
      <GuardrailsStep departmentId="dept-1" connectionId="new-connection-id" onDone={vi.fn()} />,
    );

    expect(await screen.findByText(/Assist with approval/i)).toBeInTheDocument();
  });

  it("saves the selected preset's policy -- including `only`, its tool allowlist -- spread over the existing tools", async () => {
    // The property this whole feature hinges on: `autonomous_with_limit` is
    // safe ONLY because it withholds `delete_record`. If this step ever
    // wrote read/write/send/approval into the department frame but dropped
    // `only`, that preset would silently permit unattended deletion under a
    // name promising a limit.
    getTools.mockResolvedValue({
      tools: {
        "existing-connection-id": {
          enabled: true,
          read: true,
          write: true,
          send: false,
          approval_eur: null,
        },
      },
    });
    getConnections.mockResolvedValue([
      {
        id: "new-connection-id",
        name: "Odoo CRM",
        guardrailPresets: [
          {
            key: "autonomous_with_limit",
            label: "Autonom mit Limit",
            labelEn: "Autonomous with a limit",
            summary: "s",
            summaryEn: "Acts on its own below the threshold.",
            recommended: false,
            read: true,
            write: true,
            send: true,
            approvalActions: [],
            approvalEur: 1000,
            only: ["search_records", "update_record"],
          },
        ],
        hasValueSpec: true,
      },
    ]);
    putTools.mockResolvedValue({ tools: {} });
    const onDone = vi.fn();

    renderWithClient(
      <GuardrailsStep departmentId="dept-1" connectionId="new-connection-id" onDone={onDone} />,
    );

    fireEvent.click(await screen.findByText(/Autonomous with a limit/i));
    fireEvent.click(screen.getByRole("button", { name: /continue|save/i }));

    await waitFor(() => expect(putTools).toHaveBeenCalled());
    const [, body] = putTools.mock.calls[0];
    // Pre-existing tool must survive the REPLACE-not-merge PUT.
    expect(body.tools).toHaveProperty("existing-connection-id");
    // Keyed by name ("Odoo CRM"), not the database id ("new-connection-id").
    expect(body.tools["Odoo CRM"]).toMatchObject({
      enabled: true,
      read: true,
      write: true,
      send: true,
      approval_eur: 1000,
      approval_actions: [],
      only: ["search_records", "update_record"],
    });
    expect(body.tools["Odoo CRM"].only).not.toContain("delete_record");
    expect(body.tools).not.toHaveProperty("new-connection-id");
  });

  it("still offers the free write/send/approval controls for a connection whose plugin ships no presets", async () => {
    getTools.mockResolvedValue({ tools: {} });
    getConnections.mockResolvedValue([
      { id: "new-connection-id", name: "Odoo CRM", guardrailPresets: [], hasValueSpec: false },
    ]);
    putTools.mockResolvedValue({ tools: {} });
    const onDone = vi.fn();

    renderWithClient(
      <GuardrailsStep departmentId="dept-1" connectionId="new-connection-id" onDone={onDone} />,
    );

    expect(await screen.findByRole("button", { name: "Write" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
    // No preset chooser and no euro field, since this plugin declares no
    // `value_spec` and ships no presets.
    expect(screen.queryByLabelText(/€/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Write" }));
    fireEvent.click(screen.getByRole("button", { name: /continue|save/i }));

    await waitFor(() => expect(putTools).toHaveBeenCalled());
    const [, body] = putTools.mock.calls[0];
    // Keyed by name ("Odoo CRM"), not the database id ("new-connection-id").
    expect(body.tools["Odoo CRM"]).toMatchObject({
      enabled: true,
      read: true,
      write: true,
      send: false,
      only: [],
    });
    expect(body.tools).not.toHaveProperty("new-connection-id");
  });

  it("saves the guardrail selection keyed by the connection's name, not its database id", async () => {
    // The frame the runtime actually reads is keyed by the connection's
    // `name` (see backend/src/oc8/agent/engine.py:341 and
    // api/mcp_gateway.py:275), never by the freshly-created connection's
    // database-row UUID that `connectionId` carries.
    getTools.mockResolvedValue({ tools: {} });
    getConnections.mockResolvedValue([
      { id: "new-connection-id", name: "Odoo CRM", guardrailPresets: [], hasValueSpec: false },
    ]);
    putTools.mockResolvedValue({ tools: {} });
    const onDone = vi.fn();

    renderWithClient(
      <GuardrailsStep departmentId="dept-1" connectionId="new-connection-id" onDone={onDone} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /continue|save/i }));

    await waitFor(() => expect(putTools).toHaveBeenCalled());
    const [, body] = putTools.mock.calls[0];
    expect(body.tools).toHaveProperty("Odoo CRM");
    expect(body.tools).not.toHaveProperty("new-connection-id");
  });

  it("never exposes a clickable Continue button when the connection cannot be resolved from the connections list", async () => {
    // `connectionId` is a UUID that must resolve to a real connection (via
    // `connections.find`) so `save` can key the frame write by its `name`.
    // If connections haven't loaded yet, or the id matches nothing, `save`
    // must never run -- writing the UUID back (the original bug) or an
    // empty-string key would both be worse than refusing to save.
    getTools.mockResolvedValue({ tools: {} });
    getConnections.mockResolvedValue([{ id: "some-other-connection-id", name: "Odoo CRM" }]);
    const onDone = vi.fn();

    renderWithClient(
      <GuardrailsStep departmentId="dept-1" connectionId="unresolvable-id" onDone={onDone} />,
    );

    // Wait for the guard's own message, so the assertion below runs against
    // the settled (post-load) render rather than possibly the transient
    // loading state, where "no Continue button" would be trivially true.
    await screen.findByText(/could not resolve this connection/i);
    expect(screen.queryByRole("button", { name: /continue|save/i })).not.toBeInTheDocument();
    expect(putTools).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("never exposes a clickable Continue button when the existing-tools fetch fails", async () => {
    // If the GET fails, `existing` stays `undefined` forever (react-query's
    // `isLoading` flips to `false` once retries are exhausted, same as a
    // successful load). A Continue button rendered in that state would call
    // `save()` with `existing?.tools ?? {}` -- i.e. `{}` -- and PUT a body
    // containing only the newly connected tool, silently wiping every other
    // tool already granted to this department. So no such button may ever
    // appear while the fetch is in its error state.
    getTools.mockRejectedValue(new Error("network down"));
    getConnections.mockResolvedValue([{ id: "new-connection-id", name: "Odoo CRM" }]);
    const onDone = vi.fn();

    renderWithClient(
      <GuardrailsStep departmentId="dept-1" connectionId="new-connection-id" onDone={onDone} />,
    );

    await screen.findByText(/could not load/i);
    expect(screen.getByText("network down")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /continue|save/i })).not.toBeInTheDocument();
    expect(putTools).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });
});
