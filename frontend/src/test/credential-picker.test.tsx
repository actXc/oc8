import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

import { CredentialPicker } from "@/components/credential-picker";

function renderPicker(props: Partial<React.ComponentProps<typeof CredentialPicker>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CredentialPicker credentialType="s3_api" value="" onChange={vi.fn()} {...props} />
    </QueryClientProvider>,
  );
}

const S3_TYPE = {
  name: "s3_api",
  displayName: "S3 / Object storage",
  fields: [
    {
      key: "access_key",
      label: "Access key",
      kind: "password",
      required: false,
      default: "",
      placeholder: "",
      help: "",
    },
  ],
};

describe("CredentialPicker", () => {
  beforeEach(() => {
    credentialsMock.mockReset();
    credentialTypesMock.mockReset();
    createCredentialMock.mockReset();
    credentialsMock.mockReturnValue({
      data: [{ id: "c1", name: "Prod S3", credentialType: "s3_api" }],
    });
    credentialTypesMock.mockReturnValue({ data: [S3_TYPE] });
  });

  it("lists existing credentials of the given type", () => {
    renderPicker();
    expect(screen.getByText("Prod S3")).toBeInTheDocument();
  });

  it("calls onChange when an existing credential is selected", () => {
    const onChange = vi.fn();
    renderPicker({ onChange });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "c1" } });
    expect(onChange).toHaveBeenCalledWith("c1");
  });

  it("creating a new credential calls onChange with its new id", async () => {
    createCredentialMock.mockResolvedValue({ id: "new-id" });
    const onChange = vi.fn();
    renderPicker({ onChange });
    fireEvent.click(screen.getByRole("button", { name: /create new/i }));
    fireEvent.change(screen.getByLabelText("Access key", { exact: false }), {
      target: { value: "AKIA_X" },
    });
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "New credential" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => {
      expect(createCredentialMock).toHaveBeenCalledWith({
        name: "New credential",
        credentialType: "s3_api",
        fieldValues: { access_key: "AKIA_X" },
      });
      expect(onChange).toHaveBeenCalledWith("new-id");
    });
  });
});
