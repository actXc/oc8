import { createFileRoute, Navigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Panel } from "@/components/app-shell";
import { PublicAuthLayout } from "@/components/public-auth-layout";
import { useT } from "@/lib/i18n";
import { useAgents, useAuth, useDepartments, type AgentDetail } from "@/lib/hooks";
import { useMay } from "@/lib/governance-hooks";
import { OrgStep } from "@/components/onboarding/org-step";
import { DepartmentStep } from "@/components/onboarding/department-step";
import { AgentIdentityStep } from "@/components/onboarding/agent-identity-step";
import { AgentModelStep } from "@/components/onboarding/agent-model-step";
import { ToolConnectStep } from "@/components/onboarding/tool-connect-step";
import { GuardrailsStep } from "@/components/onboarding/guardrails-step";
import { DoneStep } from "@/components/onboarding/done-step";
import { StepDots } from "@/components/onboarding/step-dots";
import { SkipLink } from "@/components/onboarding/skip-link";
import { Mascot, type OnboardingStepId } from "@/components/onboarding/mascot";
import type { AgentIdentity } from "@/components/agent-identity-fields";

export const Route = createFileRoute("/welcome")({
  component: WelcomePage,
});

const STEP_ORDER: OnboardingStepId[] = [
  "org",
  "department",
  "agent-identity",
  "agent-model",
  "tool-connect",
  "guardrails",
];
const STEP_LABEL: Record<OnboardingStepId, [string, string]> = {
  org: ["Workspace", "Workspace"],
  department: ["Department", "Abteilung"],
  "agent-identity": ["Agent", "Agent"],
  "agent-model": ["Model", "Modell"],
  "tool-connect": ["Tool", "Tool"],
  guardrails: ["Guardrails", "Guardrails"],
  done: ["Done", "Fertig"],
};

function WelcomePage() {
  const t = useT();
  const { data: me } = useAuth();
  const may = useMay();
  // Onboarding resume-check needs every row, not a paginated list view.
  const { data: departmentsPage } = useDepartments({ pageSize: 200 });
  const { data: agentsPage, isSuccess: agentsLoaded } = useAgents({ pageSize: 200 });
  const departments = departmentsPage?.items ?? [];
  const agents = agentsPage?.items ?? [];
  const [step, setStep] = useState<OnboardingStepId>("org");
  const [pendingIdentity, setPendingIdentity] = useState<AgentIdentity | null>(null);
  const [createdAgent, setCreatedAgent] = useState<AgentDetail | null>(null);
  const [connectionId, setConnectionId] = useState<string | null>(null);
  const hasResumedRef = useRef(false);

  // Resume past already-completed steps on reload/revisit -- but only the
  // FIRST time agent data settles, not on every change, or this would also
  // fire the moment the user creates their first agent mid-session and skip
  // AgentModelStep's own "ready to work" confirmation beat. Scoped to the
  // initial step so it never snaps a further-along user backwards.
  useEffect(() => {
    if (hasResumedRef.current || !agentsLoaded) return;
    hasResumedRef.current = true;
    if (agents.length > 0) {
      setStep((current) => (current === "org" ? "tool-connect" : current));
    }
  }, [agentsLoaded, agents.length]);

  if (me && me.onboardingStatus && me.onboardingStatus !== "pending") {
    return <Navigate to="/" />;
  }
  if (me && !may("department:manage")) {
    return <Navigate to="/" />;
  }

  const department = departments[0];
  const mood = step === "done" ? "celebrating" : "idle";

  return (
    <PublicAuthLayout mood={mood}>
      <div className="space-y-6">
        <StepDots steps={STEP_ORDER} labels={STEP_LABEL} current={step} />
        {step !== "done" && <SkipLink />}
        <Panel className="flex items-start gap-4 p-5">
          <img src="/octopus_oc8.svg" alt="" className="h-12 w-12 shrink-0" />
          <div className="rounded-lg bg-background/40 px-4 py-3 text-sm">
            <Mascot step={step} />
          </div>
        </Panel>

        {step === "org" && <OrgStep onDone={() => setStep("department")} />}

        {step === "department" && department && (
          <DepartmentStep department={department} onDone={() => setStep("agent-identity")} />
        )}
        {step === "department" && !department && (
          <Panel className="p-6 text-sm text-muted-foreground">
            {t("Loading your department…", "Deine Abteilung wird geladen …")}
          </Panel>
        )}

        {step === "agent-identity" && department && (
          <AgentIdentityStep
            departmentId={department.id}
            onDone={(identity) => {
              setPendingIdentity(identity);
              setStep("agent-model");
            }}
          />
        )}

        {step === "agent-model" && pendingIdentity && (
          <AgentModelStep
            identity={pendingIdentity}
            onDone={(agent) => {
              setCreatedAgent(agent);
              setStep("tool-connect");
            }}
          />
        )}

        {step === "tool-connect" && (
          <ToolConnectStep
            onDone={(newConnectionId) => {
              setConnectionId(newConnectionId);
              // A non-MCP setup plugin has no connection for Guardrails to
              // gate on -- go straight to done instead of dead-ending there.
              setStep(newConnectionId ? "guardrails" : "done");
            }}
            onSkip={() => setStep("done")}
          />
        )}

        {step === "guardrails" && (department || createdAgent) && connectionId && (
          <GuardrailsStep
            departmentId={createdAgent?.departmentId ?? department?.id ?? ""}
            connectionId={connectionId}
            onDone={() => setStep("done")}
          />
        )}

        {step === "done" && <DoneStep />}
      </div>
    </PublicAuthLayout>
  );
}
