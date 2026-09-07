import { useBudgetStatus } from "@/lib/hooks";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function BudgetWidget({
  config,
  onConfigChange,
}: {
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
}) {
  const t = useT();
  const departmentId = typeof config.departmentId === "string" ? config.departmentId : null;
  const { data: status, isPending } = useBudgetStatus(departmentId);

  if (isPending || !status) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-xs text-muted-foreground">
        {t("Loading…", "Wird geladen…")}
      </div>
    );
  }

  const limit = status.hardLimitTokens ?? status.softLimitTokens;
  const pct = limit ? Math.min(100, Math.round((status.currentTokens / limit) * 100)) : 0;

  return (
    <div className="flex h-full flex-col justify-center gap-2 p-4">
      <div className="text-[10px] uppercase tracking-widest text-muted-foreground">
        {status.scope === "tenant"
          ? t("Tenant-wide", "Unternehmensweit")
          : t("Department", "Abteilung")}
      </div>
      <div className="font-mono text-sm tabular-nums">
        {status.currentTokens}
        {limit ? ` / ${limit}` : ""}
      </div>
      {limit && (
        <div className="h-2 w-full overflow-hidden rounded-full bg-muted/40">
          <div
            className={cn(
              "h-full rounded-full",
              status.hardExceeded
                ? "bg-[color:var(--status-error)]"
                : status.softExceeded
                  ? "bg-[color:var(--status-warning)]"
                  : "bg-primary",
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
      {status.hardExceeded && (
        <div className="text-xs text-[color:var(--status-error)]">
          {t("Hard limit exceeded", "Hartes Limit überschritten")}
        </div>
      )}
    </div>
  );
}
