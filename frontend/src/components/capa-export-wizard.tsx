import { useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { StepDots } from "@/components/onboarding/step-dots";
import { downloadCapaExport, type CapaExportItemInput } from "@/lib/api";
import { useAgents, useDepartments, usePreviewCapaExport, useSkills } from "@/lib/hooks";
import { useT } from "@/lib/i18n";

type WizardStep = "selection" | "details" | "preview" | "export";

const STEP_ORDER: WizardStep[] = ["selection", "details", "preview", "export"];
const STEP_LABEL: Record<WizardStep, [string, string]> = {
  selection: ["Selection", "Auswahl"],
  details: ["Details", "Details"],
  preview: ["Preview", "Vorschau"],
  export: ["Export", "Export"],
};

type Selection = {
  departmentIds: Set<string>;
  agentIds: Set<string>;
  skillIds: Set<string>;
};

type ItemDetails = { name: string; version: string; summary: string };
type DetailsByKey = Record<string, ItemDetails>;

/** A capa name must match `_NAME_RE` in `backend/src/oc8/capas/export.py`
 * (lowercase, starts with a letter, only letters/digits/underscore). This is
 * only a friendly default for the Details step's input -- the backend is the
 * one enforcing the rule, and a bad name surfaces there as a Vorschau error. */
function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^([0-9])/, "a$1");
}

function detailKey(kind: CapaExportItemInput["kind"], id: string): string {
  return `${kind}:${id}`;
}

export function CapaExportWizard({ onClose }: { onClose: () => void }) {
  const t = useT();
  const [step, setStep] = useState<WizardStep>("selection");

  // The Selection step renders every row as a checkbox, not a paginated list
  // view -- so each list hook is called with an explicit large pageSize
  // rather than the default (20) `ListQueryParams` would otherwise use.
  const { data: departmentsPage } = useDepartments({ pageSize: 500 });
  const { data: agentsPage } = useAgents({ pageSize: 500 });
  const { data: skillsPage } = useSkills({ pageSize: 500 });
  const departments = departmentsPage?.items ?? [];
  const agents = agentsPage?.items ?? [];
  // Only locally authored skills are exportable (build_skill_export rejects
  // origin="store" -- capas/export.py) -- store-origin skills are simply
  // absent from this picker, not shown disabled.
  const localSkills = (skillsPage?.items ?? []).filter((s) => s.origin === "local");

  const [selection, setSelection] = useState<Selection>({
    departmentIds: new Set(),
    agentIds: new Set(),
    skillIds: new Set(),
  });
  const [details, setDetails] = useState<DetailsByKey>({});
  const preview = usePreviewCapaExport();
  const [exporting, setExporting] = useState(false);

  // An agent that belongs to a selected department is already carried by
  // that department's own export (build_department_export walks every agent
  // in the department) -- offering it again as a standalone pick would
  // double-export it under a second capa name.
  const coveredAgentIds = new Set(
    agents
      .filter((a) => a.departmentId && selection.departmentIds.has(a.departmentId))
      .map((a) => a.id),
  );
  const selectableAgents = agents.filter((a) => !coveredAgentIds.has(a.id));

  const hasSelection =
    selection.departmentIds.size > 0 || selection.agentIds.size > 0 || selection.skillIds.size > 0;

  function toggle(kind: keyof Selection, id: string, checked: boolean) {
    setSelection((prev) => {
      const next = new Set(prev[kind]);
      if (checked) next.add(id);
      else next.delete(id);
      return { ...prev, [kind]: next };
    });
  }

  function nameFor(kind: CapaExportItemInput["kind"], id: string): string {
    if (kind === "department") return departments.find((d) => d.id === id)?.name ?? "";
    if (kind === "agent") return agents.find((a) => a.id === id)?.name ?? "";
    return localSkills.find((s) => s.id === id)?.name ?? "";
  }

  function detailFor(kind: CapaExportItemInput["kind"], id: string): ItemDetails {
    return (
      details[detailKey(kind, id)] ?? {
        name: slugify(nameFor(kind, id)),
        version: "1.0.0",
        summary: "",
      }
    );
  }

  function setDetailField(
    kind: CapaExportItemInput["kind"],
    id: string,
    field: keyof ItemDetails,
    value: string,
  ) {
    setDetails((prev) => {
      const key = detailKey(kind, id);
      const current = prev[key] ?? detailFor(kind, id);
      return { ...prev, [key]: { ...current, [field]: value } };
    });
  }

  function buildItems(): CapaExportItemInput[] {
    const items: CapaExportItemInput[] = [];
    for (const deptId of selection.departmentIds) {
      const dept = departments.find((d) => d.id === deptId);
      if (!dept) continue;
      const d = detailFor("department", deptId);
      items.push({
        kind: "department",
        id: deptId,
        name: d.name,
        version: d.version,
        summary: d.summary,
      });
    }
    for (const agentId of selection.agentIds) {
      const agent = agents.find((a) => a.id === agentId);
      if (!agent) continue;
      const d = detailFor("agent", agentId);
      items.push({
        kind: "agent",
        id: agentId,
        name: d.name,
        version: d.version,
        summary: d.summary,
      });
    }
    for (const skillId of selection.skillIds) {
      const skill = localSkills.find((s) => s.id === skillId);
      if (!skill) continue;
      const d = detailFor("skill", skillId);
      items.push({
        kind: "skill",
        id: skillId,
        name: d.name,
        version: d.version,
        summary: d.summary,
      });
    }
    return items;
  }

  function goToPreview() {
    preview.mutate(buildItems());
    setStep("preview");
  }

  async function handleExport() {
    setExporting(true);
    try {
      const { blob, filename } = await downloadCapaExport(buildItems());
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(t("Capa exported", "Capa exportiert"), { description: filename });
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("Export failed", "Export fehlgeschlagen"));
    } finally {
      setExporting(false);
    }
  }

  const stepIdx = STEP_ORDER.indexOf(step);

  return (
    <div className="space-y-4">
      <StepDots steps={STEP_ORDER} labels={STEP_LABEL} current={step} />

      <div className="max-h-[55vh] overflow-y-auto">
        {step === "selection" && (
          <SelectionStep
            departments={departments}
            selectableAgents={selectableAgents}
            localSkills={localSkills}
            selection={selection}
            onToggle={toggle}
          />
        )}

        {step === "details" && (
          <DetailsStep
            departments={departments}
            agents={agents}
            localSkills={localSkills}
            selection={selection}
            detailFor={detailFor}
            onChange={setDetailField}
          />
        )}

        {step === "preview" && <PreviewStep preview={preview} />}

        {step === "export" && (
          <p className="text-sm text-muted-foreground">
            {t(
              "Click Export to download the ZIP file.",
              "Klicke Export, um die ZIP-Datei herunterzuladen.",
            )}
          </p>
        )}
      </div>

      <div className="flex justify-between border-t border-border pt-3">
        <button
          type="button"
          onClick={() => (stepIdx === 0 ? onClose() : setStep(STEP_ORDER[stepIdx - 1]))}
          className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          {stepIdx === 0 ? t("Cancel", "Abbrechen") : t("Back", "Zurück")}
        </button>

        {step === "selection" && (
          <button
            type="button"
            disabled={!hasSelection}
            onClick={() => setStep("details")}
            className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
          >
            {t("Next", "Weiter")}
          </button>
        )}

        {step === "details" && (
          <button
            type="button"
            onClick={goToPreview}
            className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground"
          >
            {t("Preview", "Vorschau")}
          </button>
        )}

        {step === "preview" && (
          <button
            type="button"
            disabled={preview.isPending}
            onClick={() => setStep("export")}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
          >
            {preview.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
            {t("Continue to export", "Weiter zum Export")}
          </button>
        )}

        {step === "export" && (
          <button
            type="button"
            disabled={exporting || preview.isPending || !preview.isSuccess}
            onClick={handleExport}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
          >
            {exporting && <Loader2 className="h-3 w-3 animate-spin" />}
            {exporting ? t("Exporting…", "Exportiere…") : t("Export", "Export")}
          </button>
        )}
      </div>
    </div>
  );
}

function SelectionStep({
  departments,
  selectableAgents,
  localSkills,
  selection,
  onToggle,
}: {
  departments: { id: string; name: string }[];
  selectableAgents: { id: string; name: string }[];
  localSkills: { id: string; name: string }[];
  selection: Selection;
  onToggle: (kind: keyof Selection, id: string, checked: boolean) => void;
}) {
  const t = useT();
  return (
    <div className="space-y-5">
      <section>
        <p className="text-sm font-medium">{t("Departments", "Departments")}</p>
        {departments.length === 0 && (
          <p className="mt-1 text-xs text-muted-foreground">
            {t("No departments yet.", "Noch keine Departments.")}
          </p>
        )}
        <div className="mt-2 space-y-1.5">
          {departments.map((dept) => (
            <label key={dept.id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={selection.departmentIds.has(dept.id)}
                onChange={(e) => onToggle("departmentIds", dept.id, e.target.checked)}
              />
              {dept.name}
            </label>
          ))}
        </div>
      </section>

      <section>
        <p className="text-sm font-medium">{t("Standalone agents", "Einzelne Agenten")}</p>
        {selectableAgents.length === 0 && (
          <p className="mt-1 text-xs text-muted-foreground">
            {t(
              "No agents outside a selected department.",
              "Keine Agenten außerhalb eines ausgewählten Departments.",
            )}
          </p>
        )}
        <div className="mt-2 space-y-1.5">
          {selectableAgents.map((agent) => (
            <label key={agent.id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={selection.agentIds.has(agent.id)}
                onChange={(e) => onToggle("agentIds", agent.id, e.target.checked)}
              />
              {agent.name}
            </label>
          ))}
        </div>
      </section>

      <section>
        <p className="text-sm font-medium">{t("Skills", "Skills")}</p>
        {localSkills.length === 0 && (
          <p className="mt-1 text-xs text-muted-foreground">
            {t(
              "No locally authored skills to export.",
              "Keine lokal erstellten Skills zum Exportieren.",
            )}
          </p>
        )}
        <div className="mt-2 space-y-1.5">
          {localSkills.map((skill) => (
            <label key={skill.id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={selection.skillIds.has(skill.id)}
                onChange={(e) => onToggle("skillIds", skill.id, e.target.checked)}
              />
              {skill.name}
            </label>
          ))}
        </div>
      </section>
    </div>
  );
}

function DetailsStep({
  departments,
  agents,
  localSkills,
  selection,
  detailFor,
  onChange,
}: {
  departments: { id: string; name: string }[];
  agents: { id: string; name: string }[];
  localSkills: { id: string; name: string }[];
  selection: Selection;
  detailFor: (kind: CapaExportItemInput["kind"], id: string) => ItemDetails;
  onChange: (
    kind: CapaExportItemInput["kind"],
    id: string,
    field: keyof ItemDetails,
    value: string,
  ) => void;
}) {
  const t = useT();
  const rows: { kind: CapaExportItemInput["kind"]; id: string; label: string }[] = [
    ...[...selection.departmentIds].flatMap((id) => {
      const dept = departments.find((d) => d.id === id);
      return dept ? [{ kind: "department" as const, id, label: dept.name }] : [];
    }),
    ...[...selection.agentIds].flatMap((id) => {
      const agent = agents.find((a) => a.id === id);
      return agent ? [{ kind: "agent" as const, id, label: agent.name }] : [];
    }),
    ...[...selection.skillIds].flatMap((id) => {
      const skill = localSkills.find((s) => s.id === id);
      return skill ? [{ kind: "skill" as const, id, label: skill.name }] : [];
    }),
  ];

  return (
    <div className="space-y-4">
      {rows.map(({ kind, id, label }) => {
        const current = detailFor(kind, id);
        return (
          <div key={`${kind}:${id}`} className="space-y-2 rounded-md border border-border p-3">
            <p className="text-sm font-medium">{label}</p>
            <label className="block text-xs text-muted-foreground">
              {t("Capa name", "Capa-Name")}
              <input
                className="mt-1 w-full rounded border border-border bg-background/40 px-2 py-1 text-sm text-foreground"
                value={current.name}
                onChange={(e) => onChange(kind, id, "name", e.target.value)}
              />
            </label>
            <label className="block text-xs text-muted-foreground">
              {t("Version", "Version")}
              <input
                className="mt-1 w-full rounded border border-border bg-background/40 px-2 py-1 text-sm text-foreground"
                value={current.version}
                onChange={(e) => onChange(kind, id, "version", e.target.value)}
              />
            </label>
            <label className="block text-xs text-muted-foreground">
              {t("Summary", "Kurzbeschreibung")}
              <input
                className="mt-1 w-full rounded border border-border bg-background/40 px-2 py-1 text-sm text-foreground"
                value={current.summary}
                onChange={(e) => onChange(kind, id, "summary", e.target.value)}
              />
            </label>
          </div>
        );
      })}
    </div>
  );
}

function PreviewStep({ preview }: { preview: ReturnType<typeof usePreviewCapaExport> }) {
  const t = useT();
  if (preview.isPending) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        {t("Building preview…", "Vorschau wird erstellt…")}
      </div>
    );
  }
  if (preview.isError) {
    return (
      <p className="text-sm text-destructive">
        {preview.error instanceof Error ? preview.error.message : String(preview.error)}
      </p>
    );
  }
  return (
    <div className="space-y-3">
      {preview.data?.errors.map((e) => (
        <p key={e} className="text-sm text-destructive">
          {e}
        </p>
      ))}
      {preview.data?.items.map((item) => (
        <div key={item.folder_name} className="rounded-md border border-border p-3">
          <p className="text-sm font-medium">{item.folder_name}/</p>
          {item.warnings.map((w) => (
            <p key={w} className="text-xs text-amber-500">
              ⚠ {w}
            </p>
          ))}
          <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap break-all rounded bg-background/40 p-2 text-xs text-muted-foreground">
            {item.manifest_toml}
          </pre>
          {Object.entries(item.extra_files).map(([path, content]) => (
            <div key={path} className="mt-2">
              <p className="text-[11px] text-muted-foreground">{path}</p>
              <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap break-all rounded bg-background/40 p-2 text-xs text-muted-foreground">
                {content}
              </pre>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
