# Agents

**Route:** `/agents` · Detail: `/agents/$id`

## What it is

An **AI worker** with a mission, model, skills, and permissions. Agents do the
actual work — drafts, updates, tool calls — under the rules of their department.

## What it is for

- Hire or create workers for recurring outcomes
- Start / pause / stop lifecycle
- Watch a live run, chat, configure tools and skills
- Narrow permissions below the department frame

## Where you are in the flow

```text
Department exists + model + capas → ★ Agent configured → Run / schedule / chat
  → maybe My work → done (Activity)
```

## Tabs on the detail page

| Tab | Purpose |
|-----|---------|
| **Overview** | Status, mission, current task |
| **Live Log** | Streaming transcript of the active run |
| **Chat** | Talk to the agent / send a task |
| **Files** | Workspace files for this agent |
| **Configuration** | Mission, model, triggers, autonomy |
| **Access & Permissions** | Tool narrowing vs department defaults |
| **Skills** | Which procedures this agent may load |
| **Memory** | What it is allowed to remember |
| **History** | Past runs |

## What you do here

1. Create an agent or hire from a capa template ([Capas](capas.md)).
2. Assign a [model](models.md), mission, and skills.
3. Start the agent; send a low-risk task.
4. If it waits — open [My work](my-work.md).
5. Review [Activity](activity.md) / History after.

## Where work goes next

| From agent | Next |
|------------|------|
| Needs approval | [My work](my-work.md) |
| Hands to another team | [Handoffs](handoffs.md) |
| Part of a pipeline | [Flows](flows.md) |
| Missing tools | [Capas](capas.md) → department permissions |

## Isolation note

When isolation is on, each run can execute in a **sandbox container**. Details:
[Key concepts — Agent architecture](../key-concepts.mdx#agent-architecture).

## Related

- [Skills](skills.md)
- [Departments](departments.md)
- [How work moves](how-work-moves.md)
