import { useActivity } from "@/lib/hooks";
import { useT } from "@/lib/i18n";

export function ActivityWidget({}: { config: Record<string, unknown>; onConfigChange: (c: Record<string, unknown>) => void }) {
  const t = useT();
  const { data, isPending } = useActivity({ limit: 8 });
  const items = (data ?? []).slice(0, 8);

  if (isPending) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-xs text-muted-foreground">
        {t("Loading…", "Wird geladen…")}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-xs text-muted-foreground">
        {t("No recent activity", "Keine kürzliche Aktivität")}
      </div>
    );
  }

  return (
    <div className="h-full divide-y divide-border overflow-y-auto">
      {items.map((item) => (
        <div key={item.id} className="px-3 py-2 text-xs">
          <div className="truncate font-medium">{item.message}</div>
          <div className="truncate text-muted-foreground">{item.time}</div>
        </div>
      ))}
    </div>
  );
}
