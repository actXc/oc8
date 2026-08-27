// Direct 1:1 chat with a single agent. Reused verbatim in two places: the
// top-level /chat route (pick any agent you can see) and the agent detail
// page's own "Chat" tab (routes/agents.$id.tsx) -- same component, same
// mechanism, per the brainstormed design: a chat turn is a real AgentRun
// (source="chat"), so guardrails/approvals apply exactly as they do to an
// autonomous run.

import { useEffect, useRef, useState } from "react";
import { MessageSquare, Plus, Send } from "lucide-react";
import { Panel } from "@/components/app-shell";
import { ChatMarkdown } from "@/components/chat-markdown";
import { RUN_COMPONENT_REGISTRY } from "@/components/run-record-card";
import {
  useChatSessions,
  useCreateChatSession,
  useChatMessages,
  useSendChatMessage,
} from "@/lib/hooks-chat";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function ChatWindow({ agentId, agentName }: { agentId: string; agentName: string }) {
  const t = useT();
  const { data: sessions, isLoading: sessionsLoading } = useChatSessions(agentId);
  const createSession = useCreateChatSession();
  const [sessionId, setSessionId] = useState<string | null>(null);

  // Default to the most recent session once sessions load. Never runs again
  // once the reader has one selected -- including a brand new one just
  // created below -- so this can't clobber an explicit choice.
  useEffect(() => {
    if (sessionId !== null) return;
    if (sessions && sessions.length > 0) setSessionId(sessions[0].id);
  }, [sessions, sessionId]);

  const { data: messages, isLoading: messagesLoading } = useChatMessages(sessionId);
  const sendMessage = useSendChatMessage(sessionId ?? "");
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && typeof el.scrollTo === "function") el.scrollTo({ top: el.scrollHeight });
  }, [messages]);

  function startNewSession() {
    createSession.mutate(agentId, { onSuccess: (session) => setSessionId(session.id) });
  }

  function submit() {
    const trimmed = draft.trim();
    if (!trimmed || !sessionId) return;
    setDraft("");
    sendMessage.mutate(trimmed);
  }

  // The transcript's own state IS the "is the agent still working" signal --
  // see useChatMessages's poll-or-stop doc comment. No separate flag needed.
  const waitingOnAgent =
    !!messages && messages.length > 0 && messages[messages.length - 1].role === "user";

  return (
    <Panel className="flex h-[560px] flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="text-sm font-medium">
          {t(`Chat with ${agentName}`, `Chat mit ${agentName}`)}
        </div>
        <div className="flex items-center gap-2">
          {sessions && sessions.length > 0 && (
            <select
              value={sessionId ?? ""}
              onChange={(e) => setSessionId(e.target.value || null)}
              className="rounded-md border border-border bg-background/40 px-2 py-1 text-xs outline-none"
            >
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title || t("Untitled chat", "Unbenannter Chat")} ·{" "}
                  {new Date(s.createdAt).toLocaleDateString()}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            onClick={startNewSession}
            disabled={createSession.isPending}
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground transition hover:text-foreground disabled:opacity-50"
          >
            <Plus className="h-3.5 w-3.5" />
            {t("New chat", "Neuer Chat")}
          </button>
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {sessionsLoading ? (
          <div className="py-10 text-center text-xs text-muted-foreground">
            {t("Loading…", "Wird geladen…")}
          </div>
        ) : !sessionId ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 py-10 text-center">
            <MessageSquare className="h-6 w-6 text-muted-foreground/60" />
            <p className="text-sm text-muted-foreground">
              {t(
                `Start a direct chat with ${agentName}.`,
                `Starte einen direkten Chat mit ${agentName}.`,
              )}
            </p>
            <button
              type="button"
              onClick={startNewSession}
              disabled={createSession.isPending}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
            >
              <Plus className="h-3.5 w-3.5" />
              {t("Start chat", "Chat starten")}
            </button>
          </div>
        ) : messagesLoading ? (
          <div className="py-10 text-center text-xs text-muted-foreground">
            {t("Loading…", "Wird geladen…")}
          </div>
        ) : !messages || messages.length === 0 ? (
          <div className="py-10 text-center text-xs text-muted-foreground">
            {t("Say hello to get started.", "Sag Hallo, um loszulegen.")}
          </div>
        ) : (
          messages.map((m) => (
            <div
              key={m.id}
              className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}
            >
              <div
                className={cn(
                  "max-w-[80%] space-y-2 rounded-lg px-3 py-2 text-sm",
                  m.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : "border border-border bg-background/60",
                )}
              >
                {m.role === "user" ? (
                  <div className="whitespace-pre-wrap">{m.content}</div>
                ) : (
                  <ChatMarkdown text={m.content} />
                )}
                {m.renderedComponents.length > 0 && (
                  <div className="space-y-2">
                    {m.renderedComponents.map((c, i) => {
                      const Renderer = Object.prototype.hasOwnProperty.call(
                        RUN_COMPONENT_REGISTRY,
                        c.componentKey,
                      )
                        ? RUN_COMPONENT_REGISTRY[c.componentKey]
                        : undefined;
                      return Renderer ? <Renderer key={i} props={c.props} /> : null;
                    })}
                  </div>
                )}
              </div>
            </div>
          ))
        )}
        {waitingOnAgent && (
          <div className="flex justify-start">
            <div className="max-w-[80%] rounded-lg border border-border bg-background/60 px-3 py-2 text-xs text-muted-foreground">
              {t(`${agentName} is thinking…`, `${agentName} überlegt…`)}
            </div>
          </div>
        )}
      </div>

      {sessionId && (
        <div className="flex items-end gap-2 border-t border-border px-4 py-3">
          <textarea
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={t("Type a message…", "Nachricht eingeben…")}
            className="max-h-32 flex-1 resize-none rounded-md border border-border bg-background/40 px-3 py-2 text-sm outline-none focus:border-primary/50"
          />
          <button
            type="button"
            onClick={submit}
            disabled={sendMessage.isPending || waitingOnAgent || !draft.trim()}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </Panel>
  );
}
