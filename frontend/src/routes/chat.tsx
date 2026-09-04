import { createFileRoute } from "@tanstack/react-router";
import { Search, Users } from "lucide-react";
import { useState } from "react";
import { Panel } from "@/components/app-shell";
import { ChatWindow } from "@/components/chat-window";
import { useAgents } from "@/lib/hooks";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/chat")({
  component: ChatPage,
});

function ChatPage() {
  const t = useT();
  const [search, setSearch] = useState("");
  // `/agents` is already scoped to what the caller's role/seats can see (same
  // permission the Agents list itself is gated on) -- the agent picker here
  // shows exactly the set the reader could otherwise open from /agents.
  const { data: agentsPage, isLoading } = useAgents({ search, pageSize: 200 });
  const agents = agentsPage?.items ?? [];
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = agents.find((a) => a.id === selectedId) ?? null;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{t("Chat", "Chat")}</h1>
        <p className="text-sm text-muted-foreground">
          {t(
            "Chat directly with any agent you have access to.",
            "Chatte direkt mit jedem Agenten, auf den du Zugriff hast.",
          )}
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-[280px_1fr]">
        <Panel className="flex h-[560px] flex-col overflow-hidden">
          <div className="border-b border-border p-3">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t("Search agents…", "Agenten suchen…")}
                className="w-full rounded-md border border-border bg-background/40 py-1.5 pl-8 pr-2 text-sm outline-none focus:border-primary/50"
              />
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {isLoading ? (
              <div className="py-10 text-center text-xs text-muted-foreground">
                {t("Loading…", "Wird geladen…")}
              </div>
            ) : agents.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-10 text-center text-xs text-muted-foreground">
                <Users className="h-5 w-5 text-muted-foreground/60" />
                {t("No agents found.", "Keine Agenten gefunden.")}
              </div>
            ) : (
              agents.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  onClick={() => setSelectedId(a.id)}
                  className={cn(
                    "flex w-full flex-col items-start gap-0.5 rounded-md px-3 py-2 text-left text-sm transition-colors",
                    a.id === selectedId
                      ? "bg-primary/10 text-primary"
                      : "text-foreground/90 hover:bg-accent",
                  )}
                >
                  <span className="truncate font-medium">{a.name}</span>
                  <span className="truncate text-xs text-muted-foreground">{a.role}</span>
                </button>
              ))
            )}
          </div>
        </Panel>

        {selected ? (
          <ChatWindow key={selected.id} agentId={selected.id} agentName={selected.name} />
        ) : (
          <Panel className="flex h-[560px] items-center justify-center p-10 text-center">
            <p className="text-sm text-muted-foreground">
              {t(
                "Pick an agent on the left to start chatting.",
                "Wähle links einen Agenten aus, um zu chatten.",
              )}
            </p>
          </Panel>
        )}
      </div>
    </div>
  );
}
