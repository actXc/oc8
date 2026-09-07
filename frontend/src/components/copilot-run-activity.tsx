// A compact strip showing what the current run is doing right now --
// step count, latest tool call, and an explicit failed-run notice. Reads
// the ["run", runId] cache useCopilotRunActivity seeds, kept live by the WS
// patchers in live/apply-event.ts. This component itself sits after
// CopilotChatTab's `if (!active) return null`, so it unmounts along with
// the rest of a backgrounded tab's JSX and does NOT keep observing patches
// while unfocused -- reactivating the tab simply re-fetches the run's
// current state, which is correct and cheap (useCopilotRunActivity's
// staleTime is Infinity, so this only ever fires once per distinct runId).

import { useCopilotRunActivity } from "@/lib/hooks-chat";
import { useT } from "@/lib/i18n";
import { ChevronDown, ChevronUp, Loader2, TriangleAlert } from "lucide-react";
import { useState } from "react";

export function CopilotRunActivity({
  sessionId,
  runId,
}: {
  sessionId: string | null;
  runId: string | null;
}) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const { data: run } = useCopilotRunActivity(sessionId, runId);

  if (!run) return null;

  if (run.state === "failed") {
    return (
      <div className="flex items-center gap-2 border-b border-destructive/30 bg-destructive/10 px-3 py-1.5 text-xs text-destructive">
        <TriangleAlert className="h-3.5 w-3.5" />
        <span>{t("This run failed.", "Dieser Lauf ist fehlgeschlagen.")}</span>
      </div>
    );
  }

  if (run.state === "done" || run.state === "interrupted") return null;

  const lastCall = run.toolCalls[run.toolCalls.length - 1] as { name?: string } | undefined;

  return (
    <div className="border-b border-border bg-muted/30 text-xs">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left"
      >
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        <span className="flex-1 truncate">
          {lastCall?.name
            ? t(`Working: ${lastCall.name}`, `Arbeitet: ${lastCall.name}`)
            : t("Working...", "Arbeitet ...")}
        </span>
        <span className="text-muted-foreground">{run.steps}</span>
        {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
      </button>
      {expanded && run.toolCalls.length > 0 && (
        <ul className="space-y-0.5 px-3 pb-2 text-muted-foreground">
          {run.toolCalls.map((call, i) => (
            <li key={i} className="truncate">
              {(call as { name?: string }).name ?? "?"}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
