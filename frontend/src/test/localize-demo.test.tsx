import { describe, expect, it } from "vitest";
import { localizeAgent, localizeDepartment, localizeLabeled } from "@/lib/localize-demo";
import type { Agent, Department } from "@/lib/mock-data";

const agent: Agent = {
  id: "vera",
  name: "Vera",
  role: "Sales Assistant",
  llm: "Claude 3.5 Sonnet",
  provider: "Claude",
  status: "warning",
  tools: ["CRM"],
  lastAction: "Prepared quote",
  lastRun: "2 min ago",
  tasksToday: 1,
  guardrails: ["Max quote value €10,000"],
  schedule: "Mon–Fri, 08:00–18:00",
  avatarColor: "oklch(0.75 0.15 30)",
  roleTranslations: { de: "Verkaufsassistentin" },
  lastActionTranslations: { de: "Angebot vorbereitet" },
  lastRunTranslations: { de: "vor 2 Min." },
  scheduleTranslations: { de: "Mo–Fr, 08:00–18:00" },
  guardrailsTranslations: { de: ["Max. Angebotswert 10.000 €"] },
};

const dept: Department = {
  id: "vertrieb",
  name: "Sales",
  icon: "sales",
  goal: "Fill the pipeline",
  okr: "+30% qualified leads",
  kpiLabel: "Leads today",
  kpiValue: "18",
  activity: 82,
  accent: "oklch(0.75 0.15 30)",
  promptCachingEnabled: true,
  nameTranslations: { de: "Vertrieb" },
  goalTranslations: { de: "Die Pipeline füllen" },
  okrTranslations: { de: "+30 % qualifizierte Leads" },
  kpiLabelTranslations: { de: "Leads heute" },
};

describe("localize-demo", () => {
  it("leaves English canonical fields alone for lang=en", () => {
    expect(localizeAgent(agent, "en").role).toBe("Sales Assistant");
    expect(localizeDepartment(dept, "en").name).toBe("Sales");
  });

  it("applies German overlays for lang=de", () => {
    const a = localizeAgent(agent, "de");
    expect(a.role).toBe("Verkaufsassistentin");
    expect(a.lastAction).toBe("Angebot vorbereitet");
    expect(a.guardrails[0]).toBe("Max. Angebotswert 10.000 €");

    const d = localizeDepartment(dept, "de");
    expect(d.name).toBe("Vertrieb");
    expect(d.kpiLabel).toBe("Leads heute");
  });

  it("localizes approval-shaped rows", () => {
    const row = localizeLabeled(
      {
        title: "Vera wants to send a quote",
        detail: "Bauer GmbH",
        titleTranslations: { de: "Vera möchte ein Angebot senden" },
        detailTranslations: { de: "Bauer GmbH — Wartungsvertrag" },
      },
      "de",
    );
    expect(row.title).toBe("Vera möchte ein Angebot senden");
  });
});
