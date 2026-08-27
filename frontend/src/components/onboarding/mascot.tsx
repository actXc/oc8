import { useT } from "@/lib/i18n";

export type OnboardingStepId =
  | "org"
  | "department"
  | "agent-identity"
  | "agent-model"
  | "tool-connect"
  | "guardrails"
  | "done";

export function Mascot({ step }: { step: OnboardingStepId }) {
  const t = useT();
  const copy: Record<OnboardingStepId, [string, string]> = {
    org: [
      "Welcome aboard! First, what should we call your company?",
      "Willkommen an Bord! Wie soll deine Firma heißen?",
    ],
    department: [
      "Nicely named! Now let's put a name on the department you already have.",
      "Schön benannt! Geben wir jetzt deiner Abteilung einen Namen.",
    ],
    "agent-identity": [
      "Every department needs someone working in it — let's hire your first agent.",
      "Jede Abteilung braucht jemanden, der dort arbeitet — stellen wir deinen ersten Agenten ein.",
    ],
    "agent-model": [
      "Great! Now let's give them a brain — pick or connect a model.",
      "Super! Jetzt braucht er ein Gehirn — wähle oder verbinde ein Modell.",
    ],
    "tool-connect": [
      "One more thing: connect a tool so your agent has something real to work with.",
      "Noch etwas: Verbinde ein Tool, damit dein Agent etwas hat, womit er wirklich arbeiten kann.",
    ],
    guardrails: [
      "Let's set some guardrails so it knows how far it can go on its own.",
      "Setzen wir ein paar Guardrails, damit er weiß, wie weit er allein gehen darf.",
    ],
    done: [
      "Your office is open! Everything you set up is real and ready.",
      "Dein Büro ist eröffnet! Alles, was du eingerichtet hast, ist echt und einsatzbereit.",
    ],
  };
  return <p>{t(...copy[step])}</p>;
}
