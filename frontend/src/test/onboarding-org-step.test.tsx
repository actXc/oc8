import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { OrgStep } from "@/components/onboarding/org-step";

const { getOrg, putOrg } = vi.hoisted(() => ({
  getOrg: vi.fn(),
  putOrg: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { get: getOrg, put: putOrg },
}));

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("OrgStep", () => {
  it("saves the entered name and region and calls onDone", async () => {
    getOrg.mockResolvedValue({
      id: "org-1",
      name: "OC8 Community",
      slug: "oc8",
      tier: "community",
      region: "eu",
    });
    putOrg.mockResolvedValue({
      id: "org-1",
      name: "Acme",
      slug: "oc8",
      tier: "community",
      region: "us",
    });
    const onDone = vi.fn();

    renderWithClient(<OrgStep onDone={onDone} />);

    const nameInput = await screen.findByLabelText(/company|workspace/i);
    fireEvent.change(nameInput, { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText(/region/i), { target: { value: "us" } });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() =>
      expect(putOrg).toHaveBeenCalledWith("/settings/organization", { name: "Acme", region: "us" }),
    );
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });
});
