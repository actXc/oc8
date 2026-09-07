// Meine Arbeit — a freely arrangeable widget grid, Android-homescreen-style.
// Approvals/clarifications, reports, budget, and activity are all widgets
// INSIDE the grid now, not a fixed area — see
// docs/superpowers/specs/2026-09-07-my-work-widget-dashboard-design.md.

import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Panel } from "@/components/app-shell";
import { DashboardGrid } from "@/components/dashboard/dashboard-grid";
import { TemplatePicker } from "@/components/dashboard/template-picker";
import { WidgetPicker } from "@/components/dashboard/widget-picker";
import {
  useDashboardLayout,
  useSaveDashboardLayout,
  useStanding,
  type DashboardTemplateDTO,
  type WidgetInstance,
  type WidgetType,
} from "@/lib/hooks";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/workspace")({
  component: WorkspacePage,
  validateSearch: (search: Record<string, unknown>): { item?: string } => {
    const item = search.item;
    return typeof item === "string" && item.length > 0 ? { item } : {};
  },
});

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}

export function WorkspacePage() {
  const t = useT();
  const standing = useStanding();
  const layoutQuery = useDashboardLayout();
  const save = useSaveDashboardLayout();

  const [widgets, setWidgets] = useState<WidgetInstance[] | null>(null);
  const [templateId, setTemplateId] = useState<string | null>(null);

  // Seed local state from the server once the query resolves. A `null`
  // response (no saved layout yet) is left as `null` here too -- rendering
  // the TemplatePicker below is what a `null` layout means.
  useEffect(() => {
    if (layoutQuery.data !== undefined && widgets === null) {
      if (layoutQuery.data !== null) {
        setWidgets(layoutQuery.data.widgets);
        setTemplateId(layoutQuery.data.templateId);
      }
    }
    // Only seed once, on the query's first resolution -- subsequent server
    // responses (e.g. from this same save) must never overwrite in-progress
    // local edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutQuery.data]);

  const debouncedWidgets = useDebouncedValue(widgets, 800);
  useEffect(() => {
    if (debouncedWidgets === null) return;
    save.mutate({ widgets: debouncedWidgets, templateId });
    // save/templateId change identity every render; only a real widgets
    // change should trigger a new debounced PUT.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedWidgets]);

  function pickTemplate(template: DashboardTemplateDTO) {
    setWidgets(template.widgets);
    setTemplateId(template.id);
  }

  function addWidget(type: WidgetType) {
    setWidgets((prev) => [
      ...(prev ?? []),
      {
        id: crypto.randomUUID(),
        type,
        x: 0,
        y: Number.POSITIVE_INFINITY,
        w: 4,
        h: 4,
        config: {},
      },
    ]);
  }

  if (standing.unassigned) {
    return (
      <Panel className="p-10 text-center">
        <h2 className="font-serif text-xl">
          {t("You are not assigned to a department", "Du bist keiner Abteilung zugeordnet")}
        </h2>
      </Panel>
    );
  }

  if (layoutQuery.isPending) {
    return (
      <div className="flex items-center justify-center p-10 text-sm text-muted-foreground">
        {t("Loading…", "Wird geladen…")}
      </div>
    );
  }

  if (widgets === null) {
    return <TemplatePicker onPick={pickTemplate} />;
  }

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <WidgetPicker onAdd={addWidget} />
      </div>
      <DashboardGrid widgets={widgets} onChange={setWidgets} />
    </div>
  );
}
