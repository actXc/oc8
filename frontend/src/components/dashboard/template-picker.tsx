import { useDashboardTemplates, type DashboardTemplateDTO } from "@/lib/hooks";
import { useT } from "@/lib/i18n";

export function TemplatePicker({ onPick }: { onPick: (template: DashboardTemplateDTO) => void }) {
  const t = useT();
  const de = t("en", "de") === "de";
  const { data: templates, isPending } = useDashboardTemplates();

  if (isPending) {
    return (
      <div className="flex items-center justify-center p-10 text-sm text-muted-foreground">
        {t("Loading…", "Wird geladen…")}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-10 text-center">
      <h2 className="font-serif text-xl">
        {t("Choose a starting layout", "Wähle eine Startanordnung")}
      </h2>
      <p className="text-sm text-muted-foreground">
        {t(
          "You can rearrange, resize, or remove any tile afterwards.",
          "Du kannst danach jede Kachel verschieben, verändern oder entfernen.",
        )}
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {(templates ?? []).map((template) => (
          <button
            key={template.id}
            type="button"
            onClick={() => onPick(template)}
            className="rounded-lg border border-border bg-panel p-4 text-left transition hover:border-primary/50"
          >
            <div className="font-medium">{de ? template.name.de : template.name.en}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              {template.widgets.length}{" "}
              {template.widgets.length === 1 ? t("tile", "Kachel") : t("tiles", "Kacheln")}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
