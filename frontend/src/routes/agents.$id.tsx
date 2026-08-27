import { createFileRoute, Link } from "@tanstack/react-router";
import {
  ArrowLeft,
  AlertTriangle,
  BookOpen,
  Check,
  CheckCircle2,
  Clock,
  Copy,
  Crown,
  FileText,
  MessageSquare,
  Play,
  Save,
  Search,
  Shield,
  ShieldCheck,
  Trash2,
  UserCheck,
  Sparkles,
  Plus,
  Webhook,
  X,
  Wrench,
  XCircle,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  useActivity,
  useAgents,
  useAgentSupervisor,
  useAgentTriggers,
  useAgentWorkspaceFile,
  useAgentWorkspaceFiles,
  useAnswerRun,
  useCreateAgentTrigger,
  useCreateGrant,
  useDeleteAgentTrigger,
  useKnowledgeBases,
  useRun,
  useRunAgent,
  useSetAgentSupervisor,
  useSkills,
  useModels,
  useSwitchAgentModel,
  useUpdateAgentTrigger,
  useMcpConnections,
  useMcpLogins,
  useCreateMcpLogin,
  type McpLoginDTO,
  type ModelDTO,
  type RunDTO,
  type TriggerDTO,
} from "@/lib/hooks";
import { CredentialPicker } from "@/components/credential-picker";
import { GuardrailPresetPicker, type GuardrailValue } from "@/components/guardrail-preset-picker";
import { SUBSCRIPTION_PROVIDER, SubscriptionRiskBadge } from "@/routes/models";
import { CronBuilder } from "@/components/cron-builder";
import {
  useAgent,
  useAssignSkill,
  useUpdateNarrowing,
  type AgentDetail as AgentDetailData,
} from "@/lib/hooks-agent-detail";
import { setViewedAgent } from "@/lib/live/toast-for-event";
import { Panel, StatusPill } from "@/components/app-shell";
import {
  type Agent,
  type AgentStatus,
  type ActivityItem,
  type KnowledgeBase,
  type Supervisor,
} from "@/lib/mock-data";
import { type Skill } from "@/lib/skills";
import { AgentRuntimePanel } from "@/components/agent-runtime-panel";
import { ChatWindow } from "@/components/chat-window";
import { AgentInstructionsPanel } from "@/components/agent-instructions-panel";
import { KnowledgeAssignment } from "@/components/knowledge-assignment";
import { RUN_COMPONENT_REGISTRY } from "@/components/run-record-card";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";
import { useMayManageAgent } from "@/lib/governance-hooks";

// Mirrors mapAgentStatus in src/lib/live/apply-event.ts (not exported there).
// Handles BOTH vocabularies: the WS "agent.status" event carries the raw
// backend column ("idle", "waiting_for_approval", ...), while the REST
// agent endpoints already pre-map through AGENT_STATUS_TO_UI
// (backend/src/oc8/api/v1/_serializers.py) and send "waiting_for_task"
// directly -- so an already-mapped value must pass through unchanged
// rather than fall into the "anything else" bucket below.
function mapAgentStatus(s: string): AgentStatus {
  if (s === "running") return "running";
  if (s === "error") return "error";
  if (s === "idle" || s === "waiting_for_task") return "waiting_for_task";
  if (s === "paused" || s === "waiting_for_approval" || s === "pending_approval") return "paused";
  // stopped | anything else -> paused
  return "paused";
}

export const Route = createFileRoute("/agents/$id")({
  component: AgentDetail,
  notFoundComponent: () => (
    <div className="p-8 text-center text-muted-foreground">Agent not found.</div>
  ),
});

const TAB_IDS = [
  "overview",
  "instructions",
  "guardrails",
  "livelog",
  "chat",
  "files",
  "config",
  "skills",
  "memory",
  "history",
] as const;
type TabId = (typeof TAB_IDS)[number];

function AgentDetail() {
  const t = useT();
  const { id } = Route.useParams();
  const { data: agent, isLoading } = useAgent(id);
  // Suppress toasts (Toast v2) for events about this agent while its Live Log
  // is open — the user is already watching it live.
  useEffect(() => {
    setViewedAgent(id);
    return () => setViewedAgent(null);
  }, [id]);
  // KB-assignment picker for this agent, not a paginated list view.
  const { data: allBasesPage } = useKnowledgeBases({ pageSize: 200 });
  const allBases = allBasesPage?.items ?? [];
  const runAgent = useRunAgent();
  const { data: models = [] } = useModels();
  // WRITE authority for THIS agent — tenant-wide `agent:manage`, OR a live seat
  // in the agent's OWN department carrying the `agentManage` toggle. Neither
  // is the `agent:view` that put the page on the screen. Gates all three
  // agent:manage-reachable controls below: the narrowing editor's Save, the
  // skill-assign button, and the model select.
  const mayManage = useMayManageAgent()(agent?.departmentId);
  const [tab, setTab] = useState<TabId>("overview");
  // Id of the most recently enqueued run for this agent (drives the Live Log
  // transcript below via useRun). Resets on page load — the user re-runs to
  // reattach to a fresh run; older runs remain visible in History.
  const [runId, setRunId] = useState<string | null>(null);
  const [runPickerOpen, setRunPickerOpen] = useState(false);
  const [runTask, setRunTask] = useState("");
  // Which knowledge bases are enabled directly on this agent. Seeded from the
  // real bases (useKnowledgeBases) once both the agent and the bases resolve.
  const [agentKbs, setAgentKbs] = useState<string[]>([]);
  useEffect(() => {
    if (!agent) return;
    setAgentKbs(allBases.filter((kb) => kb.linkedAgents.includes(agent.id)).map((kb) => kb.id));
  }, [agent, allBases]);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Link
          to="/agents"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary"
        >
          <ArrowLeft className="h-4 w-4" /> {t("Back to agents", "Zurück zu Agenten")}
        </Link>
        <Panel className="p-8 text-center text-sm text-muted-foreground">
          {t("Loading agent…", "Agent wird geladen…")}
        </Panel>
      </div>
    );
  }

  if (!agent) {
    return (
      <div className="space-y-6">
        <Link
          to="/agents"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary"
        >
          <ArrowLeft className="h-4 w-4" /> {t("Back to agents", "Zurück zu Agenten")}
        </Link>
        <Panel className="p-8 text-center text-sm text-muted-foreground">
          {t(
            "Agent not found, or you don't have access.",
            "Agent nicht gefunden oder kein Zugriff.",
          )}
        </Panel>
      </div>
    );
  }

  const status = mapAgentStatus(agent.status);
  const deptEnabled = agent.departmentId
    ? allBases.filter((kb) => kb.linkedDepartments.includes(agent.departmentId!)).map((kb) => kb.id)
    : [];

  const tabs: { id: TabId; label: string }[] = [
    { id: "overview", label: t("Overview", "Übersicht") },
    { id: "instructions", label: t("Instructions", "Anweisungen") },
    { id: "guardrails", label: t("Guardrails", "Guardrails") },
    { id: "livelog", label: t("Live Log", "Live-Log") },
    { id: "chat", label: t("Chat", "Chat") },
    { id: "files", label: t("Files", "Dateien") },
    { id: "config", label: t("Configuration", "Konfiguration") },
    { id: "skills", label: t("Skills", "Skills") },
    { id: "memory", label: t("Memory", "Gedächtnis") },
    { id: "history", label: t("History", "Verlauf") },
  ];

  function submitRun() {
    // Task is optional -- the agent's own Instructions (mission) are already
    // sent as its standing system prompt on every run (agent/preamble.py),
    // so a blank field isn't a blank run. This mirrors the placeholder
    // ScheduleEditor/new-agent-dialog.tsx already send for cron-triggered
    // runs, which have no human typing a task either.
    const task =
      runTask.trim() || t(`Manual run for ${agent!.name}`, `Manueller Lauf für ${agent!.name}`);
    runAgent.mutate(
      { agentId: agent!.id, task },
      {
        onSuccess: (data: RunDTO) => {
          setRunId(data.id);
          setRunPickerOpen(false);
          setRunTask("");
          setTab("livelog");
          toast.success(t(`Run started · ${agent!.name}`, `Lauf gestartet · ${agent!.name}`));
        },
        onError: () => toast.error(t("Couldn't start run", "Lauf konnte nicht gestartet werden")),
      },
    );
  }

  return (
    <div className="space-y-6">
      <Link
        to="/agents"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary"
      >
        <ArrowLeft className="h-4 w-4" /> {t("Back to agents", "Zurück zu Agenten")}
      </Link>

      <Panel className="p-6">
        <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-4 sm:flex sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-center gap-4">
            <div className="relative shrink-0">
              <div
                className="grid h-14 w-14 place-items-center rounded-xl font-serif text-2xl text-black"
                style={{ background: agent.avatarColor }}
              >
                {agent.name[0]}
              </div>
              {agent.isLead && (
                <Crown
                  className="absolute -top-2 -right-2 h-5 w-5 text-[color:var(--status-warning)]"
                  fill="currentColor"
                />
              )}
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h2 className="truncate font-serif text-3xl">{agent.name}</h2>
                {agent.isLead && (
                  <span className="inline-flex items-center gap-1 rounded-full border border-[color:var(--status-warning)]/50 bg-[color:var(--status-warning)]/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-[color:var(--status-warning)]">
                    <Crown className="h-3 w-3" fill="currentColor" />{" "}
                    {t("Team lead", "Teamleitung")}
                  </span>
                )}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <span>{agent.role}</span>
                <span>·</span>
                <span>{agent.llm}</span>
                {agent.departmentName && (
                  <>
                    <span>·</span>
                    <span>{agent.departmentName}</span>
                  </>
                )}
              </div>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <StatusPill status={status} />
            <button
              type="button"
              onClick={() => setRunPickerOpen(true)}
              disabled={status === "running" || runAgent.isPending}
              title={status === "running" ? t("Already running", "Läuft bereits") : undefined}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:brightness-110 glow-teal disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Play className="h-4 w-4" /> {t("Run now", "Jetzt ausführen")}
            </button>
          </div>
        </div>
      </Panel>

      {runPickerOpen && (
        <div
          className="fixed inset-0 z-40 grid place-items-center bg-black/60 p-4 backdrop-blur-sm"
          onClick={() => setRunPickerOpen(false)}
        >
          <div
            className="w-full max-w-md overflow-hidden rounded-xl border border-border bg-panel shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <div>
                <div className="text-[11px] uppercase tracking-widest text-muted-foreground">
                  {t("New run", "Neuer Lauf")}
                </div>
                <h2 className="font-serif text-xl">{t("Run now", "Jetzt ausführen")}</h2>
              </div>
              <button
                type="button"
                onClick={() => setRunPickerOpen(false)}
                className="grid h-8 w-8 place-items-center rounded-md border border-border text-muted-foreground transition hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="p-5">
              <label className="block text-xs uppercase tracking-wider text-muted-foreground">
                {t("Task (optional)", "Aufgabe (optional)")}
              </label>
              <textarea
                autoFocus
                rows={4}
                value={runTask}
                onChange={(e) => setRunTask(e.target.value)}
                placeholder={t(
                  `Optional — ${agent.name} already has standing Instructions. Leave blank to just run those.`,
                  `Optional — ${agent.name} hat bereits Anweisungen hinterlegt. Leer lassen, um einfach damit zu starten.`,
                )}
                className="mt-2 w-full rounded-md border border-border bg-background/40 px-3 py-2 text-sm outline-none focus:border-primary/50"
              />
              <div className="mt-4 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setRunPickerOpen(false)}
                  className="rounded-md border border-border px-3 py-2 text-sm text-muted-foreground hover:text-foreground"
                >
                  {t("Cancel", "Abbrechen")}
                </button>
                <button
                  type="button"
                  onClick={submitRun}
                  disabled={runAgent.isPending}
                  className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
                >
                  <Play className="h-4 w-4" />
                  {runAgent.isPending ? t("Running…", "Läuft…") : t("Run", "Ausführen")}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="flex gap-1 overflow-x-auto border-b border-border">
        {tabs.map((tb) => (
          <button
            key={tb.id}
            onClick={() => setTab(tb.id)}
            className={cn(
              "-mb-px border-b-2 px-4 py-2 text-sm transition",
              tab === tb.id
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {tb.label}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="grid gap-4 md:grid-cols-3">
          <StatCard
            icon={<Clock className="h-4 w-4" />}
            label={t("Tasks today", "Aufgaben heute")}
            value={String(agent.tasksToday)}
          />
          <StatCard
            icon={<Wrench className="h-4 w-4" />}
            label={t("Active tools", "Aktive Werkzeuge")}
            value={String(agent.tools.length)}
          />
          <StatCard
            icon={<Shield className="h-4 w-4" />}
            label={t("Guardrails", "Guardrails")}
            value={String(agent.guardrails.length)}
          />
          <Panel className="p-5 md:col-span-3">
            <div className="mb-2 text-xs uppercase tracking-wider text-muted-foreground">
              {t("Last action", "Letzte Aktion")}
            </div>
            <p className="text-sm">{agent.lastAction}</p>
            <div className="mt-3 text-xs text-muted-foreground">{agent.lastRun}</div>
          </Panel>
        </div>
      )}

      {/* The locally started run wins -- it is the one this operator just
          asked for -- but a run started by a schedule or another operator is
          followed too, which is what makes a cron-driven agent watchable at
          all. */}
      {tab === "livelog" && (
        <LiveLog agentId={agent.id} runId={runId ?? agent.currentRunId ?? null} />
      )}

      {tab === "chat" && <ChatWindow agentId={agent.id} agentName={agent.name} />}

      {tab === "files" && <WorkspaceFilesPanel agentId={agent.id} />}

      {tab === "config" && (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="md:col-span-2">
            <SupervisorPanel agent={agent} />
          </div>
          <Panel className="p-5">
            <ConfigSectionHeader
              hint={t("agent identity", "Agenten-Identität")}
              title={t("Role", "Rolle")}
            />
            <input
              defaultValue={agent.role}
              className="mt-4 w-full rounded-md border border-border bg-background/40 px-3 py-2 text-sm outline-none focus:border-primary/50"
            />
          </Panel>
          <AssignedModelPanel agent={agent} models={models} mayManage={mayManage} />
          <AgentRuntimePanel
            agentId={agent.id}
            runtimeRef={agent.runtimeRef}
            mayManage={mayManage}
          />
          <Panel className="p-5">
            <ConfigSectionHeader
              hint={t("when this agent runs", "wann dieser Agent läuft")}
              title={t("Schedule / trigger", "Zeitplan / Auslöser")}
            />
            <TriggerEditor initial={agent.schedule} agentName={agent.name} agentId={agent.id} />
          </Panel>
          <div className="md:col-span-2">
            <AgentKnowledgeSection
              agentId={agent.id}
              agentName={agent.name}
              mayManage={mayManage}
              allBases={allBases}
              agentKbs={agentKbs}
              deptEnabled={deptEnabled}
            />
          </div>
        </div>
      )}

      {tab === "instructions" && (
        <AgentInstructionsPanel agentId={agent.id} mission={agent.mission} mayManage={mayManage} />
      )}

      {tab === "guardrails" && agent.departmentId && (
        <NarrowingEditor agent={agent} mayManage={mayManage} />
      )}
      {tab === "guardrails" && !agent.departmentId && (
        <Panel className="p-5 text-sm text-muted-foreground">
          {t(
            `${agent.name} is not assigned to a department – guardrails are defined at the department level.`,
            `${agent.name} ist keiner Abteilung zugeordnet – Guardrails werden auf Abteilungsebene definiert.`,
          )}
        </Panel>
      )}

      {tab === "memory" && (
        <Panel className="p-10 text-center">
          <BookOpen className="mx-auto mb-3 h-6 w-6 text-muted-foreground/60" />
          <p className="text-sm text-muted-foreground">
            {t(
              "Agent memory browsing is not yet available.",
              "Das Durchsuchen des Agent-Gedächtnisses ist noch nicht verfügbar.",
            )}
          </p>
        </Panel>
      )}

      {tab === "history" && (
        <Panel className="p-10 text-center">
          <Clock className="mx-auto mb-3 h-6 w-6 text-muted-foreground/60" />
          <p className="text-sm text-muted-foreground">
            {t(
              "Run history browsing is not yet available.",
              "Das Durchsuchen des Ausführungsverlaufs ist noch nicht verfügbar.",
            )}
          </p>
        </Panel>
      )}

      {tab === "skills" && (
        <AgentSkillsTab agentId={agent.id} agentName={agent.name} mayManage={mayManage} />
      )}
    </div>
  );
}

// SkillDTO.currentVersionId (backend dto.py) is delivered over the wire but not
// yet declared on the shared Skill type — read it defensively. Null means the
// skill has no published version, so it cannot be assigned.
function skillVersionId(s: Skill): string | null {
  return (s as { currentVersionId?: string | null }).currentVersionId ?? null;
}

// Wraps the existing read-only <KnowledgeAssignment> summary with an
// "Assign knowledge base" button + search modal (mirrors AgentSkillsTab's
// "Assign skill" pattern) -- lets an operator attach a KB directly to this
// agent independent of its department, via the same POST /knowledge/grants
// (useCreateGrant, granteeType="agent") the summary below was already
// wired for but had no entry point to reach (readOnly hid it entirely).
function AgentKnowledgeSection({
  agentId,
  agentName,
  mayManage,
  allBases,
  agentKbs,
  deptEnabled,
}: {
  agentId: string;
  agentName: string;
  mayManage: boolean;
  allBases: KnowledgeBase[];
  agentKbs: string[];
  deptEnabled: string[];
}) {
  const t = useT();
  const createGrant = useCreateGrant();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [search, setSearch] = useState("");

  // Not yet reachable through this agent at all -- already-inherited (dept)
  // or already-granted (agent) bases stay in the read-only summary below,
  // never offered again here.
  const assignable = allBases.filter(
    (kb) => !agentKbs.includes(kb.id) && !deptEnabled.includes(kb.id),
  );
  const searchTerm = search.trim().toLowerCase();
  const filteredAssignable = searchTerm
    ? assignable.filter(
        (kb) =>
          kb.name.toLowerCase().includes(searchTerm) ||
          kb.description.toLowerCase().includes(searchTerm),
      )
    : assignable;

  function assign(kb: KnowledgeBase) {
    createGrant.mutate(
      { kbId: kb.id, granteeType: "agent", granteeId: agentId },
      {
        onSuccess: () => {
          setPickerOpen(false);
          toast.success(t("Knowledge base assigned", "Wissensbasis zugewiesen"), {
            description: `${kb.name} → ${agentName}`,
          });
        },
        onError: () =>
          toast.error(
            t("Couldn't assign knowledge base", "Wissensbasis konnte nicht zugewiesen werden"),
            { description: kb.name },
          ),
      },
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => {
            setSearch("");
            setPickerOpen(true);
          }}
          disabled={!mayManage}
          className="inline-flex shrink-0 items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Plus className="h-4 w-4" /> {t("Assign knowledge base", "Wissensbasis zuweisen")}
        </button>
      </div>
      <KnowledgeAssignment
        mode="agent"
        bases={allBases}
        agentId={agentId}
        agentName={agentName}
        enabled={agentKbs}
        departmentEnabled={deptEnabled}
        readOnly
      />

      {pickerOpen && (
        <div
          className="fixed inset-0 z-40 grid place-items-center bg-black/60 p-4 backdrop-blur-sm"
          onClick={() => setPickerOpen(false)}
        >
          <div
            className="w-full max-w-lg overflow-hidden rounded-xl border border-border bg-panel shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <div>
                <div className="text-[11px] uppercase tracking-widest text-muted-foreground">
                  {t("Knowledge library", "Wissens-Bibliothek")}
                </div>
                <h2 className="font-serif text-xl">
                  {t("Assign knowledge base", "Wissensbasis zuweisen")}
                </h2>
              </div>
              <button
                type="button"
                onClick={() => setPickerOpen(false)}
                className="grid h-8 w-8 place-items-center rounded-md border border-border text-muted-foreground transition hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="relative border-b border-border px-5 py-3">
              <Search className="pointer-events-none absolute left-8 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t("Search knowledge bases…", "Wissensbasen durchsuchen…")}
                className="w-full rounded-md border border-border bg-background/40 py-2 pl-8 pr-3 text-sm outline-none focus:border-primary/50"
              />
            </div>
            <div className="max-h-[60vh] divide-y divide-border overflow-y-auto">
              {filteredAssignable.length === 0 && (
                <div className="p-6 text-center text-sm text-muted-foreground">
                  {assignable.length === 0
                    ? t(
                        "All knowledge bases are already linked.",
                        "Alle Wissensbasen sind bereits verknüpft.",
                      )
                    : t(
                        "No knowledge bases match your search.",
                        "Keine Wissensbasen passen zur Suche.",
                      )}
                </div>
              )}
              {filteredAssignable.map((kb) => (
                <button
                  key={kb.id}
                  type="button"
                  onClick={() => assign(kb)}
                  disabled={createGrant.isPending}
                  className="flex w-full items-start gap-3 px-5 py-3 text-left transition hover:bg-primary/5 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <BookOpen className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                  <div className="min-w-0 flex-1">
                    <span className="truncate text-sm font-medium">{kb.name}</span>
                    <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                      {kb.docs.toLocaleString()} docs · {kb.description}
                    </p>
                  </div>
                  <Plus className="mt-1 h-4 w-4 text-muted-foreground" />
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function AgentSkillsTab({
  agentId,
  agentName,
  mayManage,
}: {
  agentId: string;
  agentName: string;
  mayManage: boolean;
}) {
  const t = useT();
  // Skill-assignment picker for this agent, not a paginated list view.
  const { data: skillsPage } = useSkills({ pageSize: 200 });
  const skills = skillsPage?.items ?? [];
  const assignSkill = useAssignSkill(agentId);
  // No assigned-skills field is exposed on AgentDetail yet, so the assigned set
  // is optimistic/session-local; the assignment itself is persisted server-side.
  const [assigned, setAssigned] = useState<string[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [search, setSearch] = useState("");

  const assignedSkills = skills.filter((s) => assigned.includes(s.id));
  const available = skills.filter((s) => !assigned.includes(s.id));
  const searchTerm = search.trim().toLowerCase();
  const filteredAvailable = searchTerm
    ? available.filter(
        (s) =>
          s.name.toLowerCase().includes(searchTerm) ||
          s.description.toLowerCase().includes(searchTerm),
      )
    : available;

  const assign = (s: Skill) => {
    const versionId = skillVersionId(s);
    if (!versionId) return; // no publishable version → assign is disabled
    setPickerOpen(false);
    // Backend expects a SkillVersion UUID; SkillDTO.currentVersionId carries the
    // current published version (null when the skill has none yet).
    assignSkill.mutate(
      { skillVersionId: versionId },
      {
        onSuccess: () => {
          setAssigned((prev) => (prev.includes(s.id) ? prev : [...prev, s.id]));
          toast.success(t("Skill assigned", "Skill zugewiesen"), {
            description: `${s.name} → ${agentName}`,
          });
        },
        onError: () =>
          toast.error(t("Couldn't assign skill", "Skill konnte nicht zugewiesen werden"), {
            description: s.name,
          }),
      },
    );
  };
  const remove = (s: Skill) => {
    setAssigned((prev) => prev.filter((id) => id !== s.id));
    toast(t("Skill removed", "Skill entfernt"), { description: s.name });
  };

  return (
    <div className="space-y-4">
      <Panel className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="font-serif text-lg">{t("Assigned skills", "Zugewiesene Skills")}</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              {t(
                `${agentName} is composed of a role plus the skills below. Each skill brings its own tools, knowledge and guardrails.`,
                `${agentName} besteht aus einer Rolle plus den folgenden Skills. Jeder Skill bringt eigene Tools, Wissen und Guardrails mit.`,
              )}
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              setSearch("");
              setPickerOpen(true);
            }}
            disabled={!mayManage}
            className="inline-flex shrink-0 items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Plus className="h-4 w-4" /> {t("Assign skill", "Skill zuweisen")}
          </button>
        </div>
      </Panel>

      {assignedSkills.length === 0 && (
        <Panel className="p-8 text-center text-sm text-muted-foreground">
          {t(
            "No skills assigned yet. Click ‘Assign skill’ to compose this agent.",
            "Noch keine Skills zugewiesen. Klicke ‚Skill zuweisen‘, um diesen Agenten zusammenzustellen.",
          )}
        </Panel>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        {assignedSkills.map((s) => (
          <Panel key={s.id} className="p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-primary" />
                  <h4 className="truncate font-medium">{s.name}</h4>
                  <span className="rounded-full border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                    v{s.version}
                  </span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{s.description}</p>
                <div className="mt-2 flex flex-wrap gap-1">
                  {s.tools.slice(0, 4).map((tool) => (
                    <span
                      key={tool}
                      className="inline-flex items-center gap-1 rounded border border-border bg-background/50 px-1.5 py-0.5 text-[10px] text-muted-foreground"
                    >
                      <Wrench className="h-2.5 w-2.5" /> {tool}
                    </span>
                  ))}
                </div>
              </div>
              <button
                type="button"
                onClick={() => remove(s)}
                aria-label={t("Remove", "Entfernen")}
                className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-border text-muted-foreground transition hover:border-[color:var(--status-error)] hover:text-[color:var(--status-error)]"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </Panel>
        ))}
      </div>

      {pickerOpen && (
        <div
          className="fixed inset-0 z-40 grid place-items-center bg-black/60 p-4 backdrop-blur-sm"
          onClick={() => setPickerOpen(false)}
        >
          <div
            className="w-full max-w-lg overflow-hidden rounded-xl border border-border bg-panel shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <div>
                <div className="text-[11px] uppercase tracking-widest text-muted-foreground">
                  {t("Skill library", "Skill-Bibliothek")}
                </div>
                <h2 className="font-serif text-xl">{t("Assign skill", "Skill zuweisen")}</h2>
              </div>
              <button
                type="button"
                onClick={() => setPickerOpen(false)}
                className="grid h-8 w-8 place-items-center rounded-md border border-border text-muted-foreground transition hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="relative border-b border-border px-5 py-3">
              <Search className="pointer-events-none absolute left-8 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t("Search skills…", "Skills durchsuchen…")}
                className="w-full rounded-md border border-border bg-background/40 py-2 pl-8 pr-3 text-sm outline-none focus:border-primary/50"
              />
            </div>
            <div className="max-h-[60vh] divide-y divide-border overflow-y-auto">
              {filteredAvailable.length === 0 && (
                <div className="p-6 text-center text-sm text-muted-foreground">
                  {available.length === 0
                    ? t("All skills already assigned.", "Alle Skills bereits zugewiesen.")
                    : t("No skills match your search.", "Keine Skills passen zur Suche.")}
                </div>
              )}
              {filteredAvailable.map((s) => {
                const noVersion = !skillVersionId(s);
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => assign(s)}
                    disabled={noVersion}
                    className={cn(
                      "flex w-full items-start gap-3 px-5 py-3 text-left transition",
                      noVersion ? "cursor-not-allowed opacity-50" : "hover:bg-primary/5",
                    )}
                  >
                    <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium">{s.name}</span>
                        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                          v{s.version}
                        </span>
                        {noVersion && (
                          <span className="rounded-full border border-border px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-muted-foreground">
                            {t("no version", "keine Version")}
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                        {s.description}
                      </p>
                    </div>
                    <Plus className="mt-1 h-4 w-4 text-muted-foreground" />
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <Panel className="p-5">
      <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="mt-2 font-serif text-3xl">{value}</div>
    </Panel>
  );
}

function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!on)}
      className={cn(
        "relative h-5 w-9 rounded-full border transition",
        on ? "border-primary bg-primary/40" : "border-border bg-background/40",
      )}
      aria-pressed={on}
    >
      <span
        className={cn(
          "absolute top-0.5 h-3.5 w-3.5 rounded-full transition-all",
          on
            ? "left-4 bg-primary shadow-[0_0_10px_var(--primary)]"
            : "left-0.5 bg-muted-foreground",
        )}
      />
    </button>
  );
}

// ---------- Guardrails / tool narrowing ----------

// The agent's Assigned-LLM panel. Its own component (rather than inline in
// AgentDetail) so the subscription risk badge below is reachable from a test
// without standing up the whole routed page -- same shape as NarrowingEditor
// and AgentRuntimePanel next door.
export function AssignedModelPanel({
  agent,
  models,
  mayManage,
}: {
  agent: AgentDetailData;
  models: ModelDTO[];
  mayManage: boolean;
}) {
  const t = useT();
  const switchModel = useSwitchAgentModel();
  const assigned = models.find((model) => model.id === agent.modelConfigId);
  return (
    <Panel className="p-5">
      <ConfigSectionHeader
        hint={t("reasoning engine", "Denkmaschine")}
        title={t("Assigned LLM", "Zugewiesenes LLM")}
      />
      {/* Third of the three surfaces the design's Global Constraints name for
          this badge (provider tile, models table row, agent detail). It sits
          beside the select rather than inside it -- an <option> cannot hold
          markup -- and labels what THIS agent is actually assigned, which is
          the fact that matters on this page. */}
      {assigned?.provider === SUBSCRIPTION_PROVIDER && (
        <div className="mt-3">
          <SubscriptionRiskBadge />
        </div>
      )}
      <select
        className="mt-4 w-full rounded-md border border-border bg-background/30 px-3 py-2 text-sm outline-none focus:border-primary/50 disabled:cursor-not-allowed disabled:opacity-60"
        value={agent.modelConfigId ?? ""}
        disabled={!mayManage || switchModel.isPending || models.length === 0}
        aria-label={t("Assigned LLM", "Zugewiesenes LLM")}
        onChange={(event) => {
          const modelConfigId = event.target.value;
          if (!modelConfigId || modelConfigId === agent.modelConfigId) return;
          switchModel.mutate(
            { agentId: agent.id, modelConfigId },
            {
              onSuccess: () => toast.success(t("Model updated", "Modell aktualisiert")),
              onError: (error) => toast.error(error.message),
            },
          );
        }}
      >
        <option value="" disabled>
          {t("Select a model", "Modell auswählen")}
        </option>
        {models.map((model) => (
          <option key={model.id} value={model.id}>
            {model.displayName || model.model} · {model.provider}
          </option>
        ))}
      </select>
      <p className="mt-2 text-[11px] text-muted-foreground">
        {t(
          "Changing the model takes effect for the next run. Manage model configurations under Models.",
          "Die Änderung gilt für den nächsten Lauf. Modellkonfigurationen verwaltest du unter Modelle.",
        )}
        {!mayManage &&
          ` ${t("Your role does not include agent:manage, so this assignment is read-only for you.", "Ihre Rolle enthält agent:manage nicht, deshalb ist diese Zuweisung für Sie schreibgeschützt.")}`}
      </p>
    </Panel>
  );
}

// Real tool-narrowing editor. The department frame is the ceiling; an agent may
// only *disable* frame tools (never widen). Persists via PUT /agents/{id}/narrowing
// (useUpdateNarrowing) as { narrowing: { tools: { <key>: {...} } } }. Only tools
// enabled in the frame are shown, so toggles always stay within the frame.
export function NarrowingEditor({
  agent,
  mayManage,
}: {
  agent: AgentDetailData;
  mayManage: boolean;
}) {
  const t = useT();
  const update = useUpdateNarrowing(agent.id);
  const logins = useMcpLogins();
  const { data: connections = [] } = useMcpConnections();
  const frame = agent.departmentFrameTools;
  const effective = agent.effectiveTools;
  const frameKeys = Object.keys(frame).filter((k) => frame[k]?.enabled);
  // Only a tool with a live MCP connection is actually usable -- a
  // department can enable a tool in its frame before anyone connected it
  // (e.g. Hubspot, Jira granted but never set up), and showing those
  // inline read as if they were already active. `frameKeys` still drives
  // `save()` below (every frame tool's enabled state is sent either way);
  // this narrows only what's OFFERED in the UI.
  const connectedNames = new Set(connections.filter((c) => c.connected).map((c) => c.name));
  const connectedFrameKeys = frameKeys.filter((k) => connectedNames.has(k));
  const [enabled, setEnabled] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(frameKeys.map((k) => [k, !!effective[k]?.enabled])),
  );
  const [pickerOpen, setPickerOpen] = useState(false);
  const [toolSearch, setToolSearch] = useState("");
  // Per-tool-key policy edits (read/write/send/approvalEur/approvalActions/
  // only), keyed the same way as `enabled` -- not fed back from `effective`
  // on every render, same reason DepartmentToolsPanel's `edited` state isn't
  // either: a click on a preset or a free-text chip must not get clobbered by
  // a refetch mid-edit.
  const [edited, setEdited] = useState<Record<string, GuardrailValue>>({});
  // Per-tool-key login pin, keyed the same way as `enabled`. Seeded from any
  // pin the backend already resolved for this agent (if the DTO ever carries
  // one); absent means "no pin yet", not "unpin".
  const [connectionId, setConnectionId] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      frameKeys.map((k) => [k, effective[k]?.connectionId ?? ""]).filter(([, v]) => !!v),
    ),
  );
  // A login is available FOR tool key `k` exactly when `login.name === k` —
  // `McpConnection.name` IS the tool key, the by-name convention this whole
  // feature relies on (Tasks 3/4/6). One unfiltered fetch, grouped client-side,
  // rather than N network calls for a handful of tool keys.
  const loginsByKey: Record<string, McpLoginDTO[]> = {};
  for (const login of logins.data ?? []) {
    (loginsByKey[login.name] ??= []).push(login);
  }
  // Picking (or creating) a login happens inline via CredentialPicker -- the
  // same "select or create new" control the Capa setup form uses, not a
  // stacked modal, and no free-text scopes field (live user feedback: asking
  // for raw tool-call names "das bekommt doch kein mitarbeiter hin"). A login
  // is a Credential paired 1:1 with a tenant-global McpConnection
  // (POST /mcp/logins), so picking a credential still needs one created/
  // reused behind the scenes -- pinCredential below does that.
  const createLogin = useCreateMcpLogin();

  async function pinCredential(toolKey: string, credentialType: string, credentialId: string) {
    if (!credentialId) {
      setConnectionId((s) => {
        const next = { ...s };
        delete next[toolKey];
        return next;
      });
      return;
    }
    const existing = (loginsByKey[toolKey] ?? []).find((l) => l.credentialId === credentialId);
    if (existing) {
      setConnectionId((s) => ({ ...s, [toolKey]: existing.id }));
      return;
    }
    try {
      const login = await createLogin.mutateAsync({
        name: toolKey,
        credentialType,
        credentialId,
        scopes: [],
      });
      setConnectionId((s) => ({ ...s, [toolKey]: login.id }));
    } catch (err) {
      toast.error(
        t("Could not link this credential", "Anmeldedaten konnten nicht verknüpft werden"),
        {
          description: err instanceof Error ? err.message : String(err),
        },
      );
    }
  }

  // What the picker for tool `k` starts from: any local edit, else the
  // agent's current effective policy (which already reflects prior
  // narrowing merged with the department frame), else the frame's own
  // policy. Deliberately NOT `frame[k]` alone -- that would silently reset
  // an already-narrowed approvalActions/only back to the department's wider
  // default the moment the picker first renders.
  function valueFor(k: string): GuardrailValue {
    if (edited[k]) return edited[k];
    const src = effective[k] ?? frame[k];
    return {
      read: !!src?.read,
      write: !!src?.write,
      send: !!src?.send,
      approvalActions: src?.approvalActions ?? [],
      approvalEur: src?.approvalEur ?? null,
      only: src?.only ?? [],
    };
  }

  function save() {
    const tools: Record<string, unknown> = {};
    for (const k of frameKeys) {
      const val = valueFor(k);
      if (enabled[k] && (loginsByKey[k]?.length ?? 0) > 0 && !connectionId[k]) {
        toast.error(t("Pick a login before saving", "Login vor dem Speichern auswählen"), {
          description: k,
        });
      }
      tools[k] = {
        enabled: enabled[k],
        read: val.read,
        write: val.write,
        send: val.send,
        // Backend narrowing dict reads snake_case keys throughout -- an
        // untyped dict on the backend, so nothing auto-converts these.
        approval_eur: val.approvalEur ?? null,
        approval_actions: val.approvalActions,
        only: val.only,
        connection_id: connectionId[k] || null,
      };
    }
    update.mutate(
      { narrowing: { tools } },
      {
        onSuccess: () =>
          toast.success(t("Guardrails saved", "Guardrails gespeichert"), {
            description: agent.name,
          }),
        onError: () =>
          toast.error(
            t("Couldn't save guardrails", "Guardrails konnten nicht gespeichert werden"),
            {
              description: t(
                "Narrowing must stay within the department frame.",
                "Einschränkung muss innerhalb des Abteilungsrahmens bleiben.",
              ),
            },
          ),
      },
    );
  }

  return (
    <Panel className="p-5">
      <div className="mb-4 flex items-start justify-between gap-2">
        <ConfigSectionHeader
          hint={t("tool narrowing", "Tool-Einschränkung")}
          title={t("Guardrails & tool access", "Guardrails & Tool-Zugriff")}
        />
        <button
          type="button"
          onClick={() => {
            setToolSearch("");
            setPickerOpen(true);
          }}
          disabled={!mayManage || connectedFrameKeys.length === 0}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border bg-background/40 px-2.5 py-1.5 text-xs font-medium text-foreground transition hover:bg-background/70 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" /> {t("Assign tool", "Tool zuweisen")}
        </button>
      </div>
      {connectedFrameKeys.length === 0 ? (
        <p className="rounded-md border border-dashed border-border/70 bg-background/30 p-3 text-center text-xs text-muted-foreground">
          {frameKeys.length === 0
            ? t(
                "No department frame — this agent has no narrowable tools.",
                "Kein Abteilungsrahmen — dieser Agent hat keine einschränkbaren Tools.",
              )
            : t(
                "None of the department's tools are connected yet — connect one under Capas first.",
                "Keines der Tools der Abteilung ist bereits verbunden — zuerst unter Capas verbinden.",
              )}
        </p>
      ) : connectedFrameKeys.filter((k) => enabled[k]).length === 0 ? (
        <p className="rounded-md border border-dashed border-border/70 bg-background/30 p-3 text-center text-xs text-muted-foreground">
          {t(
            "No tools assigned yet. Click ‘Assign tool’ to grant this agent one of the department's connected tools.",
            "Noch keine Tools zugewiesen. Klicke „Tool zuweisen“, um diesem Agenten eines der verbundenen Tools der Abteilung zu geben.",
          )}
        </p>
      ) : (
        <ul className="space-y-3">
          {connectedFrameKeys
            .filter((k) => enabled[k])
            .map((k) => {
              const connection = connections.find((c) => c.name === k);
              return (
                <li key={k} className="rounded-md border border-border bg-background/30 p-3">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                    <div className="flex min-w-0 flex-1 items-center gap-2">
                      <Shield className="h-3.5 w-3.5 shrink-0 text-primary" />
                      <span className="min-w-0 flex-1 truncate text-sm">{k}</span>
                    </div>
                    <span
                      className={cn(
                        "hidden text-[10px] sm:inline",
                        enabled[k] ? "text-[color:var(--status-running)]" : "text-muted-foreground",
                      )}
                    >
                      {enabled[k] ? t("enabled", "aktiv") : t("disabled", "aus")}
                    </span>
                    {(() => {
                      const credentialType = connection?.credentialType;
                      if (!credentialType) return null;
                      const pickedCredentialId =
                        (loginsByKey[k] ?? []).find((l) => l.id === connectionId[k])
                          ?.credentialId ?? "";
                      if (!mayManage) {
                        const pinnedName = (loginsByKey[k] ?? []).find(
                          (l) => l.id === connectionId[k],
                        )?.name;
                        return pinnedName ? (
                          <span className="shrink-0 text-[11px] text-muted-foreground">
                            {pinnedName}
                          </span>
                        ) : null;
                      }
                      return (
                        <div className="min-w-[220px] shrink-0">
                          <CredentialPicker
                            credentialType={credentialType}
                            value={pickedCredentialId}
                            onChange={(credentialId) =>
                              pinCredential(k, credentialType, credentialId)
                            }
                          />
                        </div>
                      );
                    })()}
                    <Toggle
                      on={!!enabled[k]}
                      onChange={(v) => setEnabled((s) => ({ ...s, [k]: v }))}
                    />
                  </div>
                  {mayManage && connection && (
                    <div className="mt-3 border-t border-border/70 pt-3">
                      <GuardrailPresetPicker
                        presets={connection.guardrailPresets}
                        guardrailLibrary={connection.guardrailLibrary}
                        hasValueSpec={connection.hasValueSpec}
                        value={valueFor(k)}
                        onChange={(next) => setEdited((prev) => ({ ...prev, [k]: next }))}
                      />
                    </div>
                  )}
                </li>
              );
            })}
        </ul>
      )}
      {frameKeys.length > 0 && (
        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={save}
            disabled={!mayManage || update.isPending}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
          >
            <Save className="h-3.5 w-3.5" /> {t("Save", "Speichern")}
          </button>
        </div>
      )}
      <p className="mt-3 text-[11px] text-muted-foreground">
        {t(
          "Disable tools to narrow this agent below the department frame. You can only tighten, never widen.",
          "Deaktiviere Tools, um diesen Agenten unter den Abteilungsrahmen einzuschränken. Nur Einschränken möglich, kein Erweitern.",
        )}
      </p>

      {pickerOpen &&
        (() => {
          const assignable = connectedFrameKeys.filter((k) => !enabled[k]);
          const searchTerm = toolSearch.trim().toLowerCase();
          const filtered = searchTerm
            ? assignable.filter((k) => k.toLowerCase().includes(searchTerm))
            : assignable;
          return (
            <div
              className="fixed inset-0 z-40 grid place-items-center bg-black/60 p-4 backdrop-blur-sm"
              onClick={() => setPickerOpen(false)}
            >
              <div
                className="w-full max-w-lg overflow-hidden rounded-xl border border-border bg-panel shadow-2xl"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex items-center justify-between border-b border-border px-5 py-4">
                  <div>
                    <div className="text-[11px] uppercase tracking-widest text-muted-foreground">
                      {t("Connected tools", "Verbundene Tools")}
                    </div>
                    <h2 className="font-serif text-xl">{t("Assign tool", "Tool zuweisen")}</h2>
                  </div>
                  <button
                    type="button"
                    onClick={() => setPickerOpen(false)}
                    className="grid h-8 w-8 place-items-center rounded-md border border-border text-muted-foreground transition hover:text-foreground"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <div className="relative border-b border-border px-5 py-3">
                  <Search className="pointer-events-none absolute left-8 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                  <input
                    value={toolSearch}
                    onChange={(e) => setToolSearch(e.target.value)}
                    placeholder={t("Search tools…", "Tools durchsuchen…")}
                    className="w-full rounded-md border border-border bg-background/40 py-2 pl-8 pr-3 text-sm outline-none focus:border-primary/50"
                  />
                </div>
                <div className="max-h-[60vh] divide-y divide-border overflow-y-auto">
                  {filtered.length === 0 && (
                    <div className="p-6 text-center text-sm text-muted-foreground">
                      {assignable.length === 0
                        ? t(
                            "All connected tools are already assigned.",
                            "Alle verbundenen Tools sind bereits zugewiesen.",
                          )
                        : t("No tools match your search.", "Keine Tools passen zur Suche.")}
                    </div>
                  )}
                  {filtered.map((k) => (
                    <button
                      key={k}
                      type="button"
                      onClick={() => {
                        setEnabled((s) => ({ ...s, [k]: true }));
                        setPickerOpen(false);
                      }}
                      className="flex w-full items-center gap-3 px-5 py-3 text-left transition hover:bg-primary/5"
                    >
                      <Wrench className="h-4 w-4 shrink-0 text-primary" />
                      <span className="min-w-0 flex-1 truncate text-sm font-medium">{k}</span>
                      <Plus className="h-4 w-4 text-muted-foreground" />
                    </button>
                  ))}
                </div>
              </div>
            </div>
          );
        })()}
    </Panel>
  );
}

// ---------- Supervisor ("Teamleiter") ----------

function SupervisorPanel({ agent }: { agent: AgentDetailData }) {
  const t = useT();
  const { data: backendSup } = useAgentSupervisor(agent.id);
  const setSupervisor = useSetAgentSupervisor();
  // Supervisor-candidate picker over the whole tenant, not a paginated list
  // view.
  const { data: allAgentsPage } = useAgents({ pageSize: 200 });
  const allAgents = allAgentsPage?.items ?? [];
  // Candidate supervisors = other agents in the same department (real data).
  const candidates = allAgents.filter(
    (a) => agent.departmentId != null && a.departmentId === agent.departmentId && a.id !== agent.id,
  );
  const [value, setValue] = useState<string>("auto");
  const [interventions, setInterventions] = useState({
    watchReasoning: true,
    driftGuard: true,
    roleAudit: true,
    weeklyReport: false,
  });

  // Sync from the backend supervision assignment once loaded.
  useEffect(() => {
    if (backendSup?.supervisorAgentId) setValue(`agent:${backendSup.supervisorAgentId}`);
    if (backendSup) setInterventions((i) => ({ ...i, driftGuard: backendSup.driftGuard }));
  }, [backendSup?.supervisorAgentId, backendSup?.driftGuard, backendSup]);

  const current = resolveSupervisor(value, candidates);

  function persist(next: string, driftGuard: boolean) {
    const supervisorAgentId = next.startsWith("agent:") ? next.slice("agent:".length) : null;
    setSupervisor.mutate({ agentId: agent.id, supervisorAgentId, driftGuard });
  }

  function onChange(next: string) {
    setValue(next);
    const resolved = resolveSupervisor(next, candidates);
    persist(next, interventions.driftGuard);
    toast.success(t("Supervisor updated", "Vorgesetzter aktualisiert"), {
      description:
        resolved.kind === "agent"
          ? `${agent.name} → ${resolved.agent.name}`
          : `${agent.name} → ${t("Human oversight", "Menschliche Aufsicht")}`,
    });
  }

  return (
    <Panel className="relative overflow-hidden p-5">
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background: `radial-gradient(ellipse at top right, color-mix(in oklab, ${agent.avatarColor} 12%, transparent), transparent 65%)`,
        }}
      />
      <div className="relative">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-[10px] uppercase tracking-widest text-muted-foreground">
              {t("Team lead · oversight", "Teamleiter · Aufsicht")}
            </div>
            <h3 className="font-serif text-lg">
              {t("Who watches the thinking of", "Wer überwacht das Denken von")}{" "}
              <span style={{ color: agent.avatarColor }}>{agent.name}</span>?
            </h3>
            <p className="mt-1 max-w-xl text-xs text-muted-foreground">
              {t(
                "The team lead observes reasoning traces and daily behavior to prevent role drift — separate from human-in-the-loop approvals, which stay off-agent.",
                "Der Teamleiter beobachtet Denkspuren und tägliches Verhalten, um Rollen-Drift zu verhindern — getrennt von Human-in-the-Loop-Approvals, die weiterhin off-agent bleiben.",
              )}
            </p>
          </div>
          <span className="rounded-full border border-border bg-background/40 px-2 py-1 text-[11px] text-muted-foreground">
            {current.kind === "agent"
              ? current.source === "department-lead"
                ? t("via department lead", "über Abteilungsleiter")
                : t("explicit", "explizit")
              : t("human reviewer", "menschlicher Reviewer")}
          </span>
        </div>

        <div
          className="mt-4 rounded-xl border-2 border-dashed p-4"
          style={{
            borderColor: `color-mix(in oklab, ${agent.avatarColor} 45%, transparent)`,
            background: `color-mix(in oklab, ${agent.avatarColor} 6%, transparent)`,
          }}
        >
          <div className="flex flex-wrap items-center gap-3">
            <SupervisorChip supervisor={current} />
            <span className="text-[11px] text-muted-foreground">
              {t("supervises →", "beaufsichtigt →")}
            </span>
            <div className="flex items-center gap-2 rounded-md border border-border bg-background/60 px-2 py-1.5 text-xs">
              <div
                className="grid h-5 w-5 place-items-center rounded-full font-serif text-[10px] text-black"
                style={{ background: agent.avatarColor }}
              >
                {agent.name[0]}
              </div>
              <span className="font-medium text-foreground">{agent.name}</span>
              <span className="text-muted-foreground">· {agent.role}</span>
            </div>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
            <div>
              <div className="text-[10px] uppercase tracking-widest text-muted-foreground">
                {t("Assign supervisor", "Vorgesetzten zuweisen")}
              </div>
              <select
                value={value}
                onChange={(e) => onChange(e.target.value)}
                className="mt-1 w-full rounded-md border border-border bg-background/40 px-3 py-2 text-sm outline-none focus:border-primary/50"
              >
                <option value="auto">
                  {t("Auto (department lead)", "Automatisch (Abteilungsleiter)")}
                </option>
                <option value="human">{t("Human reviewer", "Menschlicher Reviewer")}</option>
                {candidates.length > 0 && (
                  <optgroup label={t("Agents", "Agenten")}>
                    {candidates.map((c) => (
                      <option key={c.id} value={`agent:${c.id}`}>
                        {c.name} — {c.role}
                        {c.isLead ? " · lead" : ""}
                      </option>
                    ))}
                  </optgroup>
                )}
              </select>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-widest text-muted-foreground">
                {t(
                  "Oversight rules — reasoning & behavior",
                  "Überwachungs-Regeln — Denken & Verhalten",
                )}
              </div>
              <div className="mt-1 space-y-1.5 rounded-md border border-border bg-background/30 p-2">
                <InterventionRow
                  label={t("Observe reasoning traces", "Denkspuren beobachten")}
                  on={interventions.watchReasoning}
                  onChange={(v) => setInterventions((s) => ({ ...s, watchReasoning: v }))}
                />
                <InterventionRow
                  label={t(
                    "Drift guard — pause on off-role actions",
                    "Drift-Schutz — pausieren bei rollenfremden Aktionen",
                  )}
                  on={interventions.driftGuard}
                  onChange={(v) => {
                    setInterventions((s) => ({ ...s, driftGuard: v }));
                    persist(value, v);
                  }}
                />
                <InterventionRow
                  label={t(
                    "Daily role audit — compare actions vs. role scope",
                    "Tägliches Rollen-Audit — Aktionen vs. Rollenumfang",
                  )}
                  on={interventions.roleAudit}
                  onChange={(v) => setInterventions((s) => ({ ...s, roleAudit: v }))}
                />
                <InterventionRow
                  label={t("Weekly oversight report", "Wöchentlicher Aufsichtsbericht")}
                  on={interventions.weeklyReport}
                  onChange={(v) => setInterventions((s) => ({ ...s, weeklyReport: v }))}
                />
              </div>
            </div>
          </div>

          <div className="mt-3 flex items-start gap-2 text-[11px] text-muted-foreground">
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
            <span>
              {t(
                "Prevents agent-driven drift: the supervisor watches reasoning and behavior. Approvals stay off-agent with a human-in-the-loop.",
                "Verhindert Agent-Drift: Der Teamleiter beobachtet Denken und Verhalten. Approvals bleiben off-agent per Human-in-the-Loop.",
              )}
            </span>
          </div>
        </div>
      </div>
    </Panel>
  );
}

function resolveSupervisor(value: string, candidates: Agent[]): Supervisor {
  if (value.startsWith("agent:")) {
    const id = value.slice("agent:".length);
    const a = candidates.find((c) => c.id === id);
    if (a) return { kind: "agent", agent: a, source: "explicit" };
  }
  // "auto" resolves to the department lead among the real candidates, if any.
  if (value === "auto") {
    const lead = candidates.find((c) => c.isLead);
    if (lead) return { kind: "agent", agent: lead, source: "department-lead" };
  }
  return { kind: "human", source: value === "human" ? "explicit" : "fallback" };
}

function SupervisorChip({ supervisor }: { supervisor: Supervisor }) {
  if (supervisor.kind === "human") {
    return (
      <div className="inline-flex items-center gap-2 rounded-md border border-primary/40 bg-primary/10 px-2 py-1.5 text-xs">
        <div className="grid h-5 w-5 place-items-center rounded-full bg-primary/20 text-primary">
          <UserCheck className="h-3 w-3" />
        </div>
        <span className="font-medium text-foreground">Human reviewer</span>
        <span className="rounded-full border border-primary/40 bg-primary/10 px-1.5 py-0.5 text-[9px] uppercase tracking-widest text-primary">
          thinking review
        </span>
      </div>
    );
  }
  const a = supervisor.agent;
  return (
    <div className="inline-flex items-center gap-2 rounded-md border border-primary/40 bg-primary/[0.06] px-2 py-1.5 text-xs">
      <div
        className="grid h-5 w-5 place-items-center rounded-full font-serif text-[10px] text-black"
        style={{ background: a.avatarColor }}
      >
        {a.name[0]}
      </div>
      <span className="font-medium text-foreground">{a.name}</span>
      <span className="text-muted-foreground">· {a.role}</span>
      {a.isLead && (
        <span className="rounded-full border border-primary/40 bg-primary/10 px-1.5 py-0.5 text-[9px] uppercase tracking-widest text-primary">
          lead
        </span>
      )}
    </div>
  );
}

function InterventionRow({
  label,
  on,
  onChange,
}: {
  label: string;
  on: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3 rounded-md px-2 py-1 text-xs text-foreground/90 hover:bg-background/40">
      <span>{label}</span>
      <Toggle on={on} onChange={onChange} />
    </label>
  );
}

function ConfigSectionHeader({ hint, title }: { hint: string; title: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-widest text-muted-foreground">{hint}</div>
      <h3 className="mt-0.5 font-serif text-lg">{title}</h3>
    </div>
  );
}

// ---------- Schedule / trigger ----------

/** Which trigger TYPE this agent uses: a cron schedule (ScheduleEditor,
 * unchanged below) or a generic webhook (WebhookTriggerPanel). Defaults to
 * whichever the agent already has configured, cron otherwise -- an agent
 * genuinely has at most one of each kind today (single-mcp_conn dispatch's
 * own limit, runtime/executor.py), so "which tab is open" and "which
 * trigger exists" stay in lockstep. */
function TriggerEditor({
  initial,
  agentName,
  agentId,
}: {
  initial: string;
  agentName: string;
  agentId: string;
}) {
  const t = useT();
  const { data: triggers } = useAgentTriggers(agentId);
  const webhookTrigger = triggers?.find((tr) => tr.kind === "webhook");
  const [tab, setTab] = useState<"cron" | "webhook">(webhookTrigger ? "webhook" : "cron");

  useEffect(() => {
    if (webhookTrigger) setTab("webhook");
  }, [webhookTrigger?.id]);

  return (
    <div className="space-y-3">
      <div className="flex gap-1.5">
        <button
          type="button"
          onClick={() => setTab("cron")}
          className={cn(
            "rounded-full border px-2.5 py-1 text-[11px] transition",
            tab === "cron"
              ? "border-primary/60 bg-primary/15 text-primary"
              : "border-border bg-background/40 text-muted-foreground hover:text-foreground",
          )}
        >
          <Clock className="mr-1 inline h-3 w-3" />
          {t("Schedule", "Zeitplan")}
        </button>
        <button
          type="button"
          onClick={() => setTab("webhook")}
          className={cn(
            "rounded-full border px-2.5 py-1 text-[11px] transition",
            tab === "webhook"
              ? "border-primary/60 bg-primary/15 text-primary"
              : "border-border bg-background/40 text-muted-foreground hover:text-foreground",
          )}
        >
          <Webhook className="mr-1 inline h-3 w-3" />
          {t("Webhook", "Webhook")}
        </button>
      </div>
      {tab === "cron" ? (
        <ScheduleEditor initial={initial} agentName={agentName} agentId={agentId} />
      ) : (
        <WebhookTriggerPanel agentId={agentId} agentName={agentName} trigger={webhookTrigger} />
      )}
    </div>
  );
}

/** Generic, n8n-Webhook-node-style trigger: one unguessable URL, any caller,
 * any JSON payload -- no per-source setup (see backend/src/oc8/api/v1/
 * webhooks.py). The URL is not one-time-reveal; it is fetched fresh every
 * time this agent's triggers list loads, so an operator can always come
 * back and re-copy it into the external system's config. */
function WebhookTriggerPanel({
  agentId,
  agentName,
  trigger,
}: {
  agentId: string;
  agentName: string;
  trigger: TriggerDTO | undefined;
}) {
  const t = useT();
  const createTrigger = useCreateAgentTrigger();
  const deleteTrigger = useDeleteAgentTrigger();
  const [copied, setCopied] = useState(false);

  function create() {
    createTrigger.mutate(
      {
        agentId,
        kind: "webhook",
        taskText: t(
          `React to whatever this webhook sends, on ${agentName}'s behalf.`,
          `Reagiere auf das, was dieser Webhook sendet, im Namen von ${agentName}.`,
        ),
      },
      {
        onSuccess: () => toast.success(t("Webhook created", "Webhook erstellt")),
        onError: () =>
          toast.error(t("Couldn't create webhook", "Webhook konnte nicht erstellt werden")),
      },
    );
  }

  function remove() {
    if (!trigger) return;
    deleteTrigger.mutate(
      { agentId, triggerId: trigger.id },
      {
        onSuccess: () => toast.success(t("Webhook removed", "Webhook entfernt")),
        onError: () =>
          toast.error(t("Couldn't remove webhook", "Webhook konnte nicht entfernt werden")),
      },
    );
  }

  async function copy() {
    if (!trigger?.webhookUrl) return;
    await navigator.clipboard.writeText(trigger.webhookUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  if (!trigger) {
    return (
      <div className="space-y-3">
        <p className="text-[11px] text-muted-foreground">
          {t(
            "Any system that can send a JSON POST can trigger this agent — no signature or per-source setup needed. The URL itself is the secret, so it isn't shown to anyone who isn't looking at this page.",
            "Jedes System, das per POST JSON senden kann, kann diesen Agenten auslösen — keine Signatur oder Quellen-spezifische Einrichtung nötig. Die URL selbst ist das Geheimnis und wird nur hier angezeigt.",
          )}
        </p>
        <button
          type="button"
          onClick={create}
          disabled={createTrigger.isPending}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Webhook className="h-3.5 w-3.5" />
          {createTrigger.isPending
            ? t("Creating…", "Wird erstellt…")
            : t("Create webhook URL", "Webhook-URL erstellen")}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 rounded-md border border-border bg-background/30 px-3 py-2">
        <code className="min-w-0 flex-1 truncate font-mono text-xs">{trigger.webhookUrl}</code>
        <button
          type="button"
          onClick={copy}
          className="inline-flex shrink-0 items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] text-muted-foreground transition hover:text-foreground"
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          {copied ? t("Copied", "Kopiert") : t("Copy", "Kopieren")}
        </button>
      </div>
      <p className="text-[11px] text-muted-foreground">
        {t(
          "Paste this into the external system's webhook / automation config — e.g. Odoo's Automation Rules → Send Webhook Notification.",
          "Trage diese URL in die Webhook-/Automatisierungs-Konfiguration des externen Systems ein — z. B. Odoos Automation Rules → Send Webhook Notification.",
        )}
      </p>
      <button
        type="button"
        onClick={remove}
        disabled={deleteTrigger.isPending}
        className="inline-flex items-center gap-1 text-[11px] text-muted-foreground transition hover:text-foreground disabled:opacity-50"
      >
        <Trash2 className="h-3 w-3" />
        {t("Remove webhook", "Webhook entfernen")}
      </button>
    </div>
  );
}

type TriggerMode = "continuous" | "24-7" | "schedule" | "on-demand" | "custom";

// "schedule" (free-text "Weekly schedule") dropped from the preset list --
// Custom cron already covers everything it could express, so it was a
// redundant option. `detectTriggerMode` below still falls back to it for
// an agent whose `agent.schedule` already holds an old free-text value, so
// that legacy data keeps rendering rather than being reinterpreted as cron.
const TRIGGER_PRESETS: { id: TriggerMode; label: string; example: string }[] = [
  { id: "continuous", label: "Continuous", example: "Continuous" },
  { id: "24-7", label: "24/7", example: "24/7" },
  { id: "on-demand", label: "On-Demand", example: "On-Demand" },
  { id: "custom", label: "Time scheduled", example: "0 */2 * * *" },
];

function detectTriggerMode(value: string): TriggerMode {
  const v = value.trim().toLowerCase();
  if (v === "continuous") return "continuous";
  if (v === "24/7") return "24-7";
  if (v === "on-demand") return "on-demand";
  if (/^[0-9*/,\-\s]+$/.test(v)) return "custom";
  return "schedule";
}

function ScheduleEditor({
  initial,
  agentName,
  agentId,
}: {
  initial: string;
  agentName: string;
  agentId: string;
}) {
  const t = useT();
  const { data: triggers } = useAgentTriggers(agentId);
  const cronTrigger = triggers?.find((tr) => tr.kind === "cron");
  const createTrigger = useCreateAgentTrigger();
  const updateTrigger = useUpdateAgentTrigger();
  const deleteTrigger = useDeleteAgentTrigger();
  const [mode, setMode] = useState<TriggerMode>(() => detectTriggerMode(initial));
  const [value, setValue] = useState<string>(initial);

  useEffect(() => {
    if (cronTrigger?.cronExpression) {
      setMode("custom");
      setValue(cronTrigger.cronExpression);
    }
  }, [cronTrigger?.cronExpression]);

  function pickMode(next: TriggerMode) {
    setMode(next);
    const preset = TRIGGER_PRESETS.find((p) => p.id === next)!;
    if (next !== "schedule" && next !== "custom") {
      setValue(preset.example);
    }
    if (next === "custom") {
      // Seed a valid cron if the current value is not one.
      const isCron = /^[0-9*/,\-\s]+$/.test(value.trim());
      if (!isCron) setValue("0 9 * * 1-5");
    }
  }

  function save() {
    const onFailed = () =>
      toast.error(t("Couldn't update schedule", "Zeitplan konnte nicht aktualisiert werden"), {
        description: t("Try again in a moment.", "Bitte versuche es in Kürze erneut."),
      });
    // Only "custom" mode produces a real cron string (via CronBuilder); the
    // other presets are display-only and never touch the backend.
    if (mode !== "custom") {
      const onDone = () =>
        toast.success(t("Schedule updated", "Zeitplan aktualisiert"), {
          description: `${agentName} · ${value || TRIGGER_PRESETS.find((p) => p.id === mode)?.example || mode}`,
        });
      if (cronTrigger) {
        deleteTrigger.mutate(
          { agentId, triggerId: cronTrigger.id },
          { onSuccess: onDone, onError: onFailed },
        );
      } else {
        onDone();
      }
      return;
    }
    const onSaved = () =>
      toast.success(t("Schedule updated", "Zeitplan aktualisiert"), {
        description: `${agentName} · ${value}`,
      });
    if (cronTrigger) {
      updateTrigger.mutate(
        { agentId, triggerId: cronTrigger.id, cronExpression: value },
        { onSuccess: onSaved, onError: onFailed },
      );
    } else {
      createTrigger.mutate(
        { agentId, cronExpression: value, taskText: `Scheduled run for ${agentName}` },
        { onSuccess: onSaved, onError: onFailed },
      );
    }
  }

  const showTextInput = mode === "schedule" || mode === "custom";

  return (
    <div className="mt-4 space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {TRIGGER_PRESETS.map((p) => {
          const active = p.id === mode;
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => pickMode(p.id)}
              className={cn(
                "rounded-full border px-2.5 py-1 text-[11px] transition",
                active
                  ? "border-primary/60 bg-primary/15 text-primary"
                  : "border-border bg-background/40 text-muted-foreground hover:text-foreground",
              )}
            >
              {p.label}
            </button>
          );
        })}
      </div>
      {showTextInput ? (
        mode === "custom" ? (
          <CronBuilder
            initial={value}
            onChange={setValue}
            onSave={save}
            saveLabel={t("Save", "Speichern")}
          />
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="z. B. Mon–Fri, 09:00–17:00"
              className="min-w-0 flex-1 rounded-md border border-border bg-background/40 px-3 py-2 text-sm outline-none focus:border-primary/50"
            />
            <button
              type="button"
              onClick={save}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90"
            >
              <Save className="h-3.5 w-3.5" /> {t("Save", "Speichern")}
            </button>
          </div>
        )
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-background/30 px-3 py-2 text-sm">
          <span className="font-mono text-xs text-muted-foreground">{value}</span>
          <button
            type="button"
            onClick={save}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-xs font-medium text-primary-foreground transition hover:opacity-90"
          >
            <Save className="h-3.5 w-3.5" /> {t("Save", "Speichern")}
          </button>
        </div>
      )}
      <p className="text-[11px] text-muted-foreground">
        {t(
          "Pick a preset or define an exact schedule / cron expression.",
          "Wähle einen Preset oder definiere einen exakten Zeitplan / Cron-Ausdruck.",
        )}
      </p>
    </div>
  );
}

// ---------- Files ----------
//
// The agent's sandbox workspace: GET /agents/{id}/workspace/files lists the
// most recent run's files (only meaningful for the three containerized
// runtimes -- opencode/codex/claude_code -- that mount a host directory at
// /workspace; `applicable: false` covers everything else, including the
// in-process "Standard" runtime).

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function WorkspaceFilesPanel({ agentId }: { agentId: string }) {
  const t = useT();
  const files = useAgentWorkspaceFiles(agentId);
  const [selected, setSelected] = useState<string | null>(null);
  const content = useAgentWorkspaceFile(agentId, selected);

  if (files.isPending) {
    return (
      <Panel className="p-10 text-center">
        <p className="text-sm text-muted-foreground">
          {t("Loading files…", "Dateien werden geladen…")}
        </p>
      </Panel>
    );
  }

  const data = files.data;
  if (!data || !data.applicable) {
    return (
      <Panel className="p-10 text-center">
        <FileText className="mx-auto mb-3 h-6 w-6 text-muted-foreground/60" />
        <p className="text-sm text-muted-foreground">
          {data?.message ??
            t(
              "The agent file store is not available yet.",
              "Der Agent-Dateispeicher ist noch nicht verfügbar.",
            )}
        </p>
      </Panel>
    );
  }

  if (data.files.length === 0) {
    return (
      <Panel className="p-10 text-center">
        <FileText className="mx-auto mb-3 h-6 w-6 text-muted-foreground/60" />
        <p className="text-sm text-muted-foreground">
          {data.message ??
            t(
              "This run's workspace has no files yet.",
              "Der Workspace dieses Laufs enthält noch keine Dateien.",
            )}
        </p>
      </Panel>
    );
  }

  return (
    <div className="grid gap-4 md:grid-cols-[minmax(0,220px)_minmax(0,1fr)]">
      <Panel className="max-h-[480px] overflow-auto p-2">
        <ul className="space-y-0.5">
          {data.files.map((f) => (
            <li key={f.path}>
              <button
                type="button"
                onClick={() => setSelected(f.path)}
                className={cn(
                  "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-xs transition hover:bg-muted/60",
                  selected === f.path && "bg-muted text-foreground",
                )}
                title={f.path}
              >
                <span className="truncate font-mono">{f.path}</span>
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  {formatFileSize(f.size)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </Panel>
      <Panel className="max-h-[480px] overflow-auto p-3">
        {!selected ? (
          <div className="py-10 text-center text-xs text-muted-foreground">
            {t("Select a file to view its contents.", "Datei auswählen, um den Inhalt zu sehen.")}
          </div>
        ) : content.isPending ? (
          <div className="py-10 text-center text-xs text-muted-foreground">
            {t("Loading…", "Wird geladen…")}
          </div>
        ) : (
          <>
            {content.data?.truncated && (
              <div className="mb-2 text-[11px] text-[color:var(--status-warning)]">
                {t("File truncated for display.", "Datei für die Anzeige gekürzt.")}
              </div>
            )}
            <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed">
              {content.data?.content ?? ""}
            </pre>
          </>
        )}
      </Panel>
    </div>
  );
}

// ---------- Live Log ----------
//
// Real live run transcript: the current run (useRun, live-patched by the
// "run.status" WS event in src/lib/live/apply-event.ts) plus this agent's
// slice of the activity feed (useActivity, live-patched by "activity.logged").
// No client-side simulation — every line here comes from the backend.

function isTerminalRunState(state: string): boolean {
  return state === "done" || state === "failed" || state === "interrupted";
}

function LiveLog({ agentId, runId }: { agentId: string; runId: string | null }) {
  const t = useT();
  const run = useRun(runId);
  // Asked of the server for THIS agent, and raised by "show more": filtering a
  // global page client-side meant a quiet agent showed an empty feed while its
  // own history sat just past the cut.
  const [feedLimit, setFeedLimit] = useState(50);
  const activity = useActivity({ agentId, limit: feedLimit });
  const agentActivity = activity.data ?? [];
  const mayHaveMore = agentActivity.length >= feedLimit;
  const live = !!runId && !!run.data && !isTerminalRunState(run.data.state);

  return (
    <div className="space-y-4">
      <Panel className="p-5">
        <div className="mb-3 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "relative inline-flex h-2.5 w-2.5 rounded-full",
                live ? "bg-[color:var(--status-running)]" : "bg-muted-foreground/60",
              )}
            >
              {live && (
                <span className="absolute inset-0 animate-ping rounded-full bg-[color:var(--status-running)] opacity-60" />
              )}
            </span>
            <div className="text-xs uppercase tracking-wider text-muted-foreground">
              {t("Live Log · current run", "Live-Log · aktueller Lauf")}
            </div>
          </div>
          {run.data && <RunStateBadge state={run.data.state} />}
        </div>
        {runId ? (
          <RunTranscript run={run.data} pending={run.isPending} />
        ) : (
          <div className="py-10 text-center text-xs text-muted-foreground">
            {t(
              "No run in progress. Start one with “Run now”, or wait for the schedule.",
              "Kein Lauf aktiv. Starte einen mit „Jetzt ausführen“ — oder warte auf den Zeitplan.",
            )}
          </div>
        )}
      </Panel>
      <ActivityFeed
        items={agentActivity}
        onShowMore={mayHaveMore ? () => setFeedLimit((n) => n + 50) : undefined}
      />
    </div>
  );
}

function RunTranscript({ run, pending }: { run: RunDTO | undefined; pending: boolean }) {
  const t = useT();
  if (pending) {
    return (
      <div className="py-10 text-center text-xs text-muted-foreground">
        {t("Loading run…", "Lauf wird geladen…")}
      </div>
    );
  }
  if (!run) {
    return (
      <div className="py-10 text-center text-xs text-muted-foreground">
        {t("Run not found.", "Lauf nicht gefunden.")}
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="rounded-md border border-border bg-background/40 px-2 py-1 font-mono">
          {t("run", "Lauf")} {run.id.slice(0, 8)}
        </span>
        {run.phase && (
          <span className="rounded-md border border-border bg-background/40 px-2 py-1">
            {t("phase", "Phase")}: {run.phase}
          </span>
        )}
      </div>
      {run.state === "waiting_for_input" && <WaitingForInputCallout run={run} />}
      {run.liveAnswer && (
        <LiveAnswerPane text={run.liveAnswer} live={!isTerminalRunState(run.state)} />
      )}
      {run.transcript && run.transcript.length > 0 && (
        <LiveTranscriptPane transcript={run.transcript} live={!isTerminalRunState(run.state)} />
      )}
      {run.components && run.components.length > 0 && (
        <div className="space-y-2">
          {run.components.map((c, i) => {
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
      <div className="max-h-[360px] min-h-[160px] overflow-auto rounded-md border border-border bg-background/60 p-3 font-mono text-[12px] leading-relaxed">
        {run.toolCalls.length === 0 ? (
          <div className="py-6 text-center text-xs text-muted-foreground">
            {t("No tool calls yet.", "Noch keine Tool-Aufrufe.")}
          </div>
        ) : (
          run.toolCalls.map((call, i) => <ToolCallRow key={i} call={call} />)
        )}
      </div>
    </div>
  );
}

// Growing pane for the model's own answer as it streams in (Stage 2 token
// streaming), fed by "run.token_delta" events (see apply-event.ts) -- the
// SAME terminal look and same scroll-position rule as LiveTranscriptPane
// below, but rendering one continuous string rather than joining an array
// of separate stdout lines, since token fragments are words of one answer,
// not log lines. Applies to every runtime: unlike run.output_delta (raw
// container stdout, container runtimes only), run.token_delta comes from
// wherever the model call itself runs -- in-process or isolated.
function LiveAnswerPane({ text, live }: { text: string; live: boolean }) {
  const t = useT();
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [text]);

  function onScroll() {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 32;
  }

  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-muted-foreground">
        {live && (
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-[color:var(--status-running)]">
            <span className="absolute inset-0 animate-ping rounded-full bg-[color:var(--status-running)] opacity-60" />
          </span>
        )}
        {t("Answer", "Antwort")}
      </div>
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="max-h-[280px] overflow-auto rounded-md border border-border bg-black/90 p-3 font-mono text-[11px] leading-relaxed text-emerald-300"
      >
        <pre className="whitespace-pre-wrap break-words">{text}</pre>
      </div>
    </div>
  );
}

// Growing, terminal-like output pane for a containerized runtime's raw
// stdout, fed by "run.output_delta" events (see apply-event.ts). Auto-scrolls
// to the bottom on every new chunk UNLESS the operator has scrolled up to
// read something earlier -- checked by distance-from-bottom right before the
// chunk that triggered the re-render is appended, so a deliberate scroll-back
// is never yanked back down mid-read.
function LiveTranscriptPane({ transcript, live }: { transcript: string[]; live: boolean }) {
  const t = useT();
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [transcript]);

  function onScroll() {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 32;
  }

  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-muted-foreground">
        {live && (
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-[color:var(--status-running)]">
            <span className="absolute inset-0 animate-ping rounded-full bg-[color:var(--status-running)] opacity-60" />
          </span>
        )}
        {t("Output", "Ausgabe")}
      </div>
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="max-h-[280px] overflow-auto rounded-md border border-border bg-black/90 p-3 font-mono text-[11px] leading-relaxed text-emerald-300"
      >
        <pre className="whitespace-pre-wrap break-words">{transcript.join("\n")}</pre>
      </div>
    </div>
  );
}

// Callout for a run suspended in `waiting_for_input` (RunStateBadge already
// renders the amber badge for this state above — this adds the answer
// affordance below it). Shows the real backend `run.question`; older runs
// that predate the field fall back to a generic prompt, but the textarea and
// submit path are identical either way.
function WaitingForInputCallout({ run }: { run: RunDTO }) {
  const t = useT();
  const answerRun = useAnswerRun(run.id);
  const [answer, setAnswer] = useState("");

  function submit() {
    const trimmed = answer.trim();
    if (!trimmed) return;
    answerRun.mutate(trimmed, {
      onSuccess: () => {
        setAnswer("");
        toast.success(t("Answer sent", "Antwort gesendet"));
      },
      onError: () => toast.error(t("Couldn't send answer", "Antwort konnte nicht gesendet werden")),
    });
  }

  return (
    <div className="rounded-md border border-[color:var(--status-warning)]/40 bg-[color:var(--status-warning)]/10 p-3">
      <div className="flex items-start gap-2 text-sm text-foreground/90">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[color:var(--status-warning)]" />
        <p>
          {run.question ??
            t("The agent is waiting for your input.", "Der Agent wartet auf deine Eingabe.")}
        </p>
      </div>
      <textarea
        rows={3}
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
        placeholder={t("Type your answer…", "Antwort eingeben…")}
        className="mt-3 w-full rounded-md border border-border bg-background/40 px-3 py-2 text-sm outline-none focus:border-primary/50"
      />
      <div className="mt-2 flex justify-end">
        <button
          type="button"
          onClick={submit}
          disabled={answerRun.isPending || !answer.trim()}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
        >
          <MessageSquare className="h-3.5 w-3.5" />
          {answerRun.isPending
            ? t("Sending…", "Wird gesendet…")
            : t("Send answer", "Antwort senden")}
        </button>
      </div>
    </div>
  );
}

function RunStateBadge({ state }: { state: string }) {
  const map: Record<string, { icon: React.ReactNode; color: string }> = {
    queued: {
      icon: <Clock className="h-3 w-3" />,
      color: "text-muted-foreground border-border bg-background/40",
    },
    running: {
      icon: <Play className="h-3 w-3" />,
      color:
        "text-[color:var(--status-running)] border-[color:var(--status-running)]/40 bg-[color:var(--status-running)]/10",
    },
    waiting_for_input: {
      icon: <AlertTriangle className="h-3 w-3" />,
      color:
        "text-[color:var(--status-warning)] border-[color:var(--status-warning)]/40 bg-[color:var(--status-warning)]/10",
    },
    waiting_for_approval: {
      icon: <AlertTriangle className="h-3 w-3" />,
      color:
        "text-[color:var(--status-warning)] border-[color:var(--status-warning)]/40 bg-[color:var(--status-warning)]/10",
    },
    done: {
      icon: <CheckCircle2 className="h-3 w-3" />,
      color: "text-primary border-primary/40 bg-primary/10",
    },
    failed: {
      icon: <XCircle className="h-3 w-3" />,
      color:
        "text-[color:var(--status-error)] border-[color:var(--status-error)]/40 bg-[color:var(--status-error)]/10",
    },
    interrupted: {
      icon: <XCircle className="h-3 w-3" />,
      color:
        "text-[color:var(--status-error)] border-[color:var(--status-error)]/40 bg-[color:var(--status-error)]/10",
    },
  };
  const m = map[state] ?? {
    icon: <Clock className="h-3 w-3" />,
    color: "text-muted-foreground border-border bg-background/40",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium",
        m.color,
      )}
    >
      {m.icon}
      {state}
    </span>
  );
}

// toolCalls is `Array<Record<string, unknown>>` with no fixed schema on the
// wire yet — render whichever recognizable keys are present and fall back to
// the raw JSON so nothing silently disappears.
function ToolCallRow({ call }: { call: Record<string, unknown> }) {
  const name =
    (call.tool as string | undefined) ??
    (call.name as string | undefined) ??
    (call.toolName as string | undefined) ??
    "tool call";
  const status = (call.status as string | undefined) ?? (call.state as string | undefined);
  const detail =
    (call.output as string | undefined) ??
    (call.result as string | undefined) ??
    (call.error as string | undefined) ??
    (typeof call.input === "string" ? call.input : undefined);
  return (
    <div className="flex flex-wrap items-start gap-2 border-b border-border/50 py-1 last:border-0">
      <Wrench className="mt-0.5 h-3 w-3 shrink-0 text-primary" />
      <span className="font-medium text-foreground/90">{name}</span>
      {status && (
        <span className="rounded border border-border px-1 text-[10px] uppercase text-muted-foreground">
          {status}
        </span>
      )}
      <span className="min-w-0 flex-1 truncate text-muted-foreground">
        {detail ?? JSON.stringify(call)}
      </span>
    </div>
  );
}

function ActivityFeed({ items, onShowMore }: { items: ActivityItem[]; onShowMore?: () => void }) {
  const t = useT();
  return (
    <Panel className="p-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <span className="text-xs uppercase tracking-wider text-muted-foreground">
          {t("Activity feed", "Aktivitäts-Feed")}
        </span>
        <span className="font-mono text-[10px] text-muted-foreground">{items.length}</span>
      </div>
      {items.length === 0 ? (
        <div className="py-8 text-center text-xs text-muted-foreground">
          {t("No activity yet for this agent.", "Noch keine Aktivität für diesen Agenten.")}
        </div>
      ) : (
        <>
          <ul className="divide-y divide-border">
            {items.map((it) => (
              <ActivityRow key={it.id} item={it} />
            ))}
          </ul>
          {onShowMore && (
            <button
              type="button"
              onClick={onShowMore}
              className="mt-3 w-full rounded-md border border-border py-2 text-xs text-muted-foreground transition hover:bg-muted/40"
            >
              {t("Show older", "Ältere anzeigen")}
            </button>
          )}
        </>
      )}
    </Panel>
  );
}

function ActivityRow({ item }: { item: ActivityItem }) {
  const t = useT();
  const color =
    item.status === "success"
      ? "var(--status-running)"
      : item.status === "warning"
        ? "var(--status-warning)"
        : item.status === "error"
          ? "var(--status-error)"
          : "var(--primary)";
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span
          className="rounded-full px-2 py-0.5 text-[10px] font-medium"
          style={{
            color,
            background: `color-mix(in oklab, ${color} 12%, transparent)`,
            border: `1px solid color-mix(in oklab, ${color} 30%, transparent)`,
          }}
        >
          {item.status}
        </span>
        {item.cacheHit && (
          <span
            className="rounded-full border border-border px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
            title={t(
              "Answered from the department cache, not a fresh model call",
              "Aus dem Department-Cache beantwortet, kein frischer Modell-Aufruf",
            )}
          >
            {t("cached", "aus Cache")}
          </span>
        )}
        <span className="min-w-0 flex-1 truncate text-foreground/90">{item.message}</span>
        <span className="font-mono text-xs text-muted-foreground">{item.time}</span>
      </div>
      {item.detail && <p className="mt-1 text-xs text-muted-foreground">{item.detail}</p>}
    </li>
  );
}
