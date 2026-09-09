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
import { ChatMarkdown } from "@/components/chat-markdown";
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
      <div className="flex flex-col gap-1 border-b border-destructive/30 bg-destructive/10 px-3 py-1.5 text-xs text-destructive">
        <div className="flex items-center gap-2">
          <TriangleAlert className="h-3.5 w-3.5" />
          <span>{t("This run failed.", "Dieser Lauf ist fehlgeschlagen.")}</span>
        </div>
        {run.output && <span className="pl-6 text-destructive/80">{run.output}</span>}
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
        <span className="text-muted-foreground">
          {run.phase ? `${run.phase} · ${run.steps}` : run.steps}
        </span>
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

// Renders the assistant's answer as it streams in token by token, in the
// exact spot the "waiting" typing-dots indicator used to sit alone. Reads
// the same ["run", runId] cache CopilotRunActivity above does -- `liveAnswer`
// only exists on it once the "run.token_delta" WS patcher has concatenated a
// first fragment onto it (see hooks-chat.ts's RunActivityDTO), so there is a
// dots-only gap between "user hit send" and "first token arrived" that this
// component does not try to fill; the caller renders its own dots for that.
export function CopilotStreamingAnswer({
  sessionId,
  runId,
}: {
  sessionId: string | null;
  runId: string | null;
}) {
  const { data: run } = useCopilotRunActivity(sessionId, runId);
  if (!run?.liveAnswer) return null;

  return (
    <div className="flex gap-2">
      <img src="/octopus_oc8.svg" alt="" className="mt-0.5 h-6 w-6 shrink-0" draggable={false} />
      <ChatMarkdown text={run.liveAnswer} className="max-w-[88%]" />
    </div>
  );
}
