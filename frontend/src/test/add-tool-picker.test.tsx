import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { McpConnection } from "@/lib/hooks";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const mcpLoginsMock = vi.fn(() => ({ data: [] }));
const createMcpLoginMock = vi.fn();
const credentialsMock = vi.fn(() => ({ data: [] }));
const credentialTypesMock = vi.fn(() => ({ data: [] }));

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useMcpLogins: () => mcpLoginsMock(),
    useCreateMcpLogin: () => ({ mutateAsync: createMcpLoginMock, isPending: false }),
    useCredentials: () => credentialsMock(),
    useCredentialTypes: () => credentialTypesMock(),
  };
});

import { AddToolPicker } from "@/components/add-tool-picker";

function connection(overrides: Partial<McpConnection> = {}): McpConnection {
  return {
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
    ...overrides,
  };
}

function renderPicker(props: Partial<React.ComponentProps<typeof AddToolPicker>>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AddToolPicker
        addableNames={[]}
        connections={[]}
        onAdd={vi.fn()}
        onClose={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );
}

describe("AddToolPicker", () => {
  it("shows a checked recommendation checkbox for a connection with a preset", () => {
    const onAdd = vi.fn();
    renderPicker({
      addableNames: ["github"],
      connections: [
        connection({
          name: "github",
          guardrailPresets: [
            {
              key: "dev_standard",
              label: "Developer standard",
              labelTranslations: {},
              summary: "",
              summaryTranslations: {},
              recommended: true,
              read: true,
              modify: true,
              approvalActions: [],
              approvalEur: null,
              only: [],
            },
          ],
        }),
      ],
      onAdd,
    });
    const checkbox = screen.getByRole("checkbox", { name: /mit empfehlung|with recommendation/i });
    expect(checkbox).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: /hinzufügen|add/i }));
    expect(onAdd).toHaveBeenCalledWith(
      "github",
      expect.objectContaining({ read: true, modify: true }),
      undefined,
    );
  });

  it("shows no checkbox for a connection with no preset, and adds a blank policy", () => {
    const onAdd = vi.fn();
    renderPicker({
      addableNames: ["internal-wiki"],
      connections: [connection({ name: "internal-wiki", guardrailPresets: [] })],
      onAdd,
    });
    expect(screen.queryByRole("checkbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /hinzufügen|add/i }));
    expect(onAdd).toHaveBeenCalledWith("internal-wiki", null, undefined);
  });

  it("unchecking the recommendation checkbox adds a blank policy instead", () => {
    const onAdd = vi.fn();
    renderPicker({
      addableNames: ["github"],
      connections: [
        connection({
          name: "github",
          guardrailPresets: [
            {
              key: "dev_standard",
              label: "Developer standard",
              labelTranslations: {},
              summary: "",
              summaryTranslations: {},
              recommended: true,
              read: true,
              modify: true,
              approvalActions: [],
              approvalEur: null,
              only: [],
            },
          ],
        }),
      ],
      onAdd,
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /mit empfehlung|with recommendation/i }));
    fireEvent.click(screen.getByRole("button", { name: /hinzufügen|add/i }));
    expect(onAdd).toHaveBeenCalledWith("github", null, undefined);
  });

  it("a tool backed by a login requires picking a credential before Add is enabled", () => {
    const onAdd = vi.fn();
    renderPicker({
      addableNames: ["odoo"],
      connections: [connection({ name: "odoo", credentialType: "odoo_api" })],
      departmentId: null,
      onAdd,
    });
    const addButton = screen.getByRole("button", { name: /hinzufügen|add/i });
    expect(addButton).toBeDisabled();
    fireEvent.click(addButton);
    expect(onAdd).not.toHaveBeenCalled();
    // The credential picker is rendered inline, offering a way to satisfy it
    // right here instead of failing later with a 422 (the bug this covers).
    expect(screen.getByText(/needs a login|braucht einen Login/i)).toBeInTheDocument();
  });

  it("does not require a login inline when departmentId is not provided (Hire dialog usage)", () => {
    const onAdd = vi.fn();
    renderPicker({
      addableNames: ["odoo"],
      connections: [connection({ name: "odoo", credentialType: "odoo_api" })],
      onAdd,
    });
    const addButton = screen.getByRole("button", { name: /hinzufügen|add/i });
    expect(addButton).not.toBeDisabled();
    fireEvent.click(addButton);
    expect(onAdd).toHaveBeenCalledWith("odoo", null, undefined);
  });
});
