import { createFileRoute } from "@tanstack/react-router";
import { CheckCircle2, Circle, Loader2, Play, Save, Settings2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Panel } from "@/components/app-shell";
import { BackupPanel } from "@/components/backup-panel";
import { OnboardingWizard } from "@/components/onboarding/onboarding-wizard";
import { useT } from "@/lib/i18n";
import {
  useCredentials,
  useMcpConnections,
  useModels,
  useOrganizationSettings,
  useUpdateOrganizationSettings,
} from "@/lib/hooks";

export const Route = createFileRoute("/settings")({
  component: SettingsPage,
});

function SettingsPage() {
  const t = useT();
  const [setupOpen, setSetupOpen] = useState(false);
  return (
    <>
      <div className="grid gap-4 md:grid-cols-2">
        <PilotSetup onOpenWizard={() => setSetupOpen(true)} />
        <OrganizationPanel />
        <BackupPanel />
      </div>
      {setupOpen && (
        // The SAME wizard as the first-run /welcome route -- there used to be
        // a second, older InstanceSetupWizard here with its own hardcoded
        // provider/model list and no real credential flow, so "Guided setup"
        // could not actually connect a provider (fixed by sharing this
        // component instead of maintaining two).
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4"
          onClick={() => setSetupOpen(false)}
        >
          <div
            className="w-full max-w-lg rounded-xl border border-border bg-panel p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between">
              <h2 className="font-serif text-lg">{t("Guided setup", "Geführte Einrichtung")}</h2>
              <button
                type="button"
                onClick={() => setSetupOpen(false)}
                className="rounded-md p-1 text-muted-foreground hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <OnboardingWizard />
          </div>
        </div>
      )}
    </>
  );
}

/** A checklist over live resources, not a separate state machine. A step is
 * complete only when the underlying API confirms it, so refreshing the page
 * can never turn pilot readiness back into a decorative progress indicator. */
function PilotSetup({ onOpenWizard }: { onOpenWizard: () => void }) {
  const t = useT();
  const { data: organization } = useOrganizationSettings();
  const { data: models = [] } = useModels();
  const { data: connections = [] } = useMcpConnections();
  const { data: credentials = [] } = useCredentials();
  const hasTestedConnection = connections.some((connection) => connection.connected);
  const hasProviderCredential = credentials.some((c) => c.credentialType.endsWith("_api_key"));
  const steps = [
    {
      label: t("Name your workspace", "Arbeitsbereich benennen"),
      done: Boolean(organization?.name.trim()),
      href: "#organization",
    },
    {
      label: t("Add a model configuration", "Modellkonfiguration anlegen"),
      done: models.length > 0,
      href: "/models",
    },
    {
      label: t("Store a provider credential", "Provider-Zugangsdaten hinterlegen"),
      done: hasProviderCredential,
      href: "/credentials",
    },
    {
      label: t("Test an MCP connection", "Eine MCP-Verbindung testen"),
      done: hasTestedConnection,
      href: "/capas",
    },
  ];
  const completed = steps.filter((step) => step.done).length;

  return (
    <Panel className="p-5 md:col-span-2">
      <header className="flex items-start gap-3">
        <div className="grid h-9 w-9 place-items-center rounded-md bg-primary/15 text-primary">
          <Settings2 className="h-4 w-4" />
        </div>
        <div>
          <h3 className="font-serif text-lg">{t("Pilot setup", "Pilot-Einrichtung")}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t(
              "Complete these live checks before inviting pilot users.",
              "Erledige diese Live-Prüfungen, bevor du Pilotnutzer einlädst.",
            )}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground">
            {completed}/{steps.length}
          </span>
          <button
            type="button"
            onClick={onOpenWizard}
            className="inline-flex items-center gap-1 rounded-md border border-border bg-background/30 px-2 py-1 text-xs text-foreground hover:border-primary/50"
          >
            <Play className="h-3 w-3" /> {t("Guided setup", "Geführte Einrichtung")}
          </button>
        </div>
      </header>
      <ol className="mt-4 grid gap-2 md:grid-cols-2">
        {steps.map((step) => (
          <li key={step.label}>
            <a
              href={step.href}
              className="flex items-center gap-2 rounded-md border border-border bg-background/30 px-3 py-2 text-sm transition hover:border-primary/40"
            >
              {step.done ? (
                <CheckCircle2 className="h-4 w-4 shrink-0 text-[color:var(--status-running)]" />
              ) : (
                <Circle className="h-4 w-4 shrink-0 text-muted-foreground" />
              )}
              {step.label}
            </a>
          </li>
        ))}
      </ol>
      <p className="mt-3 text-[11px] text-muted-foreground">
        {t(
          "The guided setup configures the tenant you are signed into. New tenants are deliberately provisioned by the administrator CLI so the application role never receives cross-tenant write authority.",
          "Die geführte Einrichtung konfiguriert den angemeldeten Mandanten. Neue Mandanten werden bewusst über die Administrator-CLI provisioniert, damit die App-Rolle nie mandantenübergreifende Schreibrechte erhält.",
        )}
      </p>
    </Panel>
  );
}

function OrganizationPanel() {
  const t = useT();
  const { data: organization, isLoading, error } = useOrganizationSettings();
  const update = useUpdateOrganizationSettings();
  const [name, setName] = useState("");
  const [region, setRegion] = useState("");

  useEffect(() => {
    if (organization) {
      setName(organization.name);
      setRegion(organization.region);
    }
  }, [organization]);

  const save = async () => {
    try {
      await update.mutateAsync({ name: name.trim(), region: region.trim() });
      toast.success(t("Organization updated", "Organisation aktualisiert"));
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : t("Could not save settings.", "Speichern fehlgeschlagen."),
      );
    }
  };

  return (
    <Panel id="organization" className="p-5">
      <h3 className="font-serif text-lg">{t("Organization", "Organisation")}</h3>
      <p className="mt-1 text-xs text-muted-foreground">
        {t("Basic details of your workspace.", "Grunddaten Ihres Arbeitsbereichs.")}
      </p>
      {isLoading && <Loader2 className="mt-5 h-4 w-4 animate-spin text-muted-foreground" />}
      {error && (
        <p className="mt-4 text-sm text-[color:var(--status-error)]">
          {t(
            "Could not load organization settings.",
            "Organisationseinstellungen konnten nicht geladen werden.",
          )}
        </p>
      )}
      {organization && (
        <div className="mt-4 space-y-3">
          <label className="block text-xs text-muted-foreground">
            {t("Name", "Name")}
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-background/30 px-3 py-2 text-sm text-foreground"
            />
          </label>
          <label className="block text-xs text-muted-foreground">
            {t("Region", "Region")}
            <input
              value={region}
              onChange={(event) => setRegion(event.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-background/30 px-3 py-2 text-sm text-foreground"
            />
          </label>
          <dl className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
            <div>
              <dt>{t("Tenant", "Mandant")}</dt>
              <dd className="mt-0.5 font-mono text-foreground">{organization.slug}</dd>
            </div>
            <div>
              <dt>{t("Tier", "Stufe")}</dt>
              <dd className="mt-0.5 text-foreground">{organization.tier}</dd>
            </div>
          </dl>
          <button
            type="button"
            onClick={save}
            disabled={!name.trim() || !region.trim() || update.isPending}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-40"
          >
            {update.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Save className="h-3 w-3" />
            )}
            {t("Save", "Speichern")}
          </button>
        </div>
      )}
    </Panel>
  );
}
