import { useState } from "react";
import { RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { Panel } from "@/components/app-shell";
import {
  useAgentInstructionHistory,
  useUpdateAgentInstructions,
  type AgentInstructionRevision,
} from "@/lib/hooks";
import { useT } from "@/lib/i18n";

interface VersionRow {
  text: string;
  ts: string | null;
  by: string | null;
  /** True only for the oldest version once every older page has been
   * loaded -- an as-yet-unloaded page could still hold an earlier
   * transition, so "Since creation" would be a guess until then. */
  isKnownOldest: boolean;
}

/** Rebuilds the version timeline from the current mission plus however much
 * of the audit trail's before/after pairs is loaded so far. `revisions` is
 * newest first (R3, R2, R1 for a history A->B->C->D where D is current): a
 * version's own "became active" timestamp is the NEXT revision's `ts` in
 * this newest-first order (D's is R3.ts, C's is R2.ts, B's is R1.ts),
 * because that revision is the one whose `after` made it current -- the
 * last loaded version's transition-in lives on a revision that may not be
 * loaded yet, so its ts/by only resolve to "since creation" once
 * `allLoaded` confirms there is truly nothing older. */
function buildVersions(
  mission: string,
  revisions: AgentInstructionRevision[],
  allLoaded: boolean,
): VersionRow[] {
  return [
    {
      text: mission,
      ts: revisions[0]?.ts ?? null,
      by: revisions[0]?.by ?? null,
      isKnownOldest: false,
    },
    ...revisions.map((rev, i) => {
      const isLast = i === revisions.length - 1;
      return {
        text: rev.before,
        ts: revisions[i + 1]?.ts ?? null,
        by: revisions[i + 1]?.by ?? null,
        isKnownOldest: isLast && allLoaded,
      };
    }),
  ];
}

/** Agent detail page's "Instructions" tab: the persistent, editable system
 * prompt every run sends (`Agent.mission` on the wire), laid out like
 * Impossible Cloud's IAM policy-version editor (editor pane + a numbered
 * "Versions" side panel with a Default badge and per-row restore) at the
 * user's explicit request -- adapted to oc8's own tamper-evident audit
 * chain (`agent.instructions.updated` events) instead of a bounded five-slot
 * version store, so nothing is pruned. The panel loads history a page at a
 * time (cursor pagination, same shape as the audit screen) so an agent
 * edited a hundred times doesn't drag the tab down loading all of it at
 * once; version numbers still count down from the server-reported total, so
 * "v1" stays correct however much of the list is actually on screen.
 * Restoring loads the old text into the draft rather than saving
 * immediately (Save still gates it), so a restore reads the same as any
 * other edit in the history it creates. Skills stay a separate, more
 * granular mechanism layered on top -- this tab is only the base
 * instructions every run carries. */
export function AgentInstructionsPanel({
  agentId,
  mission,
  mayManage,
}: {
  agentId: string;
  mission: string;
  mayManage: boolean;
}) {
  const t = useT();
  // Seeded once from the loaded agent, like AgentRuntimePanel's own `draft`
  // -- a later external change (another tab/session editing the same agent)
  // is the same rare, unhandled edge case that panel also leaves alone.
  const [draft, setDraft] = useState(mission);
  const update = useUpdateAgentInstructions();
  const history = useAgentInstructionHistory(agentId);

  const dirty = draft !== mission;
  const revisions = history.data?.pages.flatMap((p) => p.revisions) ?? [];
  const totalCount = history.data?.pages[0]?.totalCount ?? revisions.length;
  const versions = buildVersions(mission, revisions, !history.hasNextPage);
  const totalVersions = totalCount + 1;

  function save() {
    update.mutate(
      { agentId, instructions: draft },
      {
        onSuccess: () => toast.success(t("Instructions saved", "Anweisungen gespeichert")),
        onError: (error: Error) =>
          toast.error(
            t("Couldn't save instructions", "Anweisungen konnten nicht gespeichert werden"),
            {
              description: error.message,
            },
          ),
      },
    );
  }

  function restore(text: string) {
    setDraft(text);
    toast(
      t(
        "Version loaded — click Save to restore it",
        "Version geladen — zum Wiederherstellen auf Speichern klicken",
      ),
    );
  }

  return (
    <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_320px]">
      <Panel className="p-5">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="text-[10px] uppercase tracking-widest text-muted-foreground">
              {t("standing system prompt", "dauerhafter System-Prompt")}
            </div>
            <h3 className="mt-0.5 font-serif text-lg">{t("Instructions", "Anweisungen")}</h3>
          </div>
          {mayManage && (
            <button
              type="button"
              onClick={save}
              disabled={!dirty || update.isPending}
              className="shrink-0 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {update.isPending ? t("Saving…", "Wird gespeichert…") : t("Save", "Speichern")}
            </button>
          )}
        </div>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={!mayManage}
          rows={16}
          placeholder={t(
            "What should this agent do? Sent with every run.",
            "Was soll dieser Agent tun? Wird bei jedem Lauf mitgeschickt.",
          )}
          className="mt-4 w-full resize-y rounded-md border border-border bg-background/40 px-3 py-2 font-mono text-sm outline-none focus:border-primary/50 disabled:cursor-not-allowed disabled:opacity-60"
        />
        <p className="mt-3 text-[11px] text-muted-foreground">
          {t(
            "For task-specific instructions, assign a skill instead — this field is only the agent's base mission.",
            "Für aufgabenspezifische Anweisungen nutze stattdessen Skills — dieses Feld ist nur die Basis-Mission des Agenten.",
          )}
          {!mayManage &&
            ` ${t("Your role does not include agent:manage, so this field is read-only for you.", "Ihre Rolle enthält agent:manage nicht, deshalb ist dieses Feld für Sie schreibgeschützt.")}`}
        </p>
      </Panel>

      <Panel className="p-5">
        <h3 className="font-serif text-lg">{t("Versions", "Versionen")}</h3>
        <p className="mt-1 text-[11px] text-muted-foreground">
          {t(
            "Every save is kept — nothing is pruned.",
            "Jede Speicherung bleibt erhalten — nichts wird gelöscht.",
          )}
        </p>
        {history.isLoading && (
          <p className="mt-3 text-sm text-muted-foreground">{t("Loading…", "Wird geladen…")}</p>
        )}
        {history.isSuccess && (
          <ul className="mt-3 space-y-2">
            {versions.map((v, i) => {
              const versionLabel = `v${totalVersions - i}`;
              const isCurrent = i === 0;
              return (
                <li
                  key={i}
                  className="rounded-md border border-border/60 bg-background/30 p-2.5 text-xs"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono font-medium text-foreground">{versionLabel}</span>
                      {isCurrent && (
                        <span className="rounded-full border border-primary/40 bg-primary/10 px-1.5 py-0.5 text-[9px] uppercase tracking-widest text-primary">
                          {t("Default", "Standard")}
                        </span>
                      )}
                    </div>
                    {/* Gated on the DRAFT, not on isCurrent -- once an older
                     * version is loaded into the editor, the still-current
                     * (Default) row is the only way back without retyping
                     * it by hand, so it needs a Restore button too. */}
                    {v.text !== draft && mayManage && (
                      <button
                        type="button"
                        onClick={() => restore(v.text)}
                        title={t("Load into the editor", "In den Editor laden")}
                        className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] text-muted-foreground transition hover:border-primary/40 hover:text-foreground"
                      >
                        <RotateCcw className="h-3 w-3" />
                        {t("Restore", "Wiederherstellen")}
                      </button>
                    )}
                  </div>
                  <div className="mt-1 text-muted-foreground">
                    {v.ts
                      ? new Date(v.ts).toLocaleString()
                      : v.isKnownOldest
                        ? t("Since creation", "Seit Erstellung")
                        : ""}
                    {v.by && ` · ${v.by}`}
                  </div>
                  <p className="mt-1.5 line-clamp-2 text-foreground/80">
                    {v.text || t("(empty)", "(leer)")}
                  </p>
                </li>
              );
            })}
          </ul>
        )}
        {history.hasNextPage && (
          <button
            type="button"
            onClick={() => history.fetchNextPage()}
            disabled={history.isFetchingNextPage}
            className="mt-3 w-full rounded-md border border-border px-3 py-1.5 text-[11px] text-muted-foreground transition hover:border-primary/40 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          >
            {history.isFetchingNextPage
              ? t("Loading…", "Wird geladen…")
              : t("Load older versions", "Ältere Versionen laden")}
          </button>
        )}
      </Panel>
    </div>
  );
}
