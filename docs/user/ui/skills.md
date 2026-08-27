# Skills

**Route:** `/skills`

## What it is

Reusable **procedures** — playbooks an agent can load during a run (instructions,
required tools, guardrails). Skills are how you encode “how we do X here”
without rewriting every agent.

## What it is for

- Standardise steps (triage, draft reply, escalate billing, …)
- Share the same procedure across many agents
- Import skills from capas or create your own

## Where you are in the flow

```text
Capa / author creates skill → ★ Skills catalog → assign to Agents → used at runtime
```

Skills are configured **before** or **between** runs — not the live inbox.

## What you do here

1. Browse existing skills.
2. Create or import a skill.
3. Assign it to one or more [Agents](agents.md) (also possible from the agent
   Skills tab).
4. Run a task that should invoke the skill; verify in Live Log / Activity.

## Where work goes next

| Situation | Next |
|-----------|------|
| Skill needs tools | Ensure [Capas](capas.md) + department permissions allow them |
| Skill assigned | Open [Agents](agents.md) and test a run |
| Skill came from a pack | Manage the pack under Capas |

## Related

- [Key concepts — Skill](../key-concepts.mdx#skill)
- [Developer: capa types](../../developer/plugin-types.md) (authors)
