// One grid tile bound to its own chat session -- unlike the floating dock's
// tabs, a grid tile never unmounts while the page is open, so `active` is
// always true and draft text is scoped to this component's own state
// rather than lifted to a shared tabs manager.
import { useState } from "react";
import { Plus } from "lucide-react";
import { CopilotChatTab } from "@/components/copilot-dock";
import { ChatSessionPicker } from "@/components/chat-window";
import { useAssistant } from "@/lib/hooks";
import { useChatSessions } from "@/lib/hooks-chat";
import { useT } from "@/lib/i18n";

export function ChatWidget({
  config,
  onConfigChange,
}: {
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
}) {
  const t = useT();
  const [draft, setDraft] = useState("");
  const { data: assistant } = useAssistant();
  const assistantAgentId = assistant?.agentId;
  const { data: sessions } = useChatSessions(assistantAgentId);
  const sessionId = typeof config.sessionId === "string" ? config.sessionId : null;

  function setSessionId(id: string | null) {
    onConfigChange({ ...config, sessionId: id });
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-border px-2 py-1">
        {assistantAgentId && (
          <button
            type="button"
            onClick={() => setSessionId(null)}
            aria-label={t("New chat", "Neuer Chat")}
            title={t("New chat", "Neuer Chat")}
            className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted-foreground transition hover:bg-muted/40 hover:text-foreground"
          >
            <Plus className="h-4 w-4" />
          </button>
        )}
        {assistantAgentId && sessions && sessions.length > 0 && (
          <ChatSessionPicker
            agentId={assistantAgentId}
            sessions={sessions}
            sessionId={sessionId}
            onSelect={setSessionId}
          />
        )}
      </div>
      <div className="flex flex-1 flex-col overflow-hidden">
        <CopilotChatTab
          active
          sessionId={sessionId}
          onSessionChange={setSessionId}
          assistantAgentId={assistantAgentId}
          draft={draft}
          onDraftChange={setDraft}
        />
      </div>
    </div>
  );
}
