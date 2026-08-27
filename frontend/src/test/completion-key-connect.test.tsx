import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const credentialsMock = vi.fn();
const credentialTypesMock = vi.fn();
const createCredentialMock = vi.fn();

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useCredentials: (type?: string) => credentialsMock(type),
    useCredentialTypes: () => credentialTypesMock(),
    useCreateCredential: () => ({ mutateAsync: createCredentialMock, isPending: false }),
  };
});

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// `CompletionKeyPicker` is not exported (only `ProviderCard` uses it), so
// these tests exercise it through `ProviderCard` itself -- the real render
// path, not a re-exported internal.
import { ProviderCard } from "@/routes/models";

const ANTHROPIC_TYPE = {
  name: "anthropic_api_key",
  displayName: "Anthropic",
  fields: [
    {
      key: "api_key",
      label: "API key",
      kind: "password",
      required: true,
      default: "",
      placeholder: "",
      help: "",
    },
  ],
};

function group(overrides: Partial<{ available: boolean }> = {}) {
  return {
    canonical: "anthropic",
    locality: "cloud",
    available: overrides.available ?? false,
    configs: [],
  };
}

function renderCard(available: boolean) {
  const qc = new QueryClient();
  return {
    qc,
    ...render(
      <QueryClientProvider client={qc}>
        <ProviderCard group={group({ available })} mayManage={true} />
      </QueryClientProvider>,
    ),
  };
}

describe("ProviderCard's completion-key picker", () => {
  beforeEach(() => {
    credentialsMock.mockReset();
    credentialTypesMock.mockReset();
    createCredentialMock.mockReset();
    credentialsMock.mockReturnValue({ data: [] });
    credentialTypesMock.mockReturnValue({ data: [ANTHROPIC_TYPE] });
  });

  it("renders the credential picker directly, not collapsed, when the key is missing", () => {
    renderCard(false);
    expect(
      screen.getByText(/needs a completion key before its agents can run/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /change key/i })).not.toBeInTheDocument();
  });

  it("shows a collapsed 'Change key' link, not the picker, when the key is already set", () => {
    credentialsMock.mockReturnValue({ data: [{ id: "c1", name: "Prod Anthropic" }] });
    renderCard(true);
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /change key/i })).toBeInTheDocument();
  });

  it("clicking 'Change key' expands the picker, pre-selecting the bound credential", () => {
    credentialsMock.mockReturnValue({ data: [{ id: "c1", name: "Prod Anthropic" }] });
    renderCard(true);
    fireEvent.click(screen.getByRole("button", { name: /change key/i }));
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select).toBeInTheDocument();
    expect(select.value).toBe("c1");
  });

  it("passes the anthropic_api_key credential type through to the picker", () => {
    renderCard(false);
    expect(credentialsMock).toHaveBeenCalledWith("anthropic_api_key");
  });

  it("creating a new credential invalidates model-providers so the pill can flip", async () => {
    createCredentialMock.mockResolvedValue({ id: "new-id" });
    const { qc } = renderCard(false);
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");

    fireEvent.click(screen.getByRole("button", { name: /create new/i }));
    fireEvent.change(screen.getByLabelText("API key", { exact: false }), {
      target: { value: "sk-test-value" },
    });
    fireEvent.change(screen.getByLabelText(/^name$/i), {
      target: { value: "Prod Anthropic" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(createCredentialMock).toHaveBeenCalledWith({
        name: "Prod Anthropic",
        credentialType: "anthropic_api_key",
        fieldValues: { api_key: "sk-test-value" },
      }),
    );
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["model-providers"] }),
    );
  });

  it("selecting an existing credential from the dropdown also invalidates model-providers", () => {
    credentialsMock.mockReturnValue({
      data: [
        { id: "c1", name: "Prod Anthropic" },
        { id: "c2", name: "Staging Anthropic" },
      ],
    });
    const { qc } = renderCard(false);
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "c2" } });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["model-providers"] });
  });

  // ProviderCard renders from useModels() before useModelProviders() resolves,
  // and defaults `available` to false until it does. Found live on the Models
  // page for the old useCreateSecret-based mechanism: anthropic showed the
  // "key set" pill AND the expanded connect form, because a
  // useState(!available) initializer latched that first false.
  it("collapses to the 'Change key' link when `available` only becomes true on a later render", () => {
    credentialsMock.mockReturnValue({ data: [{ id: "c1", name: "Prod Anthropic" }] });
    const qc = new QueryClient();
    const { rerender } = render(
      <QueryClientProvider client={qc}>
        <ProviderCard group={group({ available: false })} mayManage={true} />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("combobox")).toBeInTheDocument();

    rerender(
      <QueryClientProvider client={qc}>
        <ProviderCard group={group({ available: true })} mayManage={true} />
      </QueryClientProvider>,
    );

    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /change key/i })).toBeInTheDocument();
  });
});
