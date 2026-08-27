import { KeyRound } from "lucide-react";
import { useT } from "@/lib/i18n";
import { useDepartments, type PluginSetupSpec } from "@/lib/hooks";
import { CredentialPicker } from "@/components/credential-picker";

/** Generic field renderer for the declarative setup contract in a plugin
 * manifest. It contains no provider, product, command, environment-variable,
 * or field names -- those all belong to the plugin. No modal chrome here, so
 * callers (the plugin setup dialog, the onboarding wizard's tool-connect
 * step) can embed it wherever they need. */
export function PluginSetupFields({
  setup,
  values,
  onChange,
}: {
  setup: PluginSetupSpec;
  values: Record<string, string>;
  onChange: (values: Record<string, string>) => void;
}) {
  const t = useT();
  // Department picker for the "department" field kind, not a paginated list
  // view.
  const { data: departmentsPage } = useDepartments({ pageSize: 200 });
  const departments = departmentsPage?.items ?? [];

  return (
    <div className="space-y-3">
      {setup.fields.map((field) => (
        <label key={field.key} className="block text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            {field.kind === "password" && <KeyRound className="h-3 w-3 text-primary" />}
            {field.label}
          </span>
          {field.kind === "department" ? (
            <select
              value={values[field.key] ?? ""}
              onChange={(event) => onChange({ ...values, [field.key]: event.target.value })}
              className="mt-1 w-full rounded-md border border-border bg-background/40 px-3 py-2 text-sm text-foreground"
            >
              <option value="">{t("Leave unassigned", "Nicht zugewiesen lassen")}</option>
              {departments.map((department) => (
                <option key={department.id} value={department.id}>
                  {department.name}
                </option>
              ))}
            </select>
          ) : field.kind === "credential" ? (
            <div className="mt-1">
              <CredentialPicker
                credentialType={field.credential_type ?? ""}
                value={values[field.key] ?? ""}
                onChange={(credentialId) => onChange({ ...values, [field.key]: credentialId })}
              />
            </div>
          ) : (
            <input
              type={field.kind === "password" ? "password" : field.kind === "url" ? "url" : "text"}
              value={values[field.key] ?? ""}
              onChange={(event) => onChange({ ...values, [field.key]: event.target.value })}
              placeholder={field.placeholder}
              className="mt-1 w-full rounded-md border border-border bg-background/40 px-3 py-2 text-sm text-foreground"
            />
          )}
          {field.help && (
            <span className="mt-1 block text-[11px] text-muted-foreground/80">{field.help}</span>
          )}
        </label>
      ))}
    </div>
  );
}
