import { Component, type ReactNode } from "react";
import { GripVertical, X } from "lucide-react";
import { WIDGET_REGISTRY } from "@/components/dashboard/widget-registry";
import type { WidgetInstance } from "@/lib/hooks";
import { useT } from "@/lib/i18n";

class WidgetErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex h-full items-center justify-center p-3 text-center text-xs text-muted-foreground">
          This widget could not be displayed.
        </div>
      );
    }
    return this.props.children;
  }
}

export function WidgetFrame({
  instance,
  onConfigChange,
  onRemove,
}: {
  instance: WidgetInstance;
  onConfigChange: (config: Record<string, unknown>) => void;
  onRemove: () => void;
}) {
  const t = useT();
  const de = t("en", "de") === "de";
  const entry = Object.prototype.hasOwnProperty.call(WIDGET_REGISTRY, instance.type)
    ? WIDGET_REGISTRY[instance.type]
    : undefined;
  const Widget = entry?.component;

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg border border-border bg-panel">
      <div className="dashboard-widget-drag-handle flex shrink-0 cursor-move items-center gap-1.5 border-b border-border px-2 py-1.5">
        <GripVertical className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="flex-1 truncate text-xs font-medium">
          {entry ? entry.label(de) : t("Unregistered", "Nicht registriert")}
        </span>
        <button
          type="button"
          onClick={onRemove}
          aria-label={t("Remove widget", "Widget entfernen")}
          className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground transition hover:bg-muted/40 hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="flex-1 overflow-hidden">
        {Widget ? (
          <WidgetErrorBoundary>
            <Widget config={instance.config} onConfigChange={onConfigChange} />
          </WidgetErrorBoundary>
        ) : (
          <div className="flex h-full items-center justify-center p-3 text-center text-xs text-muted-foreground">
            {t("Unknown widget type", "Unbekannter Widget-Typ")}
          </div>
        )}
      </div>
    </div>
  );
}
