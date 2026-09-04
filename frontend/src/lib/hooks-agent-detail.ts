// TanStack Query hooks for the agent detail page. Split from hooks.ts to
// avoid colliding with in-flight edits there — this module owns its own
// query functions but reuses the ["agents", id] key that hooks.ts and the
// live event patcher (live/apply-event.ts, "agent.status") already target,
// so this data stays live without any changes to the shared file.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ToolPolicy } from "@/lib/hooks";

// Mirrors backend AgentDetailDTO (backend/src/oc8/schemas/dto.py), camelCase
// over the wire via CamelModel. Extends the AgentDTO fields inline.
export interface AgentDetail {
  id: string;
  name: string;
  role: string;
  llm: string;
  provider: string;
  status: string; // running | warning | error | paused | waiting_for_task (see mapAgentStatus in live/apply-event.ts)
  tools: string[];
  lastAction: string;
  lastRun: string;
  tasksToday: number;
  guardrails: string[];
  schedule: string;
  avatarColor: string;
  departmentId: string | null;
  modelConfigId: string | null;
  isLead: boolean;
  mission: string;
  departmentName: string | null;
  effectiveTools: Record<string, ToolPolicy>;
  departmentFrameTools: Record<string, ToolPolicy>;
  // The agent's own narrowing["tools"] row, verbatim -- not intersected with
  // role_rights the way effectiveTools is. Use this (falling back to
  // departmentFrameTools), never effectiveTools, when resaving fields a
  // panel doesn't itself edit: effectiveTools dips whenever role_rights
  // does, and re-persisting that dip bakes it into narrowing permanently.
  narrowingTools: Record<string, ToolPolicy>;
  runtimeRef: string | null;
  // The run this agent is on right now, whoever started it. Without it the live
  // log can only follow a run started in this browser tab, so a scheduled run
  // happens invisibly.
  currentRunId: string | null;
}

// Reuses the ["agents", id] key already invalidated by the "agent.status"
// live patcher in live/apply-event.ts, so this query stays live.
export function useAgent(agentId: string) {
  return useQuery({
    queryKey: ["agents", agentId],
    queryFn: () => api.get<AgentDetail>(`/agents/${agentId}`),
    enabled: !!agentId,
  });
}

// PUT /agents/{id}/narrowing expects { narrowing: Record<string, unknown> }
// (NarrowingRequest in backend/src/oc8/schemas/requests.py:23).
export function useUpdateNarrowing(agentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { narrowing: Record<string, unknown> }) =>
      api.put<AgentDetail>(`/agents/${agentId}/narrowing`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents", agentId] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

// PUT /agents/{id}/runtime expects { runtimePluginId: string | null }
// (RuntimeAssignRequest in backend/src/oc8/schemas/requests.py:104) -- an
// absent/null id means "clear, use the built-in default", never "leave
// unchanged". Returns the updated AgentDetail; the ["agents", id] cache is
// still explicitly invalidated (not just replaced from the response) so any
// other component reading this query key re-renders too.
export function useUpdateAgentRuntime(agentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { runtimePluginId: string | null }) =>
      api.put<AgentDetail>(`/agents/${agentId}/runtime`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents", agentId] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}
