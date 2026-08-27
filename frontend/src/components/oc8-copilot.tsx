import { Bot, ChevronDown, Send, ShieldAlert, Sparkles } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { api } from "@/lib/api";

export interface CopilotProposal {
  id: string;
  status: string;
  revision: number;
  operations: Array<{ label: string; references: Record<string, string> }>;
}

interface CopilotReply {
  text: string;
  missingFields?: string[];
  proposalId?: string | null;
}

const secretLike = /(api[_ -]?key|password|secret|token)\s*[:=]|sk-[a-z0-9_-]{8,}/i;

function isCritical(proposal: CopilotProposal): boolean {
  return proposal.operations.some((operation) =>
    ["plugin.enable", "integration.prepare"].includes(operation.label),
  );
}

export function CopilotProposalCard({
  proposal,
  onApply,
  onReject,
  applying = false,
}: {
  proposal: CopilotProposal;
  onApply: () => void;
  onReject: () => void;
  applying?: boolean;
}) {
  const critical = isCritical(proposal);
  const actions = (
    <div className="mt-4 flex flex-wrap gap-2">
      <button
        type="button"
        onClick={onReject}
        disabled={applying}
        className="rounded-md border border-border px-3 py-2 text-sm disabled:opacity-50"
      >
        Reject proposal
      </button>
      {critical ? (
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <button
              type="button"
              disabled={applying}
              className="rounded-md bg-amber-500 px-3 py-2 text-sm font-medium text-black disabled:opacity-50"
            >
              Review before applying
            </button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Apply critical change?</AlertDialogTitle>
              <AlertDialogDescription>
                This proposal changes a plugin or integration. It may affect other people’s work,
                but it never includes credentials.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                onClick={onApply}
                className="bg-amber-500 text-black hover:bg-amber-500/90"
              >
                Apply critical proposal
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      ) : (
        <button
          type="button"
          onClick={onApply}
          disabled={applying}
          className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          Apply proposal
        </button>
      )}
    </div>
  );
  return (
    <section
      aria-label="Copilot proposal"
      className="rounded-lg border border-primary/30 bg-primary/5 p-4"
    >
      <div className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-primary" />
        <h3 className="font-medium">Reviewable proposal</h3>
      </div>
      {critical && (
        <div className="mt-3 flex gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          <ShieldAlert className="h-4 w-4 shrink-0 text-amber-500" />
          <span>
            <strong>Critical impact:</strong> confirmation is required before this change can be
            applied.
          </span>
        </div>
      )}
      <ul className="mt-3 space-y-2">
        {proposal.operations.map((operation, index) => (
          <li
            key={`${operation.label}-${index}`}
            className="rounded-md border border-border bg-background/50 p-3"
          >
            <div className="font-mono text-xs text-foreground">{operation.label}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              {Object.entries(operation.references).map(([label, reference]) => (
                <span key={label} className="mr-3">
                  {label}: <code>{reference}</code>
                </span>
              ))}
            </div>
          </li>
        ))}
      </ul>
      {actions}
    </section>
  );
}

export function Oc8Copilot({ compact = false }: { compact?: boolean }) {
  const [open, setOpen] = useState(!compact);
  const [message, setMessage] = useState("");
  const [reply, setReply] = useState<string | null>(null);
  const [proposal, setProposal] = useState<CopilotProposal | null>(null);
  const [pending, setPending] = useState(false);
  const [rejected, setRejected] = useState(false);
  const messageFieldId = useId();
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (compact && open) inputRef.current?.focus();
  }, [compact, open]);

  const send = async () => {
    const safeMessage = message.trim();
    if (!safeMessage || pending) return;
    if (secretLike.test(safeMessage)) {
      toast.error("Do not share credentials with Copilot", {
        description: "Use the normal connection setup instead.",
      });
      return;
    }
    setPending(true);
    setRejected(false);
    try {
      // This payload is intentionally only the user's request and an optional
      // request. Credentials, connection settings and tool payloads do
      // not enter the conversation path.
      const result = await api.post<CopilotReply>("/copilot/chat", { message: safeMessage });
      setReply(result.text);
      setMessage("");
      if (result.proposalId)
        setProposal(await api.get<CopilotProposal>(`/copilot/proposals/${result.proposalId}`));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Copilot is unavailable");
    } finally {
      setPending(false);
    }
  };

  const apply = async () => {
    if (!proposal) return;
    setPending(true);
    try {
      const updated = await api.post<CopilotProposal>(`/copilot/proposals/${proposal.id}/apply`);
      setProposal(updated);
      toast.success("Proposal applied");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not apply proposal");
    } finally {
      setPending(false);
    }
  };

  const body = (
    <>
      {reply && (
        <div className="rounded-md border border-border bg-background/40 p-3 text-sm">{reply}</div>
      )}
      {pending && compact && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <img
            src="/octopus_oc8.svg"
            alt=""
            aria-hidden="true"
            className="h-6 w-6 shrink-0"
            draggable={false}
          />
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
      {proposal && !rejected && proposal.status === "draft" && (
        <CopilotProposalCard
          proposal={proposal}
          applying={pending}
          onApply={apply}
          onReject={() => {
            setRejected(true);
            toast.info("Proposal rejected in this review");
          }}
        />
      )}
      {proposal && proposal.status !== "draft" && (
        <div className="rounded-md border border-border p-3 text-sm">
          Proposal status: <strong>{proposal.status}</strong>
        </div>
      )}
      {rejected && (
        <div className="rounded-md border border-border p-3 text-sm text-muted-foreground">
          Proposal rejected. No change was applied.
        </div>
      )}
      <label className="block text-sm font-medium" htmlFor={messageFieldId}>
        What should change?
      </label>
      <textarea
        id={messageFieldId}
        ref={inputRef}
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        onKeyDown={(event) => {
          if (compact && event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            send();
          }
        }}
        rows={compact ? 2 : 3}
        placeholder="For example: prepare a weekly report trigger for this agent"
        className="w-full resize-none rounded-md border border-border bg-background/40 px-3 py-2 text-sm"
      />
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          Never paste passwords, API keys, tokens or connection settings here.
        </p>
        <button
          type="button"
          onClick={send}
          disabled={!message.trim() || pending}
          className="inline-flex shrink-0 items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          <Send className="h-4 w-4" /> {pending ? "Preparing…" : "Prepare proposal"}
        </button>
      </div>
    </>
  );

  if (compact && !open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="oc8 Copilot"
        className="group fixed bottom-5 right-5 z-50 grid h-14 w-14 place-items-center rounded-full border border-primary/30 bg-panel shadow-[0_16px_40px_-16px_oklch(0_0_0/90%)] transition hover:scale-105"
      >
        <img
          src="/octopus_oc8.svg"
          alt=""
          aria-hidden="true"
          className="h-9 w-9 select-none"
          draggable={false}
        />
        <span className="absolute -right-0.5 -top-0.5 grid h-4 w-4 place-items-center rounded-full bg-primary text-primary-foreground">
          <Sparkles className="h-2.5 w-2.5" />
        </span>
      </button>
    );
  }

  if (compact) {
    return (
      <div className="fixed bottom-24 right-5 z-50 flex h-[min(72vh,600px)] w-[min(94vw,400px)] flex-col overflow-hidden rounded-2xl border border-border bg-panel shadow-[0_30px_80px_-30px_oklch(0_0_0/80%)]">
        <header className="flex items-center gap-2.5 border-b border-border px-4 py-3">
          <img
            src="/octopus_oc8.svg"
            alt=""
            aria-hidden="true"
            className="h-8 w-8 shrink-0 select-none"
            draggable={false}
          />
          <div className="min-w-0 flex-1 leading-tight">
            <div className="font-serif text-base">oc8 Copilot</div>
            <p className="text-xs text-muted-foreground">
              Prepares proposals; a person reviews and applies every change.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close oc8 Copilot"
            className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-muted-foreground transition hover:bg-muted/40 hover:text-foreground"
          >
            <ChevronDown className="h-4 w-4" />
          </button>
        </header>
        <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">{body}</div>
      </div>
    );
  }

  return (
    <div className="relative w-full max-w-2xl space-y-4 rounded-xl border border-border bg-panel p-5 shadow-xl">
      <div className="flex items-start gap-3">
        <span className="grid h-9 w-9 place-items-center rounded-lg bg-primary/15 text-primary">
          <Bot className="h-5 w-5" />
        </span>
        <div>
          <h2 className="font-serif text-xl">Octopus Copilot</h2>
          <p className="text-sm text-muted-foreground">
            Describe a change. Copilot prepares a proposal; it never applies changes or accepts
            credentials in chat.
          </p>
        </div>
      </div>
      {body}
    </div>
  );
}
