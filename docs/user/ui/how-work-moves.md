# How work moves in oc8

This is the mental model for operators. If you only read one page about Flows
and Handoffs, read this.

## The big picture

```text
You describe an outcome
        ↓
An Agent (in a Department) works on a Task / Run
        ↓
Tool calls go through Capas → MCP gateway (with policy)
        ↓
If something is risky → My work (approval or clarification)
        ↓
If work must change team → Handoff (contract between departments)
        ↓
If several stages must run in order → Flow (pipeline of handoffs)
        ↓
Everything important is visible in Activity (+ Audit for compliance)
```

## Where each screen sits

| Step | Screen | Your job |
|------|--------|----------|
| 0 — First setup | [Welcome](welcome.md) / [Models](models.md) / [Capas](capas.md) | Make the platform able to run |
| 1 — Structure | [Departments](departments.md) | Who owns which work and which tools |
| 2 — Workers | [Agents](agents.md) + [Skills](skills.md) | Who does the work and how |
| 3 — Day-to-day | [Office](office.md) | See the floor; jump into a team or agent |
| 4 — Human decisions | [My work](my-work.md) | Approve, reject, answer questions |
| 5 — Cross-team | [Handoffs](handoffs.md) | Pass work between departments safely |
| 6 — Multi-stage | [Flows](flows.md) | Chain handoffs into a repeatable pipeline |
| 7 — Watch | [Activity](activity.md) / [Costs](costs.md) / [Audit](audit.md) | Understand what happened and what it cost |

## Flows vs Handoffs (plain language)

- A **Handoff** is one package of work moving from Department A to Department B —
  with rules (who may accept, what must be true, provenance).
- A **Flow** is a **recipe of several stages**. Each stage is usually a handoff
  (or agent work inside a department). Flows answer: “What is the whole process?”
  Handoffs answer: “Is this one transfer valid right now?”

You almost always **configure** Flows once, then **live decisions** show up in
**My work** and **Handoffs**.

## Typical handoff paths

```text
Agent needs a human
  → My work (approval / clarification)
  → Agent continues or stops

Agent finishes stage for another team
  → Handoff appears (incoming for the other department)
  → Someone accepts (or a gate sends it to My work)
  → Next department's agents pick it up

Operator starts a Flow
  → Stages run in order
  → Each gated step can pause in Handoffs / My work
  → Flow run completes or stops for human input
```

## Common confusion

| You might think… | Actually… |
|------------------|-----------|
| “Tasks” is a menu | Tasks live on the **Department board** and on the **Agent** |
| “Approvals” is a menu | Approvals live under **My work** (header badge too) |
| “Connections” is a menu | MCP connections live under **Capas** |
| Flows replace agents | Flows **orchestrate** departments/agents; agents still do the work |

## Next

- First day: [Quickstart](../quickstart.md) → [Office](office.md) → [My work](my-work.md)
- Deep dive: [Handoffs](handoffs.md) · [Flows](flows.md) · [Departments](departments.md)
