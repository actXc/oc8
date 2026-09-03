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

const PROVIDERS: ModelProviderDTO[] = [
  { canonical: "anthropic", locality: "cloud", available: true },
];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ModelsPage />
    </QueryClientProvider>,
  );
}

describe("Model form dialog's effort field", () => {
  beforeEach(() => {
    credentialsMock.mockReset().mockReturnValue({ data: [] });
    providersMock.mockReset().mockReturnValue({ data: PROVIDERS });
    modelPricesMock.mockReset().mockReturnValue({ data: [] });
    createModelMutateAsync.mockReset().mockResolvedValue({});
    updateModelMutateAsync.mockReset().mockResolvedValue({});
  });

  it("is present in the create-model dialog, unlike max output tokens", () => {
    modelsMock.mockReturnValue({ data: [] });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /new model/i }));
    expect(screen.getByPlaceholderText(/e\.g\. high/i)).toBeTruthy();
  });

  it("sends the typed value as effort on create", async () => {
    modelsMock.mockReturnValue({ data: [] });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /new model/i }));
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. sales gpt/i), {
      target: { value: "Claude" },
    });
    fireEvent.change(screen.getByPlaceholderText(/llama3\.1:8b/i), {
      target: { value: "claude-sonnet-5" },
    });
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. high/i), {
      target: { value: "high" },
    });
    const dialog = screen
      .getByText(/registers a model config/i)
      .closest(".max-w-lg") as HTMLElement | null;
    if (!dialog) throw new Error("dialog not found");
    fireEvent.click(within(dialog).getByRole("button", { name: /^add model$/i }));

    await waitFor(() => expect(createModelMutateAsync).toHaveBeenCalled());
    const call = createModelMutateAsync.mock.calls[0][0] as ModelWriteBody;
    expect(call.effort).toBe("high");
  });

  it("prefills from the model's existing effort on edit", () => {
    modelsMock.mockReturnValue({ data: [model({ id: "m1", effort: "medium" })] });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    const field = screen.getByPlaceholderText(/e\.g\. high/i) as HTMLInputElement;
    expect(field.value).toBe("medium");
  });

  it("sends the typed value as effort on save", async () => {
    modelsMock.mockReturnValue({ data: [model({ id: "m1", effort: null })] });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    const field = screen.getByPlaceholderText(/e\.g\. high/i) as HTMLInputElement;
    fireEvent.change(field, { target: { value: "low" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(updateModelMutateAsync).toHaveBeenCalled());
    const call = updateModelMutateAsync.mock.calls[0][0] as ModelWriteBody & { id: string };
    expect(call.id).toBe("m1");
    expect(call.effort).toBe("low");
  });

  it("sends an empty string (clear) when the field is left blank", async () => {
    modelsMock.mockReturnValue({ data: [model({ id: "m1", effort: "high" })] });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    const field = screen.getByPlaceholderText(/e\.g\. high/i) as HTMLInputElement;
    fireEvent.change(field, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(updateModelMutateAsync).toHaveBeenCalled());
    const call = updateModelMutateAsync.mock.calls[0][0] as ModelWriteBody & { id: string };
    expect(call.effort).toBe("");
  });
});
