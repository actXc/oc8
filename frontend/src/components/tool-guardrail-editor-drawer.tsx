import { useState } from "react";
import { X } from "lucide-react";
import { useT } from "@/lib/i18n";
import { useConnectionToolNames, type McpConnection } from "@/lib/hooks";
import { GuardrailPresetPicker, type GuardrailValue } from "@/components/guardrail-preset-picker";

export function ToolGuardrailEditorDrawer({
  toolKey,
  connection,
  ceiling,
  value,
  onChange,
  loginPicker,
  onClose,
  onSave,
  saving,
}: {
  toolKey: string;
  connection: McpConnection | undefined;
  ceiling: GuardrailValue | null;
  value: GuardrailValue;
  onChange: (next: GuardrailValue) => void;
  loginPicker?: React.ReactNode;
  onClose: () => void;
  onSave: () => void;
  saving: boolean;
}) {
  const t = useT();
  const toolNames = useConnectionToolNames(connection?.name ?? "");
  const datalistId = `approval-tag-suggestions-${toolKey}`;
  // `only` is a tool-name allowlist (same vocabulary as approval-action tags --
  // see GuardrailPreset's own doc comment in guardrail-preset-picker.tsx: "the
  // ONLY tool names this preset puts within reach"), so the same connection
  // tool-name fetch used for the approval-tag datalist above is the correct,
  // only-needed data source here too -- no separate endpoint/prop required.
  const onlyOptions = toolNames.data?.names ?? [];
  const [approvalDraft, setApprovalDraft] = useState("");

  function commitApprovalDraft() {
    const name = approvalDraft.trim();
    setApprovalDraft("");
    if (!name || value.approvalActions.includes(name)) return;
    onChange({ ...value, approvalActions: [...value.approvalActions, name] });
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/40" onClick={onClose}>
      <div
        className="h-full w-full max-w-md overflow-y-auto border-l border-border bg-panel p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-serif text-lg">{toolKey}</h2>
          <button
            type="button"
            onClick={onClose}
            className="grid h-8 w-8 place-items-center rounded-md border border-border text-muted-foreground transition hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {ceiling && (
          <div className="mb-4 rounded-md border border-border/70 bg-background/30 p-3">
            <div className="mb-1 text-[10px] uppercase tracking-widest text-muted-foreground">
              {t("Department allows (ceiling)", "Department erlaubt (Decke)")}
            </div>
            <div className="text-xs text-muted-foreground">
              {ceiling.read && t("Read", "Lesen")}
              {ceiling.read && ceiling.modify && ", "}
              {ceiling.modify && t("Modify", "Verändern")}
              {ceiling.approvalActions.length > 0 &&
                ` — ${t("approval for", "Freigabe für")}: ${ceiling.approvalActions.join(", ")}`}
              {ceiling.approvalEur != null && ` — ≥ ${ceiling.approvalEur} €`}
            </div>
          </div>
        )}

        {loginPicker && (
          <div className="mb-4">
            <div className="mb-1 text-[10px] uppercase tracking-widest text-muted-foreground">
              {t("Login", "Login")}
            </div>
            {loginPicker}
          </div>
        )}

        <GuardrailPresetPicker
          presets={connection?.guardrailPresets ?? []}
          guardrailLibrary={connection?.guardrailLibrary ?? null}
          hasValueSpec={connection?.hasValueSpec ?? false}
          value={value}
          onChange={onChange}
        />

        <datalist id={datalistId}>
          {(toolNames.data?.names ?? []).map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
        <label className="mt-3 block text-xs uppercase tracking-wider text-muted-foreground">
          {t(
            "Also require approval for (suggestions from this tool)",
            "Zusätzlich Freigabe für (Vorschläge dieses Tools)",
          )}
          <input
            role="combobox"
            aria-label={t("Approval action suggestions", "Freigabe-Aktions-Vorschläge")}
            list={datalistId}
            value={approvalDraft}
            onChange={(e) => setApprovalDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitApprovalDraft();
              }
            }}
            onBlur={commitApprovalDraft}
            className="mt-1 w-full rounded-md border border-border bg-background/40 px-2 py-1 text-xs normal-case text-foreground outline-none focus:border-primary/50"
          />
        </label>

        {onlyOptions.length > 0 && (
          <div className="mt-4">
            <label className="block text-xs uppercase tracking-wider text-muted-foreground">
              {t("Only these surfaces (optional)", "Nur diese Bereiche (optional)")}
              <div className="mt-1 flex flex-wrap gap-1.5">
                {value.only.map((surface) => (
                  <span
                    key={surface}
                    className="inline-flex items-center gap-1 rounded-full border border-border bg-background/40 px-2 py-0.5 text-[11px] normal-case text-foreground"
                  >
                    {surface}
                    <button
                      type="button"
                      onClick={() =>
                        onChange({ ...value, only: value.only.filter((s) => s !== surface) })
                      }
                    >
                      ×
                    </button>
                  </span>
                ))}
                <select
                  value=""
                  onChange={(e) => {
                    if (!e.target.value) return;
                    if (!value.only.includes(e.target.value)) {
                      onChange({ ...value, only: [...value.only, e.target.value] });
                    }
                  }}
                  className="rounded-md border border-border bg-background/40 px-2 py-1 text-xs normal-case text-foreground"
                >
                  <option value="">{t("Add…", "Hinzufügen…")}</option>
                  {onlyOptions
                    .filter((o) => !value.only.includes(o))
                    .map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                </select>
              </div>
            </label>
          </div>
        )}

        <button
          type="button"
          onClick={onSave}
          disabled={saving}
          className="mt-5 w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:brightness-110 disabled:opacity-60"
        >
          {saving ? t("Saving…", "Wird gespeichert…") : t("Save", "Speichern")}
        </button>
      </div>
    </div>
  );
}
