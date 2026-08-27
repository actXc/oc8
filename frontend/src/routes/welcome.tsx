import { createFileRoute, Navigate } from "@tanstack/react-router";
import { useState } from "react";
import { PublicAuthLayout } from "@/components/public-auth-layout";
import { useAuth } from "@/lib/hooks";
import { useMay } from "@/lib/governance-hooks";
import { OnboardingWizard } from "@/components/onboarding/onboarding-wizard";

export const Route = createFileRoute("/welcome")({
  component: WelcomePage,
});

function WelcomePage() {
  const { data: me } = useAuth();
  const may = useMay();
  const [mood, setMood] = useState<"idle" | "celebrating">("idle");

  if (me && me.onboardingStatus && me.onboardingStatus !== "pending") {
    return <Navigate to="/" />;
  }
  if (me && !may("department:manage")) {
    return <Navigate to="/" />;
  }

  return (
    <PublicAuthLayout mood={mood}>
      <OnboardingWizard
        onStepChange={(step) => setMood(step === "done" ? "celebrating" : "idle")}
      />
    </PublicAuthLayout>
  );
}
