import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { AgentRuntimePanel } from "@/components/agent-runtime-panel";

const { getRuntimes, putRuntime } = vi.hoisted(() => ({
  getRuntimes: vi.fn(),
  putRuntime: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    get: (path: string) => {
      if (path === "/runtimes") return getRuntimes();
      throw new Error(`unexpected GET ${path}`);
    },
    put: putRuntime,
  },
}));

// `name` is deliberately NOT one of the two real built-in names
// ("oc8.agent-runtime" / "oc8.agent-runtime-isolated") -- these tests are
// about how the panel resolves/renders `current`, independent of the
// bilingual-copy override `runtimeDisplayCopy` applies to the two real
// defaults, which has its own tests below and in runtime-picker.test.tsx.
const DEFAULT_ENTRY = {
  id: null,
  name: "test.default-runtime",
  label: "Default",
  summary: "In-process runtime",
  capabilities: ["checkpoints"],
  isDefault: true,
  available: true,
  unavailableReason: null,
};

const NANOCLAW_ENTRY = {
  id: "nanoclaw",
  name: "nanoclaw_runtime",
  label: "nanoclaw",
  summary: "Container runtime",
  capabilities: ["skills"],
  isDefault: false,
  available: true,
  unavailableReason: null,
};

function renderPanel(props: Partial<React.ComponentProps<typeof AgentRuntimePanel>> = {}) {
  // `retry: false`: a rejected `GET /runtimes` mock must surface as
  // `isError` on the FIRST attempt for the error-state test below --
  // react-query's default retries would otherwise keep this pending well
  // past the test's timeout.
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <AgentRuntimePanel agentId="agent-1" runtimeRef={null} mayManage {...props} />
    </QueryClientProvider>,
  );
  return { qc, spy };
}

describe("AgentRuntimePanel", () => {
  it("shows the default entry's label when runtimeRef is null", async () => {
    getRuntimes.mockResolvedValue([DEFAULT_ENTRY]);
    renderPanel({ runtimeRef: null });

    expect(await screen.findByText("Default")).toBeInTheDocument();
    expect(screen.getByText("In-process runtime")).toBeInTheDocument();
  });

  // The two real built-ins now carry a real sentinel id each (no more
  // `id: null` entry) -- `current` must still resolve for the common case
  // (runtimeRef never touched) by falling back to whichever entry
  // `isDefault` marks, not by matching `id === null` against nothing.
  const BUILTIN_IN_PROCESS = {
    id: "builtin:in-process",
    name: "oc8.agent-runtime",
    label: "Standard (in-process)",
    summary: "Runs the agent in the shared OC8 process alongside the other agents on this tenant.",
    capabilities: ["checkpoints", "skills"],
    isDefault: true,
    available: true,
    unavailableReason: null,
  };
  const BUILTIN_ISOLATED = {
    id: "builtin:isolated",
    name: "oc8.agent-runtime-isolated",
    label: "Isolated (per-agent container)",
    summary:
      "Runs the agent in its own Docker container, isolated from the other agents on this tenant.",
    capabilities: ["skills"],
    isDefault: false,
    available: true,
    unavailableReason: null,
  };

  it("resolves runtimeRef=null against the isDefault entry now that neither built-in has id:null", async () => {
    getRuntimes.mockResolvedValue([BUILTIN_IN_PROCESS, BUILTIN_ISOLATED]);
    renderPanel({ runtimeRef: null });

    expect(await screen.findByText("Standard (in-process)")).toBeInTheDocument();
    expect(screen.queryByText("This runtime is no longer available.")).not.toBeInTheDocument();
  });

  it("pre-selects the implicit default's real id (not null) when opening the picker with runtimeRef unset", async () => {
    getRuntimes.mockResolvedValue([BUILTIN_IN_PROCESS, BUILTIN_ISOLATED]);
    renderPanel({ runtimeRef: null });

    const changeButton = await screen.findByRole("button", { name: /change/i });
    await waitFor(() => expect(changeButton).toBeEnabled());
    fireEvent.click(changeButton);

    const inProcessRadio = await screen.findByRole("radio", { name: /Standard \(in-process\)/ });
    expect(inProcessRadio).toBeChecked();
  });

  it("shows the matching plugin's label and capability chips when runtimeRef is set", async () => {
    getRuntimes.mockResolvedValue([DEFAULT_ENTRY, NANOCLAW_ENTRY]);
    renderPanel({ runtimeRef: "nanoclaw" });

    expect(await screen.findByText("nanoclaw")).toBeInTheDocument();
    expect(screen.getByText("Container runtime")).toBeInTheDocument();
    expect(screen.getByText("skills")).toBeInTheDocument();
  });

  it("shows a warning when runtimeRef matches nothing in the runtimes list", async () => {
    getRuntimes.mockResolvedValue([DEFAULT_ENTRY, NANOCLAW_ENTRY]);
    renderPanel({ runtimeRef: "gone-plugin-id" });

    expect(await screen.findByText("This runtime is no longer available.")).toBeInTheDocument();
    expect(screen.getByText("gone-plugin-id")).toBeInTheDocument();
    // The stale ref must not be presented as if it resolved to something real.
    expect(screen.queryByText("nanoclaw")).not.toBeInTheDocument();
  });

  it("editing the runtime calls PUT /agents/{id}/runtime and invalidates the detail query", async () => {
    getRuntimes.mockResolvedValue([DEFAULT_ENTRY, NANOCLAW_ENTRY]);
    putRuntime.mockResolvedValue({ id: "agent-1", runtimeRef: "nanoclaw" });
    const { spy } = renderPanel({ runtimeRef: null });

    const changeButton = await screen.findByRole("button", { name: /change/i });
    await waitFor(() => expect(changeButton).toBeEnabled());
    fireEvent.click(changeButton);
    fireEvent.click(await screen.findByRole("radio", { name: /nanoclaw/ }));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(putRuntime).toHaveBeenCalledWith("/agents/agent-1/runtime", {
        runtimePluginId: "nanoclaw",
      }),
    );
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ["agents", "agent-1"] }));
  });

  it("renders inline violation reasons on a 422 from the capability check", async () => {
    getRuntimes.mockResolvedValue([DEFAULT_ENTRY, NANOCLAW_ENTRY]);
    putRuntime.mockRejectedValue(
      new Error(
        JSON.stringify({
          error: "runtime_capability_violation",
          missing: [
            {
              kind: "supervision",
              missingCapability: "checkpoints",
              reason: "This agent is supervised and requires a runtime that supports checkpoints.",
            },
          ],
        }),
      ),
    );
    renderPanel({ runtimeRef: null });

    const changeButton = await screen.findByRole("button", { name: /change/i });
    await waitFor(() => expect(changeButton).toBeEnabled());
    fireEvent.click(changeButton);
    fireEvent.click(await screen.findByRole("radio", { name: /nanoclaw/ }));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(
      await screen.findByText(
        "This agent is supervised and requires a runtime that supports checkpoints.",
      ),
    ).toBeInTheDocument();
    // Not a bare error code or a raw JSON dump.
    expect(screen.queryByText(/runtime_capability_violation/)).not.toBeInTheDocument();
  });

  it("shows an honest error state, not an eternal spinner, when GET /runtimes fails", async () => {
    getRuntimes.mockRejectedValue(new Error("network error"));
    renderPanel({ runtimeRef: null });

    expect(await screen.findByText("Couldn't load runtime information.")).toBeInTheDocument();
    // Never claims to still be loading once the fetch has actually failed.
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
  });

  it("renders the built-in default's bilingual copy, not the backend's English string, for the real default names", async () => {
    getRuntimes.mockResolvedValue([
      {
        id: null,
        name: "oc8.agent-runtime-isolated",
        label: "Isolated (per-agent container)",
        summary:
          "Runs the agent in its own Docker container, isolated from the other agents on this tenant.",
        capabilities: ["skills"],
        isDefault: true,
        available: true,
        unavailableReason: null,
      },
    ]);
    renderPanel({ runtimeRef: null });

    // Same English text the backend sends here (`t()` defaults to English
    // with no LanguageProvider in scope) -- the assertion that matters is
    // that this came from the frontend's own copy table, exercised for real
    // in runtime-picker.test.tsx by feeding a backend label that would fail
    // the test if it leaked through unmodified.
    expect(await screen.findByText("Isolated (per-agent container)")).toBeInTheDocument();
  });

  it("offers a rescue action that resets to the built-in default when the current runtime is unresolved", async () => {
    getRuntimes.mockResolvedValue([DEFAULT_ENTRY]);
    putRuntime.mockResolvedValue({ id: "agent-1", runtimeRef: null });
    renderPanel({ runtimeRef: "gone-plugin-id" });

    expect(await screen.findByText("This runtime is no longer available.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /use the built-in default/i }));

    await waitFor(() =>
      expect(putRuntime).toHaveBeenCalledWith("/agents/agent-1/runtime", {
        runtimePluginId: null,
      }),
    );
  });

  it("does not offer the rescue action when the caller may not manage the agent", async () => {
    getRuntimes.mockResolvedValue([DEFAULT_ENTRY]);
    renderPanel({ runtimeRef: "gone-plugin-id", mayManage: false });

    expect(await screen.findByText("This runtime is no longer available.")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /use the built-in default/i }),
    ).not.toBeInTheDocument();
  });

  // Regression: live /browse testing found the "Change" button was only
  // gated on `mayManage`, not on the runtimes query's load state. A user
  // who clicked "Change" while GET /runtimes was still in flight (this
  // endpoint measured ~2s live) opened a picker with `runtimes ?? []`
  // still empty -- a genuinely blank modal (heading, then Cancel/Save,
  // nothing in between), which is what the original bug report described
  // as "Runtime-Change-Modal leer". The button must stay disabled until
  // the query actually resolves with data.
  it("keeps the Change button disabled while GET /runtimes is still in flight", async () => {
    let resolveRuntimes!: (value: (typeof DEFAULT_ENTRY)[]) => void;
    getRuntimes.mockReturnValue(
      new Promise((resolve) => {
        resolveRuntimes = resolve;
      }),
    );
    renderPanel({ runtimeRef: null });

    const changeButton = screen.getByRole("button", { name: /change/i });
    expect(changeButton).toBeDisabled();
    expect(screen.getByText("Loading…")).toBeInTheDocument();

    // Clicking a disabled button is a no-op, but assert directly against
    // the bug: the modal must not appear while data is still loading.
    fireEvent.click(changeButton);
    expect(screen.queryByText("Change runtime")).not.toBeInTheDocument();

    resolveRuntimes([DEFAULT_ENTRY]);
    await waitFor(() => expect(changeButton).toBeEnabled());
  });
});
