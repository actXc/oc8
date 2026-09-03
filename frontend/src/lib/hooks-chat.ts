// Direct 1:1 chat with a single agent (oc8.chat.service on the backend).
// Every message is a real AgentRun under the hood, so guardrails/approvals
// apply exactly as they do to an autonomous run -- this file only has to
// manage the session/message list and the "is the agent still thinking"
// poll, not any of that.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { RunComponentDTO } from "@/lib/hooks";

export interface ChatSessionDTO {
  id: string;
  agentId: string;
  title: string;
  createdAt: string;
  lastMessageAt: string | null;
}

export interface ChatMessageDTO {
  id: string;
  sessionId: string;
  role: "user" | "assistant";
  content: string;
  runId: string | null;
  renderedComponents: RunComponentDTO[];
  createdAt: string;
}

const keys = {
  sessions: (agentId?: string) => ["chat", "sessions", agentId ?? "all"] as const,
  messages: (sessionId: string) => ["chat", "messages", sessionId] as const,
};

export function useChatSessions(agentId?: string) {
  return useQuery({
    queryKey: keys.sessions(agentId),
    queryFn: () =>
      api.get<ChatSessionDTO[]>(`/chat/sessions${agentId ? `?agentId=${agentId}` : ""}`),
  });
}

export function useCreateChatSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (agentId: string) => api.post<ChatSessionDTO>("/chat/sessions", { agentId }),
    onSuccess: (session) => {
      qc.invalidateQueries({ queryKey: keys.sessions(session.agentId) });
      qc.invalidateQueries({ queryKey: keys.sessions() });
    },
  });
}

// Polls only while the transcript's last turn is the user's own -- i.e. the
// run answering it hasn't reached a terminal state yet. There is no WS event
// dedicated to chat (a run's "run.status" event doesn't know it's answering
// a chat session), so this is the whole "is the agent still typing" signal.
// Stops the moment an assistant turn lands, same shape as
// knowledge-connector-hooks.ts's useIngestionJob poll-or-stop.
export function useChatMessages(sessionId: string | null) {
  return useQuery({
    queryKey: keys.messages(sessionId ?? ""),
    queryFn: () => api.get<ChatMessageDTO[]>(`/chat/sessions/${sessionId}/messages`),
    enabled: !!sessionId,
    refetchInterval: (query) => {
      const data = query.state.data as ChatMessageDTO[] | undefined;
      if (!data || data.length === 0) return false;
      return data[data.length - 1].role === "user" ? 2000 : false;
    },
  });
}

export function useRenameChatSession(agentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, title }: { sessionId: string; title: string }) =>
      api.patch<ChatSessionDTO>(`/chat/sessions/${sessionId}`, { title }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.sessions(agentId) });
      qc.invalidateQueries({ queryKey: keys.sessions() });
    },
  });
}

export function useDeleteChatSession(agentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => api.delete<void>(`/chat/sessions/${sessionId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.sessions(agentId) });
      qc.invalidateQueries({ queryKey: keys.sessions() });
    },
  });
}

export function useSendChatMessage(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (message: string) =>
      api.post<ChatMessageDTO>(`/chat/sessions/${sessionId}/messages`, { message }),
    onSuccess: (message) => {
      qc.setQueryData<ChatMessageDTO[]>(keys.messages(sessionId), (prev) => [
        ...(prev ?? []),
        message,
      ]);
      qc.invalidateQueries({ queryKey: keys.sessions(message.sessionId) });
    },
  });
}
