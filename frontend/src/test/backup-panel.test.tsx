import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { BackupPanel } from "@/components/backup-panel";
import type { BackupPreviewDTO, BackupRestoreResultDTO } from "@/lib/api";
import type { OrganizationSettingsDTO } from "@/lib/hooks";

const { getOrg, previewBackup, restoreBackup, exportBackup } = vi.hoisted(() => ({
  getOrg: vi.fn(),
  previewBackup: vi.fn(),
  restoreBackup: vi.fn(),
  exportBackup: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    get: (path: string) =>
      path === "/settings/organization"
        ? getOrg()
        : Promise.reject(new Error(`unexpected GET ${path}`)),
  },
  previewBackup,
  restoreBackup,
  exportBackup,
}));

const ORG: OrganizationSettingsDTO = {
  id: "org-1",
  name: "Acme GmbH",
  slug: "acme",
  tier: "pro",
  region: "eu",
};

const CLEAN_PREVIEW: BackupPreviewDTO = {
  manifest: { excluded: ["audit_log", "metering"] },
  table_counts: { agents: { current: 3, archive: 5 } },
  has_secrets: false,
  problems: [],
};

const PROBLEM_PREVIEW: BackupPreviewDTO = {
  manifest: { excluded: [] },
  table_counts: { agents: { current: 3, archive: 5 } },
  has_secrets: false,
  problems: ["schema version 7 is newer than this server supports"],
};

const RESTORE_RESULT: BackupRestoreResultDTO = {
  tables: { agents: 5 },
  secrets_restored: 0,
  excluded: ["audit_log", "metering"],
};

function renderPanel() {
  // `retry: false`: matches agent-runtime-panel.test.tsx -- without it a
  // rejected mutation/query would retry past this file's `waitFor` timeouts.
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <BackupPanel />
    </QueryClientProvider>,
  );
  return { qc, spy };
}

function selectArchiveFile() {
  fireEvent.change(screen.getByLabelText("Archive file"), {
    target: { files: [new File(["x"], "backup.tar.gz")] },
  });
}

/** jsdom does not implement `URL.createObjectURL`, so `handleExport`'s
 * download step is stubbed rather than exercised -- this file cares about
 * the "an export was fetched" state, not the browser's save-as mechanics. */
async function downloadAFreshExport() {
  exportBackup.mockResolvedValue({ blob: new Blob(["x"]), filename: "backup.tar.gz" });
  fireEvent.click(screen.getByRole("button", { name: /download backup/i }));
  await waitFor(() => expect(exportBackup).toHaveBeenCalled());
}

describe("BackupPanel", () => {
  beforeEach(() => {
    getOrg.mockReset();
    previewBackup.mockReset();
    restoreBackup.mockReset();
    exportBackup.mockReset();
    // Stubbed for the same reason as `downloadAFreshExport` above.
    URL.createObjectURL = vi.fn(() => "blob:mock");
    URL.revokeObjectURL = vi.fn();
  });

  it("keeps the restore action out of reach until a preview has actually succeeded", async () => {
    getOrg.mockResolvedValue(ORG);
    renderPanel();
    await screen.findByLabelText("Archive file");

    // No file selected yet -- the confirm button doesn't merely stay
    // disabled, it isn't rendered at all: there is nothing to inspect.
    expect(
      screen.queryByRole("button", { name: /restore company from backup/i }),
    ).not.toBeInTheDocument();

    // A file is selected but the preview hasn't resolved yet.
    let resolvePreview!: (value: BackupPreviewDTO) => void;
    previewBackup.mockReturnValue(
      new Promise((resolve) => {
        resolvePreview = resolve;
      }),
    );
    selectArchiveFile();
    await screen.findByText("Reading archive…");
    expect(
      screen.queryByRole("button", { name: /restore company from backup/i }),
    ).not.toBeInTheDocument();

    resolvePreview(CLEAN_PREVIEW);
    expect(
      await screen.findByRole("button", { name: /restore company from backup/i }),
    ).toBeInTheDocument();
  });

  it("keeps the confirm button disabled until the typed name exactly matches, rejecting a case near-miss", async () => {
    getOrg.mockResolvedValue(ORG);
    previewBackup.mockResolvedValue(CLEAN_PREVIEW);
    renderPanel();
    await downloadAFreshExport();
    selectArchiveFile();

    const button = await screen.findByRole("button", { name: /restore company from backup/i });
    const nameInput = screen.getByLabelText(/type this company's name to confirm/i);
    expect(button).toBeDisabled();

    fireEvent.change(nameInput, { target: { value: "acme gmbh" } });
    expect(button).toBeDisabled();

    fireEvent.change(nameInput, { target: { value: ORG.name } });
    expect(button).toBeEnabled();
  });

  it("rejects a trailing space the backend would not trim either", async () => {
    getOrg.mockResolvedValue(ORG);
    previewBackup.mockResolvedValue(CLEAN_PREVIEW);
    renderPanel();
    await downloadAFreshExport();
    selectArchiveFile();

    const button = await screen.findByRole("button", { name: /restore company from backup/i });
    const nameInput = screen.getByLabelText(/type this company's name to confirm/i);

    fireEvent.change(nameInput, { target: { value: `${ORG.name} ` } });
    expect(button).toBeDisabled();

    fireEvent.change(nameInput, { target: { value: ORG.name } });
    expect(button).toBeEnabled();
  });

  it("keeps the restore button disabled until a fresh export has been downloaded this session, even once the name matches, and explains why", async () => {
    getOrg.mockResolvedValue(ORG);
    previewBackup.mockResolvedValue(CLEAN_PREVIEW);
    renderPanel();
    selectArchiveFile();

    const button = await screen.findByRole("button", { name: /restore company from backup/i });
    const nameInput = screen.getByLabelText(/type this company's name to confirm/i);
    fireEvent.change(nameInput, { target: { value: ORG.name } });

    // Name matches and there are no blocking problems, but nothing has been
    // exported this session yet -- the button must stay disabled and say why.
    expect(button).toBeDisabled();
    expect(screen.getByText(/download a fresh backup above first/i)).toBeInTheDocument();

    await downloadAFreshExport();

    expect(button).toBeEnabled();
    expect(screen.queryByText(/download a fresh backup above first/i)).not.toBeInTheDocument();
  });

  it("keeps the restore action blocked when the preview reports problems, with no way to name past it", async () => {
    getOrg.mockResolvedValue(ORG);
    previewBackup.mockResolvedValue(PROBLEM_PREVIEW);
    renderPanel();
    selectArchiveFile();

    const button = await screen.findByRole("button", { name: /restore company from backup/i });
    expect(button).toBeDisabled();
    expect(await screen.findByText(PROBLEM_PREVIEW.problems[0])).toBeInTheDocument();
    // The confirm-name affordance itself does not render while problems
    // block the restore -- there's no way to even attempt naming past it.
    expect(screen.queryByLabelText(/type this company's name to confirm/i)).not.toBeInTheDocument();

    fireEvent.click(button);
    expect(restoreBackup).not.toHaveBeenCalled();
  });

  it("renders the current-vs-archive table counts the confirmation is based on", async () => {
    getOrg.mockResolvedValue(ORG);
    previewBackup.mockResolvedValue(CLEAN_PREVIEW);
    renderPanel();
    selectArchiveFile();

    await screen.findByText("Currently in this company → in the archive");
    const row = screen.getByText("agents").closest("tr")!;
    expect(within(row).getByText("3")).toBeInTheDocument();
    expect(within(row).getByText("5")).toBeInTheDocument();
  });

  it("invalidates the whole query cache and shows what was not restored after a successful restore", async () => {
    getOrg.mockResolvedValue(ORG);
    previewBackup.mockResolvedValue(CLEAN_PREVIEW);
    restoreBackup.mockResolvedValue(RESTORE_RESULT);
    const { spy } = renderPanel();
    await downloadAFreshExport();
    selectArchiveFile();

    const nameInput = await screen.findByLabelText(/type this company's name to confirm/i);
    fireEvent.change(nameInput, { target: { value: ORG.name } });
    const button = screen.getByRole("button", { name: /restore company from backup/i });
    expect(button).toBeEnabled();
    fireEvent.click(button);

    await waitFor(() =>
      expect(restoreBackup).toHaveBeenCalledWith(expect.any(File), ORG.name, undefined),
    );
    // A restore replaces essentially the whole tenant's data -- the whole
    // cache is dropped, not a handful of enumerated keys.
    await waitFor(() => expect(spy).toHaveBeenCalledWith());

    const summary = (await screen.findByText("Restore complete.")).closest("div")!.parentElement!;
    expect(within(summary).getByText(/not restored:/i)).toBeInTheDocument();
    expect(
      within(summary).getByText(RESTORE_RESULT.excluded.join(", "), { exact: false }),
    ).toBeInTheDocument();
  });

  it("shows the passphrase warning right alongside the export passphrase input", async () => {
    getOrg.mockResolvedValue(ORG);
    renderPanel();

    const input = await screen.findByLabelText("Passphrase (optional)");
    const warning = screen.getByText(
      /anyone with the file and the passphrase holds those credentials too/i,
    );
    expect(input.closest("section")).toContainElement(warning);
  });
});
