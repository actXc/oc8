import { render, screen, within, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { ModelDTO, ModelProviderDTO, ModelWriteBody } from "@/lib/hooks";

const modelsMock = vi.fn();
const providersMock = vi.fn();
const credentialsMock = vi.fn();
const modelPricesMock = vi.fn();
const createModelMutateAsync = vi.fn();
const updateModelMutateAsync = vi.fn();

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useModels: () => modelsMock(),
    useModelProviders: () => providersMock(),
    useCredentials: () => credentialsMock(),
    useCreateModel: () => ({ mutateAsync: createModelMutateAsync, isPending: false }),
    useUpdateModel: () => ({ mutateAsync: updateModelMutateAsync, isPending: false }),
  };
});

vi.mock("@/lib/governance-hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/governance-hooks")>();
  return { ...actual, useMay: () => () => true };
});

vi.mock("@/lib/model-prices-hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/model-prices-hooks")>();
  return {
    ...actual,
    useModelPrices: () => modelPricesMock(),
    useCreateModelPrice: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useDeactivateModelPrice: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { ModelsPage } from "@/routes/models";

function model(overrides: Partial<ModelDTO> = {}): ModelDTO {
  return {
    id: "m1",
    provider: "anthropic",
    name: "Nora's model",
    status: "healthy",
    costTier: "$",
    latency: "-",
    assignedTo: [],
    note: "",
    model: "claude-sonnet-5",
    locality: "cloud",
    displayName: null,
    usedByCopilot: false,
    credentialId: null,
    healthError: null,
    healthCheckedAt: null,
    ...overrides,
  };
}

const ANTHROPIC_PROVIDERS: ModelProviderDTO[] = [
  { canonical: "anthropic", locality: "cloud", available: true },
];

const OLLAMA_PROVIDERS: ModelProviderDTO[] = [
  { canonical: "ollama", locality: "local", available: true },
];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ModelsPage />
    </QueryClientProvider>,
  );
}

describe("Model form dialog's advanced/raw parameters editor", () => {
  beforeEach(() => {
    credentialsMock.mockReset().mockReturnValue({ data: [] });
    modelPricesMock.mockReset().mockReturnValue({ data: [] });
    createModelMutateAsync.mockReset().mockResolvedValue({});
    updateModelMutateAsync.mockReset().mockResolvedValue({});
  });

  it("is present in the create-model dialog for anthropic", () => {
    providersMock.mockReturnValue({ data: ANTHROPIC_PROVIDERS });
    modelsMock.mockReturnValue({ data: [] });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /new model/i }));
    expect(screen.getByText(/Advanced\/Raw parameters/i)).toBeInTheDocument();
  });

  it("is absent for a provider its adapter never merges extra for", () => {
    providersMock.mockReturnValue({ data: OLLAMA_PROVIDERS });
    modelsMock.mockReturnValue({ data: [] });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /new model/i }));
    expect(screen.queryByText(/Advanced\/Raw parameters/i)).toBeNull();
  });

  it("prefills rows from the model's existing extra on edit", () => {
    providersMock.mockReturnValue({ data: ANTHROPIC_PROVIDERS });
    modelsMock.mockReturnValue({ data: [model({ id: "m1", extra: { top_p: 0.9 } })] });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    expect(screen.getByDisplayValue("top_p")).toBeInTheDocument();
    expect(screen.getByDisplayValue("0.9")).toBeInTheDocument();
  });

  it("sends a JSON-parsed key/value pair as extra on create", async () => {
    providersMock.mockReturnValue({ data: ANTHROPIC_PROVIDERS });
    modelsMock.mockReturnValue({ data: [] });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /new model/i }));
    fireEvent.change(screen.getByPlaceholderText(/llama3\.1:8b/i), {
      target: { value: "claude-sonnet-5" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add parameter/i }));
    fireEvent.change(screen.getByPlaceholderText("key"), { target: { value: "top_p" } });
    fireEvent.change(screen.getByPlaceholderText("value"), { target: { value: "0.9" } });
    const dialog = screen
      .getByText(/registers a model config/i)
      .closest(".max-w-lg") as HTMLElement | null;
    if (!dialog) throw new Error("dialog not found");
    fireEvent.click(within(dialog).getByRole("button", { name: /^add model$/i }));

    await waitFor(() => expect(createModelMutateAsync).toHaveBeenCalled());
    const call = createModelMutateAsync.mock.calls[0][0] as ModelWriteBody;
    expect(call.extra).toEqual({ top_p: 0.9 });
  });

  it("sends an empty object (clear) when no rows are entered", async () => {
    providersMock.mockReturnValue({ data: ANTHROPIC_PROVIDERS });
    modelsMock.mockReturnValue({ data: [model({ id: "m1", extra: { top_p: 0.9 } })] });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.click(screen.getByRole("button", { name: /remove parameter/i }));
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(updateModelMutateAsync).toHaveBeenCalled());
    const call = updateModelMutateAsync.mock.calls[0][0] as ModelWriteBody & { id: string };
    expect(call.extra).toEqual({});
  });
});
