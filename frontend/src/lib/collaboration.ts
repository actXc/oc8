// Cross-department collaboration mock data: contracts, handoffs, flows, runs.

export type HandoffStatus =
  | "pending"
  | "accepted"
  | "in_progress"
  | "completed"
  | "rejected"
  | "expired";

export interface ContractField {
  name: string;
  type: "string" | "number" | "enum" | "array";
  required?: boolean;
  example?: string | number;
}

export interface IntakeContract {
  id: string;
  type: string; // e.g. "project.kickoff"
  route: string; // e.g. "Team Lead"
  gate: "auto" | "approval";
  fields: ContractField[];
}

export interface DepartmentContracts {
  departmentId: string;
  emits: string[]; // event types
  intakes: IntakeContract[];
}

export const departmentContracts: DepartmentContracts[] = [
  {
    departmentId: "vertrieb",
    emits: ["sales.deal.won", "sales.deal.lost", "sales.quote.sent"],
    intakes: [
      {
        id: "in-sales-rework",
        type: "sales.deal.rework",
        route: "Team Lead",
        gate: "auto",
        fields: [
          { name: "deal_id", type: "string", required: true, example: "D-4821" },
          { name: "reason", type: "string", required: true },
        ],
      },
    ],
  },
  {
    departmentId: "entwicklung",
    emits: ["dev.project.start", "dev.project.completed", "dev.release.shipped"],
    intakes: [
      {
        id: "in-eng-kickoff",
        type: "project.kickoff",
        route: "Team Lead",
        gate: "approval",
        fields: [
          { name: "customer", type: "string", required: true, example: "Bauer GmbH" },
          { name: "scope", type: "array", required: true },
          { name: "budget_eur", type: "number", required: true, example: 74000 },
          { name: "deadline", type: "string", example: "2026-09-30" },
        ],
      },
    ],
  },
  {
    departmentId: "buchhaltung",
    emits: ["finance.invoice.paid", "finance.invoice.overdue"],
    intakes: [
      {
        id: "in-fin-invoice",
        type: "finance.invoice.create",
        route: "Team Lead",
        gate: "auto",
        fields: [
          { name: "customer", type: "string", required: true },
          { name: "amount_eur", type: "number", required: true, example: 74000 },
          { name: "project_ref", type: "string", required: true },
        ],
      },
    ],
  },
  { departmentId: "marketing", emits: ["marketing.lead.captured"], intakes: [] },
  { departmentId: "hr", emits: [], intakes: [] },
  { departmentId: "support", emits: ["support.ticket.escalated"], intakes: [] },
];

export function contractsFor(departmentId: string): DepartmentContracts {
  return (
    departmentContracts.find((d) => d.departmentId === departmentId) ?? {
      departmentId,
      emits: [],
      intakes: [],
    }
  );
}

export interface HandoffSubtask {
  id: string;
  title: string;
  agentId?: string;
  status: "todo" | "in_progress" | "done";
}

export interface Handoff {
  id: string;
  type: string;
  sourceDept: string;
  targetDept: string;
  status: HandoffStatus;
  createdBy: string; // agent id
  createdAt: string;
  age: string;
  gate: "auto" | "approval";
  payload: Record<string, unknown>;
  attachments: { name: string; kb?: string }[];
  timeline: { label: string; at?: string; done: boolean }[];
  subtasks: HandoffSubtask[];
  flowRunId?: string;
  stage?: string;
}

export const handoffs: Handoff[] = [
  {
    id: "ho-1",
    type: "project.kickoff",
    sourceDept: "vertrieb",
    targetDept: "entwicklung",
    status: "pending",
    createdBy: "vera",
    createdAt: "14:38",
    age: "12 min",
    gate: "approval",
    payload: {
      customer: "Bauer GmbH",
      scope: ["Onboarding portal", "SSO", "Reporting v1"],
      budget_eur: 74000,
      deadline: "2026-09-30",
    },
    attachments: [
      { name: "Quote OFF-2026-0714.pdf" },
      { name: "Requirements.md", kb: "kb-sales" },
    ],
    timeline: [
      { label: "Created", at: "14:38", done: true },
      { label: "Awaiting gate approval", at: "—", done: false },
      { label: "Accepted", done: false },
      { label: "In progress", done: false },
      { label: "Completed", done: false },
    ],
    subtasks: [
      { id: "st-1", title: "Discovery workshop", status: "todo", agentId: "dex" },
      { id: "st-2", title: "Architecture draft", status: "todo", agentId: "ada" },
      { id: "st-3", title: "Test plan", status: "todo", agentId: "kern" },
    ],
    flowRunId: "run-1",
    stage: "kickoff",
  },
  {
    id: "ho-2",
    type: "dev.project.start",
    sourceDept: "entwicklung",
    targetDept: "entwicklung",
    status: "in_progress",
    createdBy: "dex",
    createdAt: "Yesterday",
    age: "1 d",
    gate: "auto",
    payload: {
      customer: "Nordheim AG",
      milestone: "M1 — Setup",
      budget_eur: 42000,
    },
    attachments: [{ name: "Kickoff notes.md" }],
    timeline: [
      { label: "Created", at: "Yesterday 09:12", done: true },
      { label: "Accepted", at: "Yesterday 09:14", done: true },
      { label: "In progress", at: "Yesterday 10:00", done: true },
      { label: "Completed", done: false },
    ],
    subtasks: [
      { id: "st-a", title: "Sprint 1 backlog", status: "done", agentId: "dex" },
      { id: "st-b", title: "CI pipeline", status: "in_progress", agentId: "ada" },
      { id: "st-c", title: "Environments", status: "todo", agentId: "kern" },
    ],
    flowRunId: "run-2",
    stage: "build",
  },
  {
    id: "ho-3",
    type: "finance.invoice.create",
    sourceDept: "entwicklung",
    targetDept: "buchhaltung",
    status: "completed",
    createdBy: "dex",
    createdAt: "Mon",
    age: "3 d",
    gate: "auto",
    payload: {
      customer: "Meier AG",
      amount_eur: 28900,
      project_ref: "P-2026-004",
    },
    attachments: [{ name: "Delivery receipt.pdf" }],
    timeline: [
      { label: "Created", at: "Mon 11:04", done: true },
      { label: "Accepted", at: "Mon 11:05", done: true },
      { label: "In progress", at: "Mon 11:20", done: true },
      { label: "Completed", at: "Tue 09:47", done: true },
    ],
    subtasks: [
      { id: "st-x", title: "Draft invoice", status: "done", agentId: "fin" },
      { id: "st-y", title: "Accounting transfer", status: "done", agentId: "cent" },
    ],
    flowRunId: "run-3",
    stage: "invoice",
  },
];

export interface FlowStage {
  id: string;
  label: string;
  handoffType: string;
  sourceDept: string;
  targetDept: string;
  gate: "auto" | "approval";
  timeout: string;
}

export interface FlowDef {
  id: string;
  name: string;
  version: string;
  status: "active" | "draft" | "suspended";
  departments: string[];
  runs: number;
  stages: FlowStage[];
  compensations: { from: string; toDept: string; type: string }[];
}

export const flows: FlowDef[] = [
  {
    id: "flow-q2c",
    name: "Quote-to-Cash",
    version: "v1.0.0",
    status: "active",
    departments: ["vertrieb", "entwicklung", "buchhaltung"],
    runs: 24,
    stages: [
      {
        id: "s-kickoff",
        label: "Kickoff",
        handoffType: "project.kickoff",
        sourceDept: "vertrieb",
        targetDept: "entwicklung",
        gate: "approval",
        timeout: "72h → escalate",
      },
      {
        id: "s-build",
        label: "Build",
        handoffType: "dev.project.start",
        sourceDept: "entwicklung",
        targetDept: "entwicklung",
        gate: "auto",
        timeout: "30d",
      },
      {
        id: "s-invoice",
        label: "Invoice",
        handoffType: "finance.invoice.create",
        sourceDept: "entwicklung",
        targetDept: "buchhaltung",
        gate: "auto",
        timeout: "24h",
      },
    ],
    compensations: [
      { from: "s-kickoff", toDept: "vertrieb", type: "sales.deal.rework" },
    ],
  },
  {
    id: "flow-onb",
    name: "Customer Onboarding",
    version: "v0.3.1",
    status: "draft",
    departments: ["vertrieb", "support"],
    runs: 0,
    stages: [
      {
        id: "s-welcome",
        label: "Welcome",
        handoffType: "customer.welcome",
        sourceDept: "vertrieb",
        targetDept: "support",
        gate: "auto",
        timeout: "24h",
      },
    ],
    compensations: [],
  },
];

export interface FlowRun {
  id: string;
  flowId: string;
  currentStageId: string | null;
  customer: string;
  startedAt: string;
  stageState: Record<string, "pending" | "gated" | "active" | "done">;
  events: { at: string; label: string }[];
}

export const flowRuns: FlowRun[] = [
  {
    id: "run-1",
    flowId: "flow-q2c",
    currentStageId: "s-kickoff",
    customer: "Bauer GmbH",
    startedAt: "Today 14:38",
    stageState: { "s-kickoff": "gated", "s-build": "pending", "s-invoice": "pending" },
    events: [
      { at: "14:38", label: "flow.run.started" },
      { at: "14:38", label: "stage.entered · Kickoff" },
      { at: "14:38", label: "handoff.created · project.kickoff" },
      { at: "14:39", label: "gate.waiting · approval required" },
    ],
  },
  {
    id: "run-2",
    flowId: "flow-q2c",
    currentStageId: "s-build",
    customer: "Nordheim AG",
    startedAt: "Yesterday 09:12",
    stageState: { "s-kickoff": "done", "s-build": "active", "s-invoice": "pending" },
    events: [
      { at: "Yesterday 09:12", label: "flow.run.started" },
      { at: "Yesterday 09:14", label: "gate.approved · Kickoff" },
      { at: "Yesterday 10:00", label: "stage.entered · Build" },
    ],
  },
  {
    id: "run-3",
    flowId: "flow-q2c",
    currentStageId: null,
    customer: "Meier AG",
    startedAt: "Mon 11:04",
    stageState: { "s-kickoff": "done", "s-build": "done", "s-invoice": "done" },
    events: [
      { at: "Mon 11:04", label: "flow.run.started" },
      { at: "Mon 15:00", label: "stage.done · Build" },
      { at: "Tue 09:47", label: "flow.run.completed" },
    ],
  },
];

export function flowById(id: string) {
  return flows.find((f) => f.id === id);
}
export function runById(id: string) {
  return flowRuns.find((r) => r.id === id);
}
export function handoffById(id: string) {
  return handoffs.find((h) => h.id === id);
}

export const statusMeta: Record<HandoffStatus, { label: string; color: string }> = {
  pending: { label: "pending", color: "var(--status-warning)" },
  accepted: { label: "accepted", color: "oklch(0.7 0.15 240)" },
  in_progress: { label: "in progress", color: "var(--primary)" },
  completed: { label: "completed", color: "var(--status-running)" },
  rejected: { label: "rejected", color: "var(--status-error)" },
  expired: { label: "expired", color: "var(--muted-foreground)" },
};

export function validateFlowContracts(flow: FlowDef) {
  const problems: { stageId: string; message: string }[] = [];
  for (const st of flow.stages) {
    const tc = contractsFor(st.targetDept);
    if (!tc.intakes.some((i) => i.type === st.handoffType)) {
      problems.push({
        stageId: st.id,
        message: `${st.targetDept} does not declare intake ${st.handoffType} — add it in Department Settings → Collaboration`,
      });
    }
  }
  return problems;
}

export function flowToYaml(flow: FlowDef): string {
  const lines: string[] = [];
  lines.push(`name: ${flow.name}`);
  lines.push(`version: ${flow.version}`);
  lines.push(`status: ${flow.status}`);
  lines.push(`departments: [${flow.departments.join(", ")}]`);
  lines.push(`stages:`);
  for (const s of flow.stages) {
    lines.push(`  - id: ${s.id}`);
    lines.push(`    label: ${s.label}`);
    lines.push(`    handoff: ${s.handoffType}`);
    lines.push(`    from: ${s.sourceDept}`);
    lines.push(`    to: ${s.targetDept}`);
    lines.push(`    gate: ${s.gate}`);
    lines.push(`    timeout: "${s.timeout}"`);
  }
  if (flow.compensations.length) {
    lines.push(`compensations:`);
    for (const c of flow.compensations) {
      lines.push(`  - from: ${c.from}`);
      lines.push(`    to: ${c.toDept}`);
      lines.push(`    emit: ${c.type}`);
    }
  }
  return lines.join("\n");
}