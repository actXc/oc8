import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { DepartmentToolsPanel, toDepartmentFrame } from "@/routes/departments.$id";

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
  // `retry: false` -- matches the pattern in onboarding-guardrails-step.test.tsx;
  // without it a rejected query would retry several times before `isError`
  // ever flips true.
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("departments.$id tool guardrails", () => {
  beforeEach(() => {
    getTools.mockReset();
    putTools.mockReset();
    getConnections.mockReset();
  });

  it("renders the shared GuardrailPresetPicker for an attached connection", async () => {
    getTools.mockResolvedValue({
      tools: {
        "Odoo CRM": { enabled: true, read: true, write: false, send: false, approval_eur: null },
      },
    });
    getConnections.mockResolvedValue([
      {
        id: "conn-1",
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

    renderWithClient(<DepartmentToolsPanel departmentId="dept-1" />);

    expect(await screen.findByText(/Assist with approval/i)).toBeInTheDocument();
  });

  it("saves the selected preset's policy -- including `only`, its tool allowlist -- spread over the department's existing tools", async () => {
    // The property this whole feature hinges on: `autonomous_with_limit` is
    // safe ONLY because it withholds `delete_record`. If this screen ever
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
        "Odoo CRM": { enabled: true, read: true, write: false, send: false, approval_eur: null },
      },
    });
    getConnections.mockResolvedValue([
      {
        id: "conn-1",
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

    renderWithClient(<DepartmentToolsPanel departmentId="dept-1" />);

    fireEvent.click(await screen.findByText(/Autonomous with a limit/i));
    fireEvent.click(screen.getByRole("button", { name: /save guardrails/i }));

    await waitFor(() => expect(putTools).toHaveBeenCalled());
    const [, body] = putTools.mock.calls[0];
    // Pre-existing tool must survive the REPLACE-not-merge PUT.
    expect(body.tools).toHaveProperty("existing-connection-id");
    // Keyed by name ("Odoo CRM"), not the database id ("conn-1").
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
    expect(body.tools).not.toHaveProperty("conn-1");
  });

  it("still offers the free write/send controls for an attached connection whose plugin ships no presets", async () => {
    getTools.mockResolvedValue({
      tools: {
        "Odoo CRM": { enabled: true, read: true, write: false, send: false, approval_eur: null },
      },
    });
    getConnections.mockResolvedValue([
      { id: "conn-1", name: "Odoo CRM", guardrailPresets: [], hasValueSpec: false },
    ]);
    putTools.mockResolvedValue({ tools: {} });

    renderWithClient(<DepartmentToolsPanel departmentId="dept-1" />);

    expect(await screen.findByRole("button", { name: "Write" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
    // No preset chooser and no euro field, since this plugin declares no
    // `value_spec` and ships no presets.
    expect(screen.queryByLabelText(/€/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Write" }));
    fireEvent.click(screen.getByRole("button", { name: /save guardrails/i }));

    await waitFor(() => expect(putTools).toHaveBeenCalled());
    const [, body] = putTools.mock.calls[0];
    // Keyed by name ("Odoo CRM"), not the database id ("conn-1").
    expect(body.tools["Odoo CRM"]).toMatchObject({
      enabled: true,
      read: true,
      write: true,
      send: false,
      only: [],
    });
    expect(body.tools).not.toHaveProperty("conn-1");
  });

  it("does not offer guardrails for a connection that isn't yet enabled for this department", async () => {
    getTools.mockResolvedValue({ tools: {} });
    getConnections.mockResolvedValue([
      { id: "conn-1", name: "Odoo CRM", guardrailPresets: [], hasValueSpec: false },
    ]);

    renderWithClient(<DepartmentToolsPanel departmentId="dept-1" />);

    await screen.findByText(/no tools enabled yet/i);
    expect(screen.queryByText("Odoo CRM")).not.toBeInTheDocument();
  });

  it("recognizes a connection as attached, and shows its persisted policy, when the frame is keyed by connection NAME (as the runtime and department-template provisioning both read it), not by database id", async () => {
    // The frame the runtime actually reads is keyed by the connection's
    // human-chosen `name` (see backend/src/oc8/agent/engine.py:341 and
    // api/mcp_gateway.py:275) -- e.g. a department-template-seeded frame
    // looks like `{"odoo": {...}}`, never `{"<uuid>": {...}}`. The `id` and
    // `name` here are deliberately different strings so a lookup keyed by
    // the wrong one fails loudly instead of accidentally passing.
    getTools.mockResolvedValue({
      tools: {
        "Odoo CRM": { enabled: true, read: true, write: true, send: false, approval_eur: null },
      },
    });
    getConnections.mockResolvedValue([
      {
        id: "conn-1-uuid",
        name: "Odoo CRM",
        guardrailPresets: [],
        hasValueSpec: false,
      },
    ]);

    renderWithClient(<DepartmentToolsPanel departmentId="dept-1" />);

    // Attached: the connection must show up at all, not "No tools enabled yet".
    expect(await screen.findByText("Odoo CRM")).toBeInTheDocument();
    expect(screen.queryByText(/no tools enabled yet/i)).not.toBeInTheDocument();
    // Persisted policy resolved via name: `write: true` was saved, so the
    // "Write" toggle must render as already active, not defaulted to off.
    const writeButton = screen.getByRole("button", { name: "Write" });
    expect(writeButton.className).toContain("bg-primary");
  });

  it("saves the guardrail selection keyed by the connection's name, not its database id", async () => {
    getTools.mockResolvedValue({
      tools: {
        "Odoo CRM": { enabled: true, read: true, write: false, send: false, approval_eur: null },
      },
    });
    getConnections.mockResolvedValue([
      { id: "conn-1-uuid", name: "Odoo CRM", guardrailPresets: [], hasValueSpec: false },
    ]);
    putTools.mockResolvedValue({ tools: {} });

    renderWithClient(<DepartmentToolsPanel departmentId="dept-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Write" }));
    fireEvent.click(screen.getByRole("button", { name: /save guardrails/i }));

    await waitFor(() => expect(putTools).toHaveBeenCalled());
    const [, body] = putTools.mock.calls[0];
    expect(body.tools).toHaveProperty("Odoo CRM");
    expect(body.tools).not.toHaveProperty("conn-1-uuid");
    expect(body.tools["Odoo CRM"]).toMatchObject({ enabled: true, write: true });
  });
});

describe("guardrail preset picker identity", () => {
  it("is imported from the shared module by both the wizard step and the department tools screen", () => {
    // A fork -- a second, near-identical component -- would defeat the
    // point of this feature (one preset picker, wired in twice). Grepping
    // both sources for the exact shared import path is the real guarantee;
    // a fork would not import from here at all.
    const stepSrc = readFileSync(
      join(process.cwd(), "src/components/onboarding/guardrails-step.tsx"),
      "utf-8",
    );
    const deptSrc = readFileSync(join(process.cwd(), "src/routes/departments.$id.tsx"), "utf-8");
    expect(stepSrc).toContain('from "@/components/guardrail-preset-picker"');
    expect(deptSrc).toContain('from "@/components/guardrail-preset-picker"');
  });
});

describe("the old permissions grid must not strip what it does not model", () => {
  it("preserves only/approval_actions when it re-saves the whole frame", async () => {
    // PUT /departments/{id}/tools is a full REPLACE and the old grid re-emits
    // EVERY connection on every toggle. It models enabled/read/write/send/
    // approval_eur and nothing else, so it used to delete `only` from every
    // connection at once -- re-opening unattended delete_record on a
    // connection somebody had deliberately set to "Autonomous with a limit",
    // from one unrelated click elsewhere on the page.
    const previous = {
      "conn-a": {
        enabled: true,
        read: true,
        write: true,
        send: true,
        approval_eur: 1000,
        approval_actions: [],
        only: ["search_records", "update_record"],
      },
    };
    const policy = {
      "conn-a": {
        enabled: true,
        perms: { read: true, write: true, send: false },
        approvalEUR: 1000,
      },
    };

    const rebuilt = toDepartmentFrame(policy, previous);

    expect(rebuilt["conn-a"].only).toEqual(["search_records", "update_record"]);
    expect(rebuilt["conn-a"].approval_actions).toEqual([]);
    // and it still applies the edit it was called for
    expect(rebuilt["conn-a"].send).toBe(false);
  });
});
