"""German translations for the ACME demo seed.

English remains the canonical text in `seed/__init__.py` columns. These maps
are written under `presentation["i18n"]["de"]` / `payload["i18n"]["de"]` /
`activity_event.i18n["de"]` so the UI can flip language without a second
tenant. Keys match the seed slugs (agent id, department id, …).
"""

from __future__ import annotations

from typing import Any

DEPARTMENTS_DE: dict[str, dict[str, str]] = {
    "vertrieb": {
        "name": "Vertrieb",
        "goal": "Die Pipeline füllen",
        "okr": "+30 % qualifizierte Leads bis Ende Q3",
        "kpi_label": "Leads heute",
    },
    "entwicklung": {
        "name": "Entwicklung",
        "goal": "Das Feature-Backlog ausliefern",
        "okr": "12 Story Points / Sprint · Release 2.4",
        "kpi_label": "PRs in Review",
    },
    "marketing": {
        "name": "Marketing",
        "goal": "Kampagnen & Content",
        "okr": "2 Kampagnen live, CAC < 180 €",
        "kpi_label": "Kampagnen live",
    },
    "buchhaltung": {
        "name": "Buchhaltung",
        "goal": "Rechnungen & Reports",
        "okr": "Monatsabschluss bis BD+3",
        "kpi_label": "Belege verarbeitet",
    },
    "hr": {
        "name": "Personal",
        "goal": "Bewerbungen sichten",
        "okr": "Time-to-Interview < 5 Tage",
        "kpi_label": "CVs im Screening",
    },
    "support": {
        "name": "Support",
        "goal": "Tickets lösen",
        "okr": "Erste Antwort < 15 Min, CSAT > 4,5",
        "kpi_label": "Tickets geschlossen",
    },
}

AGENTS_DE: dict[str, dict[str, Any]] = {
    "vera": {
        "role": "Verkaufsassistentin",
        "last_action": "Angebot für Bauer GmbH vorbereitet — wartet auf Freigabe",
        "last_run": "vor 2 Min.",
        "guardrails": [
            "Max. Angebotswert 10.000 €",
            "Keine Rabatte > 15 %",
            "Freigabe ab 5.000 €",
        ],
        "schedule": "Mo–Fr, 08:00–18:00",
        "mission": "Verkaufsassistentin für die Vertriebsabteilung.",
    },
    "data": {
        "role": "Datenanalyse",
        "last_action": "Wöchentlichen Umsatzreport erstellt und in #sales geteilt",
        "last_run": "vor 34 Sek.",
        "guardrails": [
            "Nur Lesezugriff auf die Datenbank",
            "Keine personenbezogenen Daten in Reports",
        ],
        "schedule": "Dauerhaft",
        "mission": "Datenanalyse für die Marketingabteilung.",
    },
    "doku": {
        "role": "Interne Dokumentation",
        "last_action": "Änderungen an der API-Dokumentation zusammengefasst",
        "last_run": "vor 7 Min.",
        "guardrails": ["Nur interne Wikis", "Keine Code-Änderungen"],
        "schedule": "Bei Bedarf",
        "mission": "Interne Dokumentation für die Entwicklungsabteilung.",
    },
    "hera": {
        "role": "HR-Assistentin",
        "last_action": "Pausiert durch Admin — DSGVO-Prüfung",
        "last_run": "vor 2 Std.",
        "guardrails": [
            "Nur lokales LLM",
            "Keine externen APIs",
            "Mitarbeiterdaten verschlüsselt",
        ],
        "schedule": "Mo–Fr, 09:00–17:00",
        "mission": "HR-Assistentin für die Personalabteilung.",
    },
    "fin": {
        "role": "Buchhaltung",
        "last_action": "12 Eingangsrechnungen kategorisiert und an die Buchhaltung übergeben",
        "last_run": "vor 12 Min.",
        "guardrails": ["Freigabe für Buchungen > 2.500 €", "Vier-Augen-Prinzip aktiv"],
        "schedule": "Werktags, 07:00–20:00",
        "mission": "Buchhaltung für die Finanzabteilung.",
    },
    "ops": {
        "role": "IT-Monitoring",
        "last_action": "Fehler: Verbindung zur Monitoring-API abgebrochen (401)",
        "last_run": "vor 4 Min.",
        "guardrails": ["Keine Produktions-Deployments", "Nur Alarme anlegen"],
        "schedule": "24/7",
        "mission": "IT-Monitoring für die Supportabteilung.",
    },
    "leo": {
        "role": "Outbound Sales",
        "last_action": "24 Kaltakquise-Mails an Segment ‚Manufacturing DACH‘ gesendet",
        "last_run": "vor 1 Min.",
        "guardrails": ["Max. 50 Mails/Tag", "Nur B2B-Adressen"],
        "schedule": "Mo–Fr, 08:00–17:00",
        "mission": "Outbound Sales für die Vertriebsabteilung.",
    },
    "nina": {
        "role": "Lead-Qualifizierung",
        "last_action": "9 neue Leads bewertet — 3 als ‚Hot‘ markiert",
        "last_run": "vor 3 Min.",
        "guardrails": ["Nur Scoring, kein Kundenkontakt"],
        "schedule": "Mo–Fr, 09:00–18:00",
        "mission": "Lead-Qualifizierung für die Vertriebsabteilung.",
    },
    "dex": {
        "role": "Engineering Lead",
        "last_action": "PR-Review für #482 abgeschlossen — 2 Kommentare",
        "last_run": "vor 40 Sek.",
        "guardrails": ["Kein Force-Push auf main", "Freigabe für Prod-Deployments"],
        "schedule": "Mo–Fr, 09:00–19:00",
        "mission": "Engineering Lead für die Entwicklungsabteilung.",
    },
    "ada": {
        "role": "Backend-Entwicklung",
        "last_action": "Feature ‚Batch Export‘ implementiert — Tests grün",
        "last_run": "vor 6 Min.",
        "guardrails": ["Nur Feature-Branches", "Coverage > 80 %"],
        "schedule": "Dauerhaft",
        "mission": "Backend-Entwicklung für die Entwicklungsabteilung.",
    },
    "kern": {
        "role": "QA & Tests",
        "last_action": "E2E-Lauf: 2 Regressionen gefunden — wartet auf Triage",
        "last_run": "vor 8 Min.",
        "guardrails": ["Kein Zugriff auf die Prod-Datenbank"],
        "schedule": "Dauerhaft",
        "mission": "QA & Tests für die Entwicklungsabteilung.",
    },
    "mara": {
        "role": "Marketing Lead",
        "last_action": "Kampagne ‚Q3 Launch‘ im sozialen Netzwerk ausgerollt",
        "last_run": "vor 5 Min.",
        "guardrails": ["Budget-Deckel 2.000 €/Woche", "Markenrichtlinien"],
        "schedule": "Mo–Fr, 08:00–18:00",
        "mission": "Marketing Lead für die Marketingabteilung.",
    },
    "tim": {
        "role": "Content-Redakteur",
        "last_action": "Blogpost ‚Agents in Practice‘ in Review",
        "last_run": "vor 11 Min.",
        "guardrails": ["Kein automatisches Veröffentlichen"],
        "schedule": "Mo–Fr, 09:00–17:00",
        "mission": "Content-Redakteur für die Marketingabteilung.",
    },
    "cent": {
        "role": "Reisekosten & Belege",
        "last_action": "18 Reisebelege per OCR erfasst und geprüft",
        "last_run": "vor 15 Min.",
        "guardrails": ["Max. Einzelbeleg 500 € ohne Freigabe"],
        "schedule": "Werktags, 08:00–17:00",
        "mission": "Reisekosten & Belege für die Finanzabteilung.",
    },
    "sam": {
        "role": "Support Lead",
        "last_action": "Ticket #4412 gelöst — SSO-Verbindungsproblem",
        "last_run": "vor 2 Min.",
        "guardrails": ["Keine Erstattungen ohne Freigabe", "PII-Filter"],
        "schedule": "Mo–Sa, 08:00–20:00",
        "mission": "Support Lead für die Supportabteilung.",
    },
    "echo": {
        "role": "First-Level-Support",
        "last_action": "14 Antworten aus der Knowledge Base erzeugt",
        "last_run": "vor 45 Sek.",
        "guardrails": ["Nur Standardantworten", "Ab Stufe 2 eskalieren"],
        "schedule": "24/7",
        "mission": "First-Level-Support für die Supportabteilung.",
    },
}

TASKS_DE: dict[str, dict[str, str]] = {
    "t-v1": {"title": "Angebot Bauer GmbH (7.400 €)", "meta": "Freigabe > 5.000 €"},
    "t-v2": {"title": "Follow-up Nordheim AG"},
    "t-v3": {"title": "50 Kaltakquise-Mails ‚Manufacturing‘"},
    "t-v4": {"title": "Lead-Scoring W28"},
    "t-v5": {"title": "Discovery-Call vorbereiten"},
    "t-v6": {"title": "Kontakte aus dem CRM importieren"},
    "t-v7": {"title": "9 Leads qualifiziert"},
    "t-v8": {"title": "Angebot an Meier AG gesendet"},
    "t-e1": {"title": "PR #482: Batch Export", "meta": "Review durch Dex"},
    "t-e2": {"title": "Regressionen triagieren", "meta": "2 Fehler"},
    "t-e3": {"title": "SDK v3 refaktorieren"},
    "t-e4": {"title": "API-v3-Doku aktualisieren"},
    "t-e5": {"title": "E2E-Suite erweitern"},
    "t-e6": {"title": "Bug #911: Session-Timeout"},
    "t-e7": {"title": "PR #479 gemerged"},
    "t-m1": {"title": "Blogpost ‚Agents in practice‘", "meta": "Publish-Freigabe"},
    "t-m2": {"title": "Social-Kampagne Q3"},
    "t-m3": {"title": "Anzeigen-Analyse"},
    "t-m4": {"title": "Newsletter W29 entwerfen"},
    "t-m5": {"title": "Kampagne ‚Spring‘ abgeschlossen"},
    "t-b1": {"title": "Lieferant Meier & Co. R-0331", "meta": "3.280 €"},
    "t-b2": {"title": "Eingangsrechnungen W28"},
    "t-b3": {"title": "Reisekosten Vertriebsteam"},
    "t-b4": {"title": "USt-Voranmeldung Juli"},
    "t-b5": {"title": "12 Belege gebucht"},
    "t-h1": {"title": "12 ‚Backend‘-CVs sichten"},
    "t-h2": {"title": "Warten auf DSGVO-Prüfung", "meta": "Admin-Pause"},
    "t-s1": {"title": "Ticket #4498 Erstattung", "meta": "Freigabe > 200 €"},
    "t-s2": {"title": "SSO-Ausfall untersuchen", "meta": "Incident PROD-4412"},
    "t-s3": {"title": "14 First-Level-Tickets"},
    "t-s4": {"title": "Knowledge Base aktualisieren"},
    "t-s5": {"title": "Ticket #4412 gelöst"},
}

ACTIVITY_DE: dict[str, dict[str, str]] = {
    "a1": {
        "message": "Umsatzreport W27 gesendet",
        "detail": "Empfänger: #sales, cfo@acme.io. Anhang: revenue-w27.pdf",
    },
    "a2": {"message": "Freigabe angefordert: Angebot Bauer GmbH (7.400 €)"},
    "a3": {
        "message": "12 Eingangsrechnungen an die Buchhaltung übergeben",
        "detail": "Batch-ID ACC-B-8391 · Buchungskonto 3400",
    },
    "a4": {"message": "Monitoring-API antwortet 401 — API-Key prüfen"},
    "a5": {"message": "Changelog aus 8 Pull Requests erzeugt"},
    "a6": {"message": "Neue Lead-Mail von info@holtmann.de gescannt"},
    "a7": {"message": "Dashboard ‚Pipeline Health‘ aktualisiert"},
    "a8": {"message": "Agent pausiert durch admin@oc8.io"},
    "a9": {"message": "Freigabe angefordert: R-2026-0331 (3.280 €)"},
    "a10": {"message": "Onboarding-Wiki für Product X zusammengestellt"},
}

ESCALATIONS_DE: dict[str, dict[str, str]] = {
    "e1": {
        "title": "Vera möchte ein Angebot senden",
        "detail": "Bauer GmbH — Wartungsvertrag 2026 inkl. Remote-Service-Zusatz.",
    },
    "e2": {
        "title": "Fin möchte eine Buchung freigeben",
        "detail": "Lieferant Meier & Co. — Rechnung R-2026-0331, ungewöhnlich hoher Betrag.",
    },
    "e3": {
        "title": "Vera möchte einen Rabatt gewähren",
        "detail": "Kunde Nordheim AG fordert 12 % Rabatt auf den Jahresvertrag.",
    },
}

SKILLS_DE: dict[str, dict[str, Any]] = {
    "sk-invoice-check": {
        "name": "Rechnungsprüfung & Buchung",
        "description": (
            "Prüft Eingangsrechnungen gegen Bestellungen, extrahiert Positionen "
            "und bucht sie auf das richtige Sachkonto."
        ),
        "guardrails": [
            "Beträge über 10.000 € brauchen menschliche Freigabe",
            "Rechnungen ohne gültige USt-IdNr. ablehnen",
        ],
        "instructions": (
            "Aus einer Rechnungs-PDF Kopf und Positionen extrahieren, gegen offene "
            "Bestellungen matchen, auf das richtige Sachkonto buchen und bei "
            "Schwellenwerten zur Freigabe weiterleiten."
        ),
    },
    "sk-lead-qualify": {
        "name": "Lead-Qualifizierung (BANT)",
        "description": (
            "Reichert eingehende Leads an und bewertet sie nach BANT, bevor sie an den AE gehen."
        ),
        "guardrails": ["Nie Leads auf der Do-not-call-Liste kontaktieren"],
        "instructions": (
            "Lead anreichern, Budget / Authority / Need / Timeline bewerten, "
            "Score 0–100 erzeugen und weiterleiten."
        ),
    },
    "sk-contract-summary": {
        "name": "Vertragszusammenfassung",
        "description": (
            "Fasst Verträge mit Pflichten, Laufzeiten, Verlängerungen und Risikohinweisen zusammen."
        ),
        "guardrails": ["Keine Rechtsberatung", "Rechtsordnungswechsel markieren"],
        "instructions": (
            "Vertrag parsen und strukturierte Zusammenfassung mit Parteien, "
            "Laufzeit, Verlängerung, Pflichten und Risiken erzeugen."
        ),
    },
    "sk-ticket-triage": {
        "name": "Support-Ticket-Triage",
        "description": (
            "Klassifiziert eingehende Tickets, setzt Priorität und entwirft eine Erstantwort."
        ),
        "guardrails": ["Rechtliche oder sicherheitsrelevante Themen sofort eskalieren"],
        "instructions": (
            "Nach Produktbereich klassifizieren, Priorität setzen, Antwortentwurf "
            "mit Verweis auf Help-Center-Artikel vorschlagen."
        ),
    },
    "sk-onboarding": {
        "name": "Onboarding neuer Mitarbeitender",
        "description": (
            "Koordiniert Equipment, Accounts und den Wochenplan für neue Mitarbeitende."
        ),
        "guardrails": ["Private Mitarbeiterdaten nie außerhalb des HR-Kanals teilen"],
        "instructions": (
            "Aus einem New-Hire-Datensatz Accounts provisionieren, Equipment "
            "bestellen, Intro-Termine planen."
        ),
    },
    "sk-inventory-recon": {
        "name": "Bestandsabgleich",
        "description": ("Gleicht physischen Bestand mit dem ERP ab und bucht Korrekturjournale."),
        "guardrails": ["Abweichungen über 2 % brauchen Freigabe"],
        "instructions": (
            "Inventurzähldatei mit ERP-Mengen vergleichen, Abweichungsreport "
            "erzeugen, freigegebene Korrekturen buchen."
        ),
    },
}

KBS_DE: dict[str, dict[str, str]] = {
    "kb-sales": {
        "name": "Vertriebs-KB",
        "description": "Preise, Playbooks, Einwandbehandlung, Wettbewerbsbriefings.",
    },
    "kb-legal": {
        "name": "Legal-KB",
        "description": "Unterzeichnete Verträge, NDAs, AVV-Vorlagen, Rechtsordnungshinweise.",
    },
    "kb-product": {
        "name": "Produktwissen",
        "description": "Feature-Specs, Roadmap, Release Notes, Produktanalytik.",
    },
    "kb-onboard": {
        "name": "Onboarding & Richtlinien",
        "description": "Handbook, DSGVO-Leitfaden, IT-Richtlinien, Spesenregeln.",
    },
}

INTEGRATIONS_DE: dict[str, dict[str, str]] = {
    "erp": {"name": "ERP", "desc": "Rechnungen, Aufträge, Buchhaltung."},
    "crm": {"name": "CRM", "desc": "Kontakte, Deals, Pipeline."},
    "office": {"name": "Office-Suite", "desc": "Mail, Kalender, Chat, Dokumentenspeicher."},
    "chat": {"name": "Chat", "desc": "Kanäle, Nachrichten, Alarme."},
    "code-host": {"name": "Code-Host", "desc": "Repos, Pull Requests, Issues."},
    "files": {"name": "Dateien", "desc": "Docs, Tabellen, Ordner."},
    "hr-system": {"name": "HR-System", "desc": "Mitarbeitende, Zeiterfassung, Abwesenheit."},
    "accounting": {"name": "Buchhaltung", "desc": "Belegimport, Kontenplan."},
    "wiki": {"name": "Wiki", "desc": "Wikis, Notizen, Datenbanken."},
}

SOURCES_DE: dict[str, dict[str, str]] = {
    "ds-web": {"name": "acme.io (öffentliche Docs)"},
    "ds-upload": {"name": "Firmenhandbuch (Upload)"},
}

MODELS_DE: dict[str, dict[str, str]] = {
    "Claude 3.5 Sonnet": {"note": "Bevorzugt für Text & Reasoning."},
    "GPT-4o": {"note": "Multimodal, Tool-Use."},
    "GPT-4o mini": {"note": "Höhere Latenz seit 09:00 gemeldet."},
    "Mistral Large": {"note": "EU-Region, günstig."},
    "Llama 3.1 8B (local)": {"note": "On-Premise, keine Daten verlassen die VPC."},
    "Claude 3.5 Haiku": {"note": "Schnelles, günstiges Claude."},
}


def de_block(mapping: dict[str, Any] | None) -> dict[str, Any]:
    """Wrap a locale map as `{de: …}` for storage under an `i18n` key."""
    if not mapping:
        return {}
    return {"de": mapping}
