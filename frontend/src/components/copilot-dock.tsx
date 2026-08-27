import { ChevronDown, Check, Send, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";
import { useCan } from "@/lib/governance-hooks";
import {
  useApplyCopilotProposal,
  useCopilotChat,
  useCopilotProposal,
  useRejectCopilotProposal,
} from "@/lib/hooks";

// Set by DoneStep right before the full-page navigation into "/" that follows
// onboarding — sessionStorage (not React state) because that navigation
// remounts the whole app, so nothing in memory survives it.
export const COPILOT_AUTO_OPEN_KEY = "oc8-copilot-auto-open";

interface Msg {
  id: string;
  role: "user" | "copilot";
  text: string;
  proposalId?: string | null;
}

function greeting(de: boolean): string {
  return de
    ? "Hi, ich bin der oc8 Copilot. Ich kenne deine Agenten, Abteilungen und Guardrails — frag mich alles oder lass mich etwas konfigurieren."
    : "Hi, I'm the oc8 copilot. I know your agents, departments and guardrails — ask me anything or let me configure something.";
}

function ProposalCard({ proposalId }: { proposalId: string }) {
  const t = useT();
  const { data: proposal } = useCopilotProposal(proposalId);
  const apply = useApplyCopilotProposal();
  const reject = useRejectCopilotProposal();

  if (!proposal) return null;
  // Backend state machine (oc8/copilot/proposals.py): draft -> applied |
  // rejected | expired. "draft" is the only actionable state -- there is no
  // "pending" status.
  const decided = proposal.status !== "draft";

  return (
    <div className="mt-1 max-w-[88%] rounded-xl border border-border bg-background/40 p-3 text-xs">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="font-medium text-foreground">
          {t("Proposed change", "Vorgeschlagene Änderung")}
        </span>
        <span
          className={cn(
            "rounded-full px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
            proposal.status === "applied" && "bg-[color:var(--status-running)]/15 text-[color:var(--status-running)]",
            (proposal.status === "rejected" || proposal.status === "expired") &&
              "bg-muted/40 text-muted-foreground",
            proposal.status === "draft" && "bg-[color:var(--status-warning)]/15 text-[color:var(--status-warning)]",
          )}
        >
          {proposal.status}
        </span>
      </div>
      <ul className="space-y-1 text-muted-foreground">
        {proposal.operations.map((op, i) => {
          const refPairs = Object.entries(op.references);
          return (
            <li key={i}>
              {op.label}
              {refPairs.length > 0
                ? ` — ${refPairs.map(([k, v]) => `${k}: ${v}`).join(", ")}`
                : ""}
            </li>
          );
        })}
      </ul>
      {!decided && (
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            onClick={() => apply.mutate(proposalId)}
            disabled={apply.isPending || reject.isPending}
            className="inline-flex items-center gap-1 rounded-md bg-primary px-2 py-1 text-[11px] font-medium text-primary-foreground transition hover:brightness-110 disabled:opacity-60"
          >
            <Check className="h-3 w-3" />
            {t("Apply", "Übernehmen")}
          </button>
          <button
            type="button"
            onClick={() => reject.mutate(proposalId)}
            disabled={apply.isPending || reject.isPending}
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] text-muted-foreground transition hover:text-foreground disabled:opacity-60"
          >
            <X className="h-3 w-3" />
            {t("Reject", "Ablehnen")}
          </button>
        </div>
      )}
    </div>
  );
}

export function CopilotDock() {
  const t = useT();
  const can = useCan();
  const de = t("en", "de") === "de";
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([{ id: "m0", role: "copilot", text: greeting(de) }]);
  const chat = useCopilotChat();
  const scroller = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const mayUseCopilot = can("copilot:manage");

  useEffect(() => {
    if (!mayUseCopilot) return;
    if (window.sessionStorage.getItem(COPILOT_AUTO_OPEN_KEY) === "1") {
      window.sessionStorage.removeItem(COPILOT_AUTO_OPEN_KEY);
      setOpen(true);
    }
  }, [mayUseCopilot]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [msgs, chat.isPending, open]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  function send() {
    const text = input.trim();
    if (!text || chat.isPending) return;
    setMsgs((p) => [...p, { id: `u${p.length}`, role: "user", text }]);
    setInput("");
    chat.mutate(text, {
      onSuccess: (reply) => {
        setMsgs((p) => [
          ...p,
          { id: `c${p.length}`, role: "copilot", text: reply.text, proposalId: reply.proposalId },
        ]);
        inputRef.current?.focus();
      },
      onError: () => {
        setMsgs((p) => [
          ...p,
          {
            id: `c${p.length}`,
            role: "copilot",
            text: de
              ? "Der Copilot ist gerade nicht erreichbar. Versuch es gleich noch einmal."
              : "The copilot is unavailable right now. Try again in a moment.",
          },
        ]);
      },
    });
  }

  // Only org_admin holds copilot:manage (a prepared proposal can name any
  // agent in any department) — everyone else never sees the dock at all.
  if (!mayUseCopilot) return null;

  const suggestions = de
    ? ["Was wartet auf Freigabe?", "Kosten diesen Monat?", "Neuen Agenten anlegen"]
    : ["What needs approval?", "Cost this month?", "Create a new agent"];

  return (
    <>
      {open && (
        <div className="fixed bottom-24 right-5 z-50 flex h-[min(72vh,600px)] w-[min(94vw,400px)] flex-col overflow-hidden rounded-2xl border border-border bg-panel shadow-[0_30px_80px_-30px_oklch(0_0_0/80%)]">
          <header className="flex items-center gap-2.5 border-b border-border px-4 py-3">
            <img src="/octopus_oc8.svg" alt="" className="h-8 w-8 shrink-0 select-none" draggable={false} />
            <div className="min-w-0 flex-1 leading-tight">
              <div className="font-serif text-base lowercase">oc8 copilot</div>
              <div className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <span className="h-1.5 w-1.5 rounded-full bg-[color:var(--status-running)] shadow-[0_0_8px_var(--status-running)]" />
                {de ? "bereit" : "ready"}
              </div>
            </div>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label={de ? "Copilot schließen" : "Close copilot"}
              className="grid h-8 w-8 place-items-center rounded-md text-muted-foreground transition hover:bg-muted/40 hover:text-foreground"
            >
              <ChevronDown className="h-4 w-4" />
            </button>
          </header>

          <div ref={scroller} className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
            {msgs.map((m) =>
              m.role === "user" ? (
                <div key={m.id} className="flex justify-end">
                  <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3 py-2 text-sm text-primary-foreground">
                    {m.text}
                  </div>
                </div>
              ) : (
                <div key={m.id} className="flex flex-col gap-1">
                  <div className="flex gap-2">
                    <img
                      src="/octopus_oc8.svg"
                      alt=""
                      className="mt-0.5 h-6 w-6 shrink-0"
                      draggable={false}
                    />
                    <p className="max-w-[88%] text-sm leading-relaxed text-foreground/90">{m.text}</p>
                  </div>
                  {m.proposalId && (
                    <div className="ml-8">
                      <ProposalCard proposalId={m.proposalId} />
                    </div>
                  )}
                </div>
              ),
            )}
            {chat.isPending && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <img src="/octopus_oc8.svg" alt="" className="h-6 w-6 shrink-0" draggable={false} />
                <span className="inline-flex gap-1">
                  {[0, 1, 2].map((i) => (
                    <span
                      key={i}
                      className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary"
                      style={{ animationDelay: `${i * 150}ms` }}
                    />
                  ))}
                </span>
              </div>
            )}
          </div>

          {msgs.length <= 1 && (
            <div className="flex flex-wrap gap-1.5 px-4 pb-2">
              {suggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => {
                    setInput(s);
                    inputRef.current?.focus();
                  }}
                  className="rounded-full border border-border bg-background/40 px-2.5 py-1 text-[11px] text-muted-foreground transition hover:text-foreground"
                >
                  {s}
                </button>
              ))}
            </div>
          )}

          <div className="border-t border-border p-3">
            <div className="flex items-end gap-2 rounded-xl border border-border bg-background/40 px-3 py-2 focus-within:border-primary/50">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                rows={1}
                placeholder={de ? "oc8 konfigurieren oder fragen…" : "Configure or ask oc8…"}
                className="max-h-28 min-h-[24px] flex-1 resize-none bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              />
              <button
                type="button"
                onClick={send}
                disabled={!input.trim() || chat.isPending}
                aria-label={de ? "Senden" : "Send"}
                className={cn(
                  "grid h-8 w-8 shrink-0 place-items-center rounded-lg transition",
                  input.trim() && !chat.isPending
                    ? "bg-primary text-primary-foreground hover:brightness-110"
                    : "bg-muted/40 text-muted-foreground",
                )}
              >
                <Send className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        </div>
      )}

      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label={de ? "oc8 Copilot" : "oc8 copilot"}
        className="group fixed bottom-5 right-5 z-50 grid h-14 w-14 place-items-center rounded-full border border-primary/30 bg-panel shadow-[0_16px_40px_-16px_oklch(0_0_0/90%)] transition hover:scale-105"
      >
        {open ? (
          <X className="h-5 w-5 text-muted-foreground" />
        ) : (
          <>
            <img
              src="/octopus_oc8.svg"
              alt=""
              className="h-9 w-9 select-none"
              draggable={false}
            />
            <span className="absolute -right-0.5 -top-0.5 grid h-4 w-4 place-items-center rounded-full bg-primary text-primary-foreground">
              <Sparkles className="h-2.5 w-2.5" />
            </span>
          </>
        )}
      </button>
    </>
  );
}
