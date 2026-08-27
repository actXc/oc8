// Meine Arbeit — the screen the person who actually signs things off works in.
//
// A ROUTE, not a sheet. The old <ApprovalsSheet> was mounted globally in
// app-shell and had no URL, so "send your Head of Sales this link" was
// impossible; `/workspace?item=<id>` deep-links to one decision.
//
// ONE queue holding both kinds of waiting work: approvals an agent is holding,
// and questions an agent parked mid-run. The second kind had no screen at all —
// the only way to answer one was POST /runs/{id}/answer, gated on `run:control`,
// which is in no seat vocabulary, so the person the agent was waiting for was
// exactly the person who could not reply.
//
// Structure is copied from src/routes/handoffs.tsx — the only other screen that
// combines filter pills, a live list, a detail pane and a state-changing action
// with a reason — and none of its three defects: no hardcoded department
// literal (the departments come from the caller's seats and from the rows), no
// @/lib/mock-data import (every field is on the wire), and no synthesised
// timeline (nothing here is invented for the sake of looking finished).

import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronRight,
  HelpCircle,
  KeyRound,
  Send,
  ShieldCheck,
  Sparkles,
  XCircle,
} from "lucide-react";
import { Panel } from "@/components/app-shell";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  useAnswerClarification,
  useApprovals,
  useClarifications,
  useDecideApproval,
  useStanding,
  type Approval,
  type Clarification,
  type Seat,
} from "@/lib/hooks";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/workspace")({
  component: WorkspacePage,
  // `?item=<id>` is the whole reason this is a route. Anything else in the query
  // string is dropped rather than carried, so a stale link cannot smuggle state
  // into the screen.
  validateSearch: (search: Record<string, unknown>): { item?: string } => {
    const item = search.item;
    return typeof item === "string" && item.length > 0 ? { item } : {};
  },
});

// --------------------------------------------------------------------- model

type Kind = "approval" | "clarification";

interface Row {
  kind: Kind;
  id: string;
  title: string;
  agentId: string;
  agentName: string;
  /** `null` means TENANT-WIDE — today only the budget incident, and only shown
   *  to somebody unrestricted. It is not "unknown". */
  departmentId: string | null;
  departmentName: string;
  amount: string | null;
  createdAt: string;
}

/** What a decided row leaves behind for the rest of the session. Deliberately
 *  session-local and not a second query: "Von dir entschieden" is a receipt for
 *  what you just did, not a history screen. */
interface DecidedRow {
  id: string;
  kind: Kind;
  title: string;
  outcome: "approved" | "rejected" | "answered";
}

const TENANT_WIDE = "__tenant_wide__";

function approvalRow(a: Approval): Row {
  return {
    kind: "approval",
    id: a.id,
    title: a.title,
    agentId: a.agentId,
    agentName: a.agentName || a.agentId,
    departmentId: a.departmentId ?? null,
    departmentName: a.departmentName ?? "",
    amount: a.amount ?? null,
    createdAt: a.createdAt ?? "",
  };
}

function clarificationRow(c: Clarification): Row {
  return {
    kind: "clarification",
    id: c.id,
    title: c.question,
    agentId: c.agentId,
    agentName: c.agentName || c.agentId,
    departmentId: c.departmentId,
    departmentName: c.departmentName,
    amount: null,
    createdAt: c.createdAt,
  };
}

/** Relative age from an ISO timestamp. Empty for an empty or unparseable one —
 *  a row that says "0m" because a field was blank is a row that lies about how
 *  long somebody has been waiting. */
function relativeAge(iso: string): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 60) return `${mins}m`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.round(hrs / 24)}d`;
}

/** A stable colour per agent, derived from its id. The agent list is behind
 *  `agent:view`, which an employee does not hold, so the avatar cannot come from
 *  there — and a screen that 403s to draw a circle is not worth the circle. */
function avatarColor(agentId: string): string {
  let h = 0;
  for (let i = 0; i < agentId.length; i++) h = (h * 31 + agentId.charCodeAt(i)) % 360;
  return `oklch(0.78 0.12 ${h})`;
}

/** Whether the caller may act on a row, and the wire now says exactly.
 *
 * `decidesEverywhere` and not `unrestricted`. The two are different people and
 * the backend has always known it — `authz/scope.py` carries two flags and
 * documents at length why they must not become one — but `/me` sent only the
 * first, so this returned true for an `auditor`, who holds `approval:view_any`,
 * sees every department, and is 403'd by `require_departmental(approval:decide)`
 * on every click. The role's entire value is that its account cannot have caused
 * what it is auditing; offering it Approve was the screen contradicting that.
 *
 * Below that, authority is only the seats, so the seat role in that row's own
 * department is the whole answer: a `dept_viewer` reads the queue and may not
 * empty it, and hiding the buttons is the honest way to say so.
 */
function mayActOn(seats: Seat[], decidesEverywhere: boolean, departmentId: string | null): boolean {
  if (decidesEverywhere) return true;
  // `null` is TENANT-WIDE (the budget incident). A seat is not the tenant, and
  // the backend's `may_decide(None)` says the same.
  if (!departmentId) return false;
  return seats.some((s) => s.departmentId === departmentId && s.seatRole === "dept_approver");
}

/** WHY the buttons are not there, which is a different sentence for the two
 *  people it happens to. "Your seat here is view-only" is a lie told to an
 *  `auditor`, who has no seat at all and is refused by his role. */
type ViewOnlyBecause = "role" | "seat";

// ---------------------------------------------------------------- the screen

function WorkspacePage() {
  const t = useT();
  const { item: selectedId } = Route.useSearch();
  const navigate = useNavigate();
  const standing = useStanding();

  const approvalsQuery = useApprovals("pending");
  const clarificationsQuery = useClarifications();
  const approvals = useMemo(() => approvalsQuery.data ?? [], [approvalsQuery.data]);
  const clarifications = useMemo(() => clarificationsQuery.data ?? [], [clarificationsQuery.data]);

  const [kindFilter, setKindFilter] = useState<"all" | Kind>("all");
  const [deptFilter, setDeptFilter] = useState<string | null>(null);
  const [decided, setDecided] = useState<DecidedRow[]>([]);

  const decide = useDecideApproval();
  const answer = useAnswerClarification();

  // Selecting a row REPLACES the history entry: the back button should leave the
  // workspace, not walk back through every row you glanced at. A link somebody
  // was sent still lands on its row, which is the point of the parameter.
  const select = (id: string | null) =>
    navigate({ to: "/workspace", search: id ? { item: id } : {}, replace: true });

  const decidedIds = useMemo(() => new Set(decided.map((d) => d.id)), [decided]);

  const rows = useMemo(() => {
    const all = [...approvals.map(approvalRow), ...clarifications.map(clarificationRow)]
      // A row we just decided stays out of the queue even before the refetch
      // lands, so it cannot appear in both columns for a second.
      .filter((r) => !decidedIds.has(r.id));
    // Newest first, id as the tie-break: two rows written in one transaction
    // share a timestamp to the microsecond (the backend orders the same way and
    // for the same reason), and an unstable sort makes a list jump on refetch.
    return all.sort((a, b) => b.createdAt.localeCompare(a.createdAt) || b.id.localeCompare(a.id));
  }, [approvals, clarifications, decidedIds]);

  // The departments worth offering as a filter are the ones with work in them,
  // not every department that exists: a filter option that always shows an empty
  // list is furniture, and the employee cannot read /departments anyway.
  const departmentOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const r of rows) {
      const key = r.departmentId ?? TENANT_WIDE;
      if (!seen.has(key)) seen.set(key, r.departmentName);
    }
    return [...seen.entries()].map(([id, name]) => ({ id, name }));
  }, [rows]);

  // A department filter only counts while its pill is on screen. Deciding the
  // last item in a department drops it out of `departmentOptions`, and the pill
  // row disappears entirely once one department is left — without this the
  // filter would go on excluding rows with no visible control to clear it, which
  // reads exactly like the queue being empty.
  const activeDept =
    deptFilter && departmentOptions.length > 1 && departmentOptions.some((d) => d.id === deptFilter)
      ? deptFilter
      : null;

  const filtered = useMemo(
    () =>
      rows.filter((r) => {
        if (kindFilter !== "all" && r.kind !== kindFilter) return false;
        if (activeDept && (r.departmentId ?? TENANT_WIDE) !== activeDept) return false;
        return true;
      }),
    [rows, kindFilter, activeDept],
  );

  const current = rows.find((r) => r.id === selectedId) ?? null;
  const currentApproval =
    current?.kind === "approval" ? (approvals.find((a) => a.id === current.id) ?? null) : null;
  const currentClarification =
    current?.kind === "clarification"
      ? (clarifications.find((c) => c.id === current.id) ?? null)
      : null;

  // Unrestricted but not a decider is exactly one role today: `auditor`, who
  // holds `approval:view_any` and no decide right anywhere. He is refused by his
  // ROLE, not by a seat he does not have.
  const viewOnlyBecause: ViewOnlyBecause =
    standing.unrestricted && !standing.decidesEverywhere ? "role" : "seat";

  const loadError = approvalsQuery.error ?? clarificationsQuery.error;
  const loading = approvalsQuery.isPending || clarificationsQuery.isPending || standing.pending;

  // --- actions ---

  async function onDecide(
    a: Approval,
    decision: "approve" | "reject",
    reason: string,
    option: string | null,
  ) {
    try {
      const result = await decide.mutateAsync({
        approvalId: a.id,
        decision,
        reason,
        option,
      });
      if (result?.resumed === false) {
        // The decision stuck, but nothing picked the work back up. Saying
        // "Approved" here would tell the operator the agent is carrying on when
        // it never will.
        toast.warning(
          decision === "approve"
            ? t(
                "Recorded — but the agent's run could not be resumed, so the action never ran",
                "Erfasst — aber der Lauf des Agenten konnte nicht fortgesetzt werden, die Aktion wurde also nie ausgeführt",
              )
            : t(
                "Recorded — the run was already gone, so there was nothing to stop",
                "Erfasst — der Lauf war bereits beendet, es gab also nichts mehr zu stoppen",
              ),
        );
      } else {
        toast.success(
          decision === "approve" ? t("Approved", "Zugestimmt") : t("Rejected", "Abgelehnt"),
        );
      }
      setDecided((prev) => [
        {
          id: a.id,
          kind: "approval",
          title: a.title,
          outcome: decision === "approve" ? "approved" : "rejected",
        },
        ...prev,
      ]);
      select(null);
    } catch (e) {
      toast.error(
        e instanceof Error
          ? e.message
          : t("Could not record the decision", "Entscheidung konnte nicht erfasst werden"),
      );
    }
  }

  async function onAnswer(c: Clarification, text: string) {
    try {
      await answer.mutateAsync({ clarificationId: c.id, answer: text });
      toast.success(t("Answer sent", "Antwort gesendet"));
      setDecided((prev) => [
        { id: c.id, kind: "clarification", title: c.question, outcome: "answered" },
        ...prev,
      ]);
      select(null);
    } catch (e) {
      toast.error(
        e instanceof Error
          ? e.message
          : t("Could not send the answer", "Antwort konnte nicht gesendet werden"),
      );
    }
  }

  // --- the two empty states, which are the most important thing on this page ---
  //
  // "Nothing is waiting for you" and "nobody has assigned you to a department"
  // are the same blank screen today, and one of them is the system working while
  // the other is a person locked out of their own job.
  if (standing.unassigned) {
    return (
      <Panel className="p-10 text-center">
        <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-[color:var(--status-warning)]/15 text-[color:var(--status-warning)]">
          <KeyRound className="h-6 w-6" />
        </div>
        <h2 className="mt-4 font-serif text-xl">
          {t("You are not assigned to a department", "Du bist keiner Abteilung zugeordnet")}
        </h2>
        <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
          {t(
            "Your account holds nothing company-wide and nobody has given you a seat yet. Ask an administrator to add you — your queue appears the moment they do, with no new sign-in.",
            "Dein Konto hat keine unternehmensweiten Rechte und niemand hat dir bisher einen Platz gegeben. Bitte einen Administrator, dich einzutragen — deine Liste erscheint, sobald das geschehen ist, ganz ohne neue Anmeldung.",
          )}
        </p>
        <Link
          to="/governance"
          className="mt-5 inline-flex items-center gap-1.5 rounded-md border border-border bg-panel px-3 py-2 text-sm transition hover:text-primary"
        >
          {t("What may I do?", "Was darf ich?")} <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </Panel>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Pill active={kindFilter === "all"} onClick={() => setKindFilter("all")}>
          {t("All", "Alles")}
        </Pill>
        <Pill active={kindFilter === "approval"} onClick={() => setKindFilter("approval")}>
          {t("Approvals", "Freigaben")}
        </Pill>
        <Pill
          active={kindFilter === "clarification"}
          onClick={() => setKindFilter("clarification")}
        >
          {t("Questions", "Rückfragen")}
        </Pill>

        {/* A picker over one department is furniture, so it only appears when
            there is actually something to tell apart. */}
        {departmentOptions.length > 1 && (
          <>
            <span className="mx-1 h-4 w-px bg-border" />
            <Pill active={activeDept === null} onClick={() => setDeptFilter(null)}>
              {t("Every department", "Alle Abteilungen")}
            </Pill>
            {departmentOptions.map((d) => (
              <Pill key={d.id} active={activeDept === d.id} onClick={() => setDeptFilter(d.id)}>
                {d.id === TENANT_WIDE
                  ? t("Company-wide", "Unternehmensweit")
                  : d.name || t("Unnamed department", "Unbenannte Abteilung")}
              </Pill>
            ))}
          </>
        )}

        <span className="ml-auto text-xs text-muted-foreground">
          {filtered.length}{" "}
          {filtered.length === 1
            ? t("item waiting", "Vorgang wartet")
            : t("items waiting", "Vorgänge warten")}
        </span>
      </div>

      {loadError && (
        <Panel className="flex items-start gap-3 border-[color:var(--status-error)]/40 p-4 text-sm">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[color:var(--status-error)]" />
          <div>
            <div className="font-medium">
              {t("Your queue could not be loaded", "Deine Liste konnte nicht geladen werden")}
            </div>
            {/* Named rather than swallowed: an empty list drawn over a failed
                fetch is the screen telling somebody there is nothing to do. */}
            <div className="mt-0.5 text-xs text-muted-foreground">
              {loadError instanceof Error ? loadError.message : String(loadError)}
            </div>
          </div>
        </Panel>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(300px,380px)_1fr]">
        <div className="space-y-4">
          <Panel className="divide-y divide-border">
            {filtered.length === 0 && !loadError && (
              <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
                <div className="grid h-12 w-12 place-items-center rounded-full bg-primary/10">
                  <Sparkles className="h-6 w-6 text-primary" />
                </div>
                <div className="font-serif text-lg">
                  {loading
                    ? t("Loading…", "Wird geladen…")
                    : t("Nothing is waiting for you.", "Nichts wartet auf dich.")}
                </div>
                {!loading && (
                  <p className="max-w-xs text-sm text-muted-foreground">
                    {rows.length > 0
                      ? t(
                          "Nothing under this filter. Your other departments still have work.",
                          "Unter diesem Filter nichts. In deinen anderen Abteilungen liegt noch Arbeit.",
                        )
                      : t(
                          "Everything your agents needed from you has been answered.",
                          "Alles, was deine Agenten von dir gebraucht haben, ist beantwortet.",
                        )}
                  </p>
                )}
              </div>
            )}
            {filtered.map((r) => (
              <QueueRow
                key={r.id}
                row={r}
                active={r.id === selectedId}
                showDepartment={departmentOptions.length > 1}
                onOpen={() => select(r.id)}
              />
            ))}
          </Panel>

          {decided.length > 0 && (
            <Panel className="divide-y divide-border">
              <div className="px-4 py-2 text-[10px] uppercase tracking-widest text-muted-foreground">
                {t("Decided by you", "Von dir entschieden")}
              </div>
              {decided.map((d) => (
                <div key={d.id} className="flex items-center gap-2 px-4 py-2.5 text-sm">
                  <span
                    className={cn(
                      "grid h-5 w-5 shrink-0 place-items-center rounded-full text-[10px]",
                      d.outcome === "rejected"
                        ? "bg-[color:var(--status-error)]/15 text-[color:var(--status-error)]"
                        : "bg-[color:var(--status-running)]/15 text-[color:var(--status-running)]",
                    )}
                  >
                    {d.outcome === "rejected" ? "✕" : "✓"}
                  </span>
                  <span className="truncate text-muted-foreground">{d.title}</span>
                </div>
              ))}
            </Panel>
          )}
        </div>

        <Panel className="min-h-[320px]">
          {currentApproval ? (
            <ApprovalPane
              key={currentApproval.id}
              approval={currentApproval}
              mayAct={mayActOn(
                standing.seats,
                standing.decidesEverywhere,
                currentApproval.departmentId ?? null,
              )}
              viewOnlyBecause={viewOnlyBecause}
              busy={decide.isPending}
              onDecide={onDecide}
            />
          ) : currentClarification ? (
            <ClarificationPane
              key={currentClarification.id}
              clarification={currentClarification}
              mayAct={mayActOn(
                standing.seats,
                standing.decidesEverywhere,
                currentClarification.departmentId,
              )}
              viewOnlyBecause={viewOnlyBecause}
              busy={answer.isPending}
              onAnswer={onAnswer}
            />
          ) : (
            <div className="flex h-full min-h-[320px] flex-col items-center justify-center gap-2 px-8 text-center">
              <ShieldCheck className="h-6 w-6 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                {selectedId
                  ? t(
                      "That item is no longer in your queue — it may have been decided already.",
                      "Dieser Vorgang liegt nicht mehr in deiner Liste — vielleicht wurde er bereits entschieden.",
                    )
                  : t("Pick something on the left.", "Wähle links einen Vorgang aus.")}
              </p>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ the list

function Pill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-3 py-1 text-xs transition",
        active
          ? "border-primary/50 bg-primary/10 text-primary"
          : "border-border bg-panel text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function QueueRow({
  row,
  active,
  showDepartment,
  onOpen,
}: {
  row: Row;
  active: boolean;
  showDepartment: boolean;
  onOpen: () => void;
}) {
  const t = useT();
  const age = relativeAge(row.createdAt);
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "flex w-full items-start gap-3 px-4 py-3 text-left transition",
        active ? "bg-primary/5" : "hover:bg-muted/30",
      )}
    >
      <span
        className="grid h-9 w-9 shrink-0 place-items-center rounded-md font-serif text-sm text-black"
        style={{ background: avatarColor(row.agentId) }}
        aria-hidden
      >
        {row.agentName.slice(0, 1).toUpperCase()}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          {row.kind === "clarification" ? (
            <HelpCircle className="h-3.5 w-3.5 shrink-0 text-primary" />
          ) : (
            <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-[color:var(--status-warning)]" />
          )}
          <span className="truncate text-sm font-medium">{row.title}</span>
          {row.amount && (
            <span className="shrink-0 rounded-full border border-border bg-background/60 px-1.5 py-0.5 font-mono text-[10px] tabular-nums">
              {row.amount}
            </span>
          )}
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
          <span className="truncate">{row.agentName}</span>
          {/* A department chip only where there is more than one department to
              tell apart — otherwise it is the same word on every row. */}
          {showDepartment && (
            <>
              <span>·</span>
              <span className="truncate">
                {row.departmentId
                  ? row.departmentName || t("Unnamed department", "Unbenannte Abteilung")
                  : t("Company-wide", "Unternehmensweit")}
              </span>
            </>
          )}
          {age && (
            <>
              <span>·</span>
              <span>{age}</span>
            </>
          )}
        </span>
      </span>
      <ChevronRight
        className={cn("mt-1 h-4 w-4 shrink-0", active ? "text-primary" : "text-muted-foreground")}
      />
    </button>
  );
}

// ------------------------------------------------------------- detail: approval

function ApprovalPane({
  approval,
  mayAct,
  viewOnlyBecause,
  busy,
  onDecide,
}: {
  approval: Approval;
  mayAct: boolean;
  viewOnlyBecause: ViewOnlyBecause;
  busy: boolean;
  onDecide: (
    a: Approval,
    decision: "approve" | "reject",
    reason: string,
    option: string | null,
  ) => void;
}) {
  const t = useT();
  const options = approval.options ?? [];
  // Pre-selected to the agent's own recommendation: the common case is agreeing
  // with it, and an operator who does not should have to notice, not to hunt.
  const [choice, setChoice] = useState<string | null>(
    approval.recommendation ?? (options.length === 1 ? options[0].key : null),
  );
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);

  const toolArguments = Object.entries(approval.toolArguments ?? {});
  // Truthiness, not `!= null`: `amount_text` is a free-text column and an empty
  // string is not an amount. Testing for null alone would render a big blank
  // where the number goes AND put a confirm dialog in front of an approval that
  // has no number on it at all.
  const hasAmount = !!approval.amount;
  // One more question, because this one has a number on it or because agreeing
  // sends something outward that cannot be taken back.
  const needsConfirm = hasAmount || approval.actionType === "tool_send";
  const canReject = reason.trim().length > 0;

  const approve = () => onDecide(approval, "approve", reason, choice);

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-6 px-6 py-5">
        {/* 1 — who and why */}
        <section>
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <span
              className="grid h-5 w-5 place-items-center rounded font-serif text-[10px] text-black"
              style={{ background: avatarColor(approval.agentId) }}
              aria-hidden
            >
              {(approval.agentName || "?").slice(0, 1).toUpperCase()}
            </span>
            <span className="font-medium text-foreground">
              {approval.agentName || approval.agentId}
            </span>
            {approval.departmentName && (
              <>
                <ArrowRight className="h-3 w-3" />
                <span>{approval.departmentName}</span>
              </>
            )}
            {approval.taskTitle && (
              <>
                <ArrowRight className="h-3 w-3" />
                <span className="truncate">{approval.taskTitle}</span>
              </>
            )}
          </div>
          <h2 className="mt-2 font-serif text-2xl leading-tight">{approval.title}</h2>
          {approval.detail && (
            <p className="mt-2 text-sm leading-relaxed text-foreground/90">{approval.detail}</p>
          )}
        </section>

        {/* 2 — what happens if you agree. The heart of the screen. */}
        {(approval.toolName || options.length > 0) && (
          <section>
            <div className="mb-2 text-[10px] uppercase tracking-widest text-muted-foreground">
              {t("What happens if you agree", "Was passiert, wenn du zustimmst")}
            </div>

            {approval.toolName && (
              <div className="rounded-md border border-border bg-panel/60">
                <div className="flex items-center gap-2 border-b border-border px-3 py-2">
                  <Send className="h-3.5 w-3.5 shrink-0 text-primary" />
                  <code className="truncate font-mono text-sm">{approval.toolName}</code>
                </div>
                {toolArguments.length > 0 ? (
                  <dl className="divide-y divide-border/60">
                    {toolArguments.map(([k, v]) => (
                      <div
                        key={k}
                        className="grid grid-cols-1 gap-0.5 px-3 py-2 sm:grid-cols-[minmax(0,10rem)_1fr] sm:gap-3"
                      >
                        <dt className="text-[10px] uppercase tracking-widest text-muted-foreground sm:pt-0.5">
                          {k}
                        </dt>
                        <dd className="min-w-0 font-mono text-xs">
                          <ArgumentValue value={v} />
                        </dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <div className="px-3 py-2 text-xs text-muted-foreground">
                    {t("Called with no arguments.", "Wird ohne Argumente aufgerufen.")}
                  </div>
                )}
              </div>
            )}

            {options.length > 0 && (
              <div className={cn("space-y-2", approval.toolName && "mt-3")}>
                {options.map((o) => {
                  const active = o.key === choice;
                  return (
                    <button
                      key={o.key}
                      type="button"
                      onClick={() => setChoice(o.key)}
                      className={cn(
                        "flex w-full items-start gap-3 rounded-md border px-3 py-2.5 text-left transition",
                        active
                          ? "border-primary/60 bg-primary/5"
                          : "border-border hover:bg-muted/30",
                      )}
                    >
                      <span
                        className={cn(
                          "mt-1 h-3 w-3 shrink-0 rounded-full border",
                          active ? "border-primary bg-primary" : "border-muted-foreground/50",
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2 text-sm font-medium">
                          {o.label}
                          {o.key === approval.recommendation && (
                            <span className="rounded-full border border-border px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
                              {t("agent's suggestion", "Vorschlag des Agenten")}
                            </span>
                          )}
                        </span>
                        {o.detail && (
                          <span className="mt-0.5 block text-xs text-muted-foreground">
                            {o.detail}
                          </span>
                        )}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </section>
        )}

        {/* 3 — the amount, large, only when there is one */}
        {hasAmount && (
          <section>
            <div className="text-[10px] uppercase tracking-widest text-muted-foreground">
              {t("Amount", "Betrag")}
            </div>
            <div className="mt-1 font-mono text-3xl tabular-nums">{approval.amount}</div>
          </section>
        )}

        {/* 4 — the reason, required to reject */}
        {mayAct && (
          <section>
            <label
              htmlFor="workspace-reason"
              className="mb-2 block text-[10px] uppercase tracking-widest text-muted-foreground"
            >
              {t("Reason", "Begründung")}
              <span className="ml-1 normal-case tracking-normal text-muted-foreground/80">
                {t("— required to reject", "— zum Ablehnen erforderlich")}
              </span>
            </label>
            <textarea
              id="workspace-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              placeholder={t(
                "e.g. discount too high for this customer",
                "z. B. Rabatt für diesen Kunden zu hoch",
              )}
              className="w-full resize-none rounded-md border border-border bg-background/40 px-3 py-2 text-sm outline-none focus:border-primary/50"
            />
          </section>
        )}
      </div>

      <footer className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t border-border bg-panel px-6 py-3">
        {!mayAct ? (
          <p className="text-xs text-muted-foreground">
            {viewOnlyBecause === "role"
              ? t(
                  "You can read every department, but decide in none — your role is read-only.",
                  "Du kannst alle Abteilungen einsehen, aber in keiner entscheiden — deine Rolle ist nur lesend.",
                )
              : t(
                  "You can read this one, but not decide it — your seat in this department is view-only.",
                  "Du kannst diesen Vorgang einsehen, aber nicht entscheiden — dein Platz in dieser Abteilung ist nur lesend.",
                )}
          </p>
        ) : (
          <>
            <button
              type="button"
              onClick={() => onDecide(approval, "reject", reason, null)}
              disabled={busy || !canReject}
              title={
                canReject
                  ? undefined
                  : t(
                      "Give a reason first — a rejection with none is a dead end for the agent that has to act on it",
                      "Bitte zuerst begründen — eine Ablehnung ohne Begründung ist eine Sackgasse für den Agenten, der damit weiterarbeiten muss",
                    )
              }
              className="inline-flex items-center gap-1.5 rounded-md border border-[color:var(--status-error)]/40 bg-[color:var(--status-error)]/10 px-3 py-2 text-sm text-[color:var(--status-error)] transition hover:brightness-110 disabled:opacity-40"
            >
              <XCircle className="h-4 w-4" /> {t("Reject", "Ablehnen")}
            </button>
            <button
              type="button"
              onClick={() => (needsConfirm ? setConfirming(true) : approve())}
              disabled={busy}
              className="ml-auto inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:brightness-110 glow-teal disabled:opacity-50"
            >
              <Check className="h-4 w-4" /> {t("Approve", "Zustimmen")}
            </button>
          </>
        )}
      </footer>

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("Approve this?", "Wirklich zustimmen?")}</AlertDialogTitle>
            <AlertDialogDescription>
              {hasAmount
                ? t(
                    `Approving releases ${approval.amount}. The agent carries on immediately and this cannot be taken back.`,
                    `Mit der Zustimmung werden ${approval.amount} freigegeben. Der Agent macht sofort weiter, und das lässt sich nicht zurücknehmen.`,
                  )
                : t(
                    `Approving lets the agent run ${approval.toolName ?? "this action"} now. It sends something outward and cannot be taken back.`,
                    `Mit der Zustimmung führt der Agent ${approval.toolName ?? "diese Aktion"} sofort aus. Dabei geht etwas nach außen, und das lässt sich nicht zurücknehmen.`,
                  )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("Cancel", "Abbrechen")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirming(false);
                approve();
              }}
            >
              {t("Yes, approve", "Ja, zustimmen")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** One argument value. Objects and arrays are shown as formatted JSON rather
 *  than "[object Object]" — the whole promise of this pane is that you see what
 *  will be sent, exactly as it will be sent. */
function ArgumentValue({ value }: { value: unknown }) {
  if (value === null || value === undefined)
    return <span className="text-muted-foreground">—</span>;
  if (typeof value === "object") {
    return (
      <pre className="overflow-x-auto whitespace-pre-wrap break-words text-xs leading-relaxed">
        {JSON.stringify(value, null, 2)}
      </pre>
    );
  }
  return <span className="break-words">{String(value)}</span>;
}

// -------------------------------------------------------- detail: clarification

function ClarificationPane({
  clarification,
  mayAct,
  viewOnlyBecause,
  busy,
  onAnswer,
}: {
  clarification: Clarification;
  mayAct: boolean;
  viewOnlyBecause: ViewOnlyBecause;
  busy: boolean;
  onAnswer: (c: Clarification, answer: string) => void;
}) {
  const t = useT();
  const [text, setText] = useState("");
  const canSend = text.trim().length > 0;

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-6 px-6 py-5">
        <section>
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <span
              className="grid h-5 w-5 place-items-center rounded font-serif text-[10px] text-black"
              style={{ background: avatarColor(clarification.agentId) }}
              aria-hidden
            >
              {(clarification.agentName || "?").slice(0, 1).toUpperCase()}
            </span>
            <span className="font-medium text-foreground">
              {clarification.agentName || clarification.agentId}
            </span>
            {clarification.departmentName && (
              <>
                <ArrowRight className="h-3 w-3" />
                <span>{clarification.departmentName}</span>
              </>
            )}
          </div>
          <div className="mt-2 text-[10px] uppercase tracking-widest text-muted-foreground">
            {t("Waiting on your answer", "Wartet auf deine Antwort")}
          </div>
          <p className="mt-1 text-sm text-foreground/90">{clarification.question}</p>
        </section>

        {mayAct && (
          <section>
            <label
              htmlFor="workspace-answer"
              className="mb-2 block text-[10px] uppercase tracking-widest text-muted-foreground"
            >
              {t("Your answer", "Deine Antwort")}
            </label>
            <textarea
              id="workspace-answer"
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={4}
              placeholder={t("Answer in your own words", "Antworte in eigenen Worten")}
              className="w-full resize-none rounded-md border border-border bg-background/40 px-3 py-2 text-sm outline-none focus:border-primary/50"
            />
          </section>
        )}
      </div>

      <footer className="sticky bottom-0 flex items-center justify-end gap-2 border-t border-border bg-panel px-6 py-3">
        {!mayAct ? (
          <p className="mr-auto text-xs text-muted-foreground">
            {viewOnlyBecause === "role"
              ? t(
                  "You can read every department's questions, but answer none — your role is read-only.",
                  "Du kannst die Fragen aller Abteilungen lesen, aber keine beantworten — deine Rolle ist nur lesend.",
                )
              : t(
                  "You can read this question, but not answer it — your seat in this department is view-only.",
                  "Du kannst diese Frage lesen, aber nicht beantworten — dein Platz in dieser Abteilung ist nur lesend.",
                )}
          </p>
        ) : (
          <button
            type="button"
            onClick={() => onAnswer(clarification, text)}
            disabled={busy || !canSend}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:brightness-110 glow-teal disabled:opacity-40"
          >
            <Check className="h-4 w-4" /> {t("Answer", "Antworten")}
          </button>
        )}
      </footer>
    </div>
  );
}
