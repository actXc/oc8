import { Check } from "lucide-react";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function StepDots<StepId extends string>({
  steps,
  labels,
  current,
}: {
  steps: StepId[];
  labels: Record<StepId, [string, string]>;
  current: StepId | "done";
}) {
  const t = useT();
  const idx = current === "done" ? steps.length : steps.indexOf(current as StepId);
  return (
    <ol className="flex items-center justify-center gap-3">
      {steps.map((s, i) => (
        <li key={s} className="flex items-center gap-2">
          <span
            className={cn(
              "grid h-6 w-6 place-items-center rounded-full text-[11px] font-semibold transition-transform",
              i < idx
                ? "scale-100 bg-primary text-primary-foreground"
                : i === idx
                  ? "bg-primary/20 text-primary ring-1 ring-primary"
                  : "bg-background/60 text-muted-foreground",
            )}
          >
            {i < idx ? <Check className="h-3.5 w-3.5" /> : i + 1}
          </span>
          <span className="text-xs text-muted-foreground">{t(labels[s][0], labels[s][1])}</span>
        </li>
      ))}
    </ol>
  );
}
