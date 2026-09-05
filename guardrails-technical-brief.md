# Guardrails & Tool-Rechte in oc8 — Technischer Aufbau

Referenz für ein neues, intuitiveres UI-Konzept (z. B. mit Lovable). Beschreibt, was das System heute technisch leistet und abdecken **muss** — nicht, wie es dargestellt werden soll. Feldnamen sind bewusst im Original (Englisch) belassen, da sie 1:1 der API entsprechen.

---

## 1. Grundkonzept: eine Drei-Ebenen-Schnittmenge

Was ein Agent mit einem Tool (z. B. "odoo") tatsächlich darf, ist **niemals eine einzelne Einstellung**, sondern das Ergebnis einer Schnittmenge aus drei unabhängigen Quellen:

```
effective = role_rights  ∩  department_frame  ∩  agent_narrowing
```

- **`role_rights`** — was die *Rolle* des Agenten grundsätzlich erlaubt (z. B. "First Level IT Support"). In der Praxis fast immer "alles" (read+write+send), außer die Rolle ist kaputt/fehlt — dann liefert dieser Term **nichts**, nicht "alles". Das ist eine bewusste Fail-safe-Entscheidung im Backend.
- **`department_frame`** — die Obergrenze, die die Abteilung für dieses Tool erlaubt. Gilt für alle Agenten der Abteilung als Default.
- **`agent_narrowing`** — die individuelle Einschränkung eines einzelnen Agenten. Kann den Frame nur **enger** machen, nie weiter (mit einer Ausnahme, siehe Abschnitt 4).

Jede Ebene kann nur *einschränken*, nie *erweitern* — mit Ausnahme der agent-exklusiven Grants unten. Das Ergebnis (`effective`) ist das, was der Agent zur Laufzeit tatsächlich nutzen kann.

**Wichtig für ein neues UI-Konzept:** `effective` ist ein *berechnetes* Ergebnis, kein gespeicherter Zustand. Es darf in der UI nie als "der Wert, den ich bearbeite" behandelt werden — nur `department_frame` und `agent_narrowing` sind editierbar und gespeichert. (Genau das war die Ursache eines gerade behobenen Bugs: eine UI-Komponente hat versehentlich `effective` als Bearbeitungsgrundlage genommen und dadurch einen temporären Rollenfehler dauerhaft in die gespeicherte Konfiguration übernommen.)

---

## 2. Die beteiligten Entitäten

| Entität | Bedeutung |
|---|---|
| **Department** | Organisatorische Einheit, besitzt einen `frame` (JSON: pro Tool-Name eine Policy, siehe Abschnitt 3) |
| **Agent** | Gehört zu einem Department, hat optional eine `role`, besitzt eine eigene `narrowing`-Policy (gleiche Struktur wie ein Frame) |
| **MCP Connection** | Eine tenant-weite technische Integration (z. B. "odoo", "Salesforce") — Transport, welche Tools sie anbietet (`health.tools`), ob sie gerade erreichbar ist |
| **MCP Login** | Eine konkrete, Credential-gebundene Anmeldung an einer Connection (z. B. "dieser odoo-Zugang mit diesem Passwort"). Ein Agent pinnt sich einen Login über `connection_id` |
| **Credential** | Die eigentlichen Zugangsdaten (verschlüsselt gespeichert), von einem Login referenziert |
| **Role** | Rollen-Definition, die `role_rights` bestimmt. Es gibt Menschen-Rollen und Agent-Rollen (`role.kind`) — nur Agent-Rollen dürfen einem Agenten zugewiesen werden |

---

## 3. Die Policy-Felder (pro Tool, z. B. "odoo")

Jede der drei Ebenen (Frame, Narrowing, effektives Ergebnis) hat pro Tool-Namen dieselbe Struktur:

| Feld | Typ | Bedeutung für den Nutzer |
|---|---|---|
| `enabled` | bool | Ist das Tool für diesen Agenten überhaupt aktiv? |
| `read` | bool | Darf lesen/suchen |
| `write` | bool | Darf anlegen/ändern (create/update) |
| `send` | bool | Darf nach außen wirken (z. B. Nachricht an eine Person, E-Mail) |
| `approval_eur` | number \| null | Ab diesem Euro-Betrag braucht eine Aktion menschliche Freigabe |
| `approval_actions` | string[] | Einzelne Aktionen/Tools, die **immer** Freigabe brauchen, unabhängig vom Betrag (z. B. `delete_record`, `post_message`) |
| `only` | string[] \| null | Harte Einschränkung der sichtbaren Tool-Oberfläche. `null`/leer = alle Tools der Connection; sonst nur die genannten. Wichtig bei Integrationen mit vielen Tools (z. B. 30+) — zu viele Tools verwirren das Modell messbar |
| `connection_id` | string \| null | Welcher konkrete Login (Zugangsdaten) für dieses Tool verwendet wird. Nur auf Agent-Ebene sinnvoll, ein Department-Default hat keinen eigenen Login |

**Read/Write/Send sind KEINE Hierarchie** — ein Agent kann z. B. read+send haben ohne write (Kundenanfragen beantworten, aber nichts in Odoo ändern).

---

## 4. Woher eine Agent-Policy stammt — drei Fälle

1. **Frame-geerbt, unverändert**: Tool ist im Department-Frame, Agent hat keine eigene Einschränkung → `effective = frame`.
2. **Frame-geerbt, eingeschränkt**: Tool ist im Frame, Agent-Narrowing engt es weiter ein (z. B. `write: false`, obwohl Frame `write: true` erlaubt).
3. **Agent-exklusiv**: Tool ist **nicht** im Department-Frame, aber der Agent hat es trotzdem direkt zugewiesen bekommen (z. B. ein Spezial-Tool nur für diesen einen Agenten). Das ist die einzige Stelle, an der ein Agent "mehr" haben kann als der Frame nennt — weil der Frame das Tool schlicht nicht kennt, nicht weil eine Grenze überschritten wird.

Ein UI-Konzept muss diese drei Fälle klar unterscheidbar machen — aktuell sind sie über zwei getrennte Tabs und eine Badge ("Agent only") verteilt.

---

## 5. Guardrail Presets & Library

Statt jedes Feld einzeln einzustellen, gibt es vorgefertigte Kombinationen:

- **Presets** (pro Connection, z. B. 5 Stück für odoo): kuratierte Alltags-Konfigurationen wie "Unterstützen mit Freigabe", "Selbstständig mit Limit", "Nur intern", "Nur lesen". Jedes Preset setzt alle Policy-Felder auf einmal.
- **Guardrail Library** (pro Use-Case, z. B. "finance", "sales", "purchasing", "helpdesk", "inventory"): granularere, fachlich benannte Bausteine (z. B. "Rechnungsentwürfe bis zu einem Betrag automatisch, kein Löschen") — jeweils mit einer **explizit formulierten "deckt NICHT ab"-Erklärung**, damit klar ist, wo die Grenze eines Guardrails liegt.
- Presets/Library-Einträge sind **Startpunkte**, keine Käfige — nach Auswahl bleibt jedes Feld einzeln nachjustierbar.

---

## 6. Aktuelle UI-Oberfläche (Ist-Zustand)

Heute über **zwei getrennte Tabs** am Agenten verteilt:

- **Configuration-Tab**: pro Tool ein/aus schalten, Tool hinzufügen/entfernen, Login/Credential pinnen. Zeigt read/write/send **nicht an**.
- **Guardrails-Tab**: read/write/send, `approval_eur`, `approval_actions`, `only` — über Presets/Library oder manuell. Zeigt enable/connection **nicht an** (nur lesend).

Das ist der Kern des Nutzerproblems: um den vollständigen Zustand eines einzigen Tools für einen Agenten zu verstehen, muss man zwei Menüs gleichzeitig im Kopf haben.

---

## 7. Was ein neues UI-Konzept technisch abdecken MUSS

1. **Eine Ansicht pro Tool pro Agent**, die alle Felder aus Abschnitt 3 zusammen zeigt — nicht auf zwei Tabs verteilt.
2. **Sichtbare Herkunft**: für jedes Feld erkennbar, ob es (a) der Department-Standard ist, (b) vom Agenten bewusst enger gestellt wurde, oder (c) ein agent-exklusives Tool ohne Department-Bezug ist.
3. **Presets als Schnellstart**, mit Möglichkeit zur granularen Nachjustierung jedes einzelnen Feldes danach — beides muss auf demselben Bildschirm möglich sein, nicht in getrennten Flows.
4. **Editierbar sind ausschließlich Frame (auf Department-Ebene) und Narrowing (auf Agent-Ebene)** — niemals `effective` direkt. Jede Speicherung muss aus dem jeweils *eigenen, zuletzt gespeicherten* Wert heraus erfolgen, nicht aus dem berechneten Ergebnis.
5. **Validierung**: Agent-Narrowing darf ein Frame-Tool niemals über den Frame hinaus erweitern (außer es ist ein agent-exklusives Tool, das der Frame gar nicht kennt) — Verstoß muss vor dem Speichern sichtbar sein, nicht erst als Server-Fehler.
6. **`only`-Verwaltung**: eine verständliche Möglichkeit, aus der vollen Tool-Liste einer Connection (kann 10–35+ Einzel-Tools sein) eine sinnvolle Teilmenge auszuwählen, statt einer rohen String-Liste.
7. **`approval_actions` als kontrollierte Auswahl**, nicht als Freitextfeld — aktuell ein bekannter, noch offener kleinerer Bug (Freitext landet unvalidiert in dieser Liste).
8. **Verbindung/Login-Auswahl** (welche Zugangsdaten ein Tool nutzt) muss im selben Kontext sichtbar sein wie die Rechte selbst — heute getrennt.
9. **Erkennbarkeit eines "kaputten" Zustands**: wenn `effective` von `narrowing`/`frame` abweicht (z. B. durch ein Rollenproblem), sollte das im Prinzip gar nicht mehr passieren können (serverseitig jetzt entkoppelt) — ein neues UI muss aber so gebaut sein, dass es strukturell verhindert, jemals wieder aus dem falschen Feld zu speichern.

## 8. Bewusst NICHT Teil dieses Themas

- Anlegen/Verwalten von Rollen selbst (separates Rollen-Management).
- Anlegen einer neuen MCP Connection/Credential (separater Onboarding-Flow, bleibt unverändert).
- Freigabe-Workflow selbst (was passiert, wenn eine Freigabe angefragt wird) — nur *ob* eine Aktion eine Freigabe auslöst, ist hier relevant.
