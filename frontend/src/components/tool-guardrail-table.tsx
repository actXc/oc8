import { useState } from "react";
import { Plus } from "lucide-react";
import { useT } from "@/lib/i18n";
import type { McpConnection } from "@/lib/hooks";
import type { GuardrailValue } from "@/components/guardrail-preset-picker";
import { ToolGuardrailEditorDrawer } from "@/components/tool-guardrail-editor-drawer";
import { AddToolPicker } from "@/components/add-tool-picker";

export interface ToolGuardrailRow {
  toolKey: string;
  connection: McpConnection | undefined;
  ceilingPolicy: GuardrailValue | null;
  ownValue: GuardrailValue;
  status: "inherited" | "narrowed" | "agent-only" | null;
  deviationCount?: { count: number; total: number };
  loginPicker?: React.ReactNode;
}

function StatusBadge({ status, t }: { status: ToolGuardrailRow["status"]; t: (en: string, de: string) => string }) {
  if (status === "inherited") {
    return (
      <span className="rounded-full border border-border bg-background/40 px-2 py-0.5 text-[11px] text-muted-foreground">
        {t("Same as department", "Wie Department")}
      </span>
    );
  }
  if (status === "narrowed") {
    return (
      <span className="rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[11px] text-primary">
        {t("Narrowed", "Eingeschränkt")}
      </span>
    );
  }
  if (status === "agent-only") {
    return (
      <span className="rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[11px] text-primary">
        {t("This agent only", "Nur dieser Agent")}
      </span>
    );
  }
  return null;
}

export function ToolGuardrailTable({
  level,
  rows,
  addableNames,
  connections,
  onSave,
  onAdd,
  saving,
}: {
  level: "department" | "agent";
  rows: ToolGuardrailRow[];
  addableNames: string[];
  connections: McpConnection[];
  onSave: (toolKey: string, next: GuardrailValue) => void;
  onAdd: (name: string, policy: GuardrailValue | null) => void;
  saving: boolean;
}) {
  const t = useT();
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [draft, setDraft] = useState<GuardrailValue | null>(null);

  const editingRow = rows.find((r) => r.toolKey === editingKey);

  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-border text-[11px] uppercase tracking-wider text-muted-foreground">
            <th className="p-3">{t("Tool", "Tool")}</th>
            <th className="p-3">{t("Rights", "Rechte")}</th>
            <th className="p-3">{t("Approval needed for", "Freigabe nötig für")}</th>
            {level === "agent" && <th className="p-3">{t("Login", "Login")}</th>}
            {level === "agent" && <th className="p-3">{t("Status", "Status")}</th>}
            {level === "department" && <th className="p-3">{t("Deviations", "Abweichungen")}</th>}
            <th className="p-3" />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.toolKey} className="border-b border-border/60 last:border-0">
              <td className="p-3 font-medium">{row.toolKey}</td>
              <td className="p-3 text-xs text-muted-foreground">
                {[row.ownValue.read && t("Read", "Lesen"), row.ownValue.modify && t("Modify", "Verändern")]
                  .filter(Boolean)
                  .join(", ") || "—"}
              </td>
              <td className="p-3 text-xs text-muted-foreground">
                {row.ownValue.approvalActions.join(", ") || "—"}
                {row.ownValue.approvalEur != null && ` ≥ ${row.ownValue.approvalEur} €`}
              </td>
              {level === "agent" && <td className="p-3">{row.loginPicker ?? "—"}</td>}
              {level === "agent" && (
                <td className="p-3">
                  <StatusBadge status={row.status} t={t} />
                </td>
              )}
              {level === "department" && (
                <td className="p-3 text-xs text-muted-foreground">
                  {row.deviationCount
                    ? t(
                        `${row.deviationCount.count} of ${row.deviationCount.total} agents`,
                        `${row.deviationCount.count} von ${row.deviationCount.total} Agents`,
                      )
                    : "—"}
                </td>
              )}
              <td className="p-3 text-right">
                <button
                  type="button"
                  onClick={() => {
                    setDraft(row.ownValue);
                    setEditingKey(row.toolKey);
                  }}
                  className="rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground transition hover:bg-accent"
                >
                  {t("Edit", "Bearbeiten")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="border-t border-border p-3">
        <button
          type="button"
          onClick={() => setPickerOpen(true)}
          disabled={addableNames.length === 0}
          className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background/40 px-2.5 py-1.5 text-xs font-medium text-foreground transition hover:bg-background/70 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" /> {t("Add tool", "Tool hinzufügen")}
        </button>
      </div>

      {editingRow && draft && (
        <ToolGuardrailEditorDrawer
          toolKey={editingRow.toolKey}
          connection={editingRow.connection}
          ceiling={level === "agent" ? editingRow.ceilingPolicy : null}
          value={draft}
          onChange={setDraft}
          loginPicker={editingRow.loginPicker}
          onClose={() => setEditingKey(null)}
          onSave={() => {
            onSave(editingRow.toolKey, draft);
            setEditingKey(null);
          }}
          saving={saving}
        />
      )}

      {pickerOpen && (
        <AddToolPicker
          addableNames={addableNames}
          connections={connections}
          onAdd={(name, policy) => {
            onAdd(name, policy);
            setPickerOpen(false);
          }}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}
