# The helpdesk agent (second use case)

An autonomous first-line support agent that works Odoo Helpdesk tickets: it
reads the oldest ticket in the first stage, answers the customer in the chatter,
moves the ticket on, and leaves an internal note when a human has to decide
something.

It exists to be a *different shape* from the sales agent, not a second copy of
it. Nora is started by a human and produces a value-gated write; Sina is started
by a schedule and produces customer-facing text with no monetary value at all.
That difference exercises the parts of oc8 the sales case never touches — trigger
→ run, an outward action with no €-value, and a skill loaded at runtime.

## What it is made of

| Piece | Type | What it carries |
|---|---|---|
| `capas/helpdesk_first_response_skill` | `skill` | CRAFT: how to write to a customer, plus guardrails (never promise a refund, never invent facts) |
| `capas/helpdesk_support_agent` | `department_template` | PROCESS: which ticket, which stage, when to escalate — and the department frame |
| `scripts/seed_helpdesk_tickets.py` | script | Fresh German customer tickets in the test Odoo |
| a `cron` trigger | data | Wakes the agent on a schedule |

Neither capa contains Odoo interface logic. They name the connection **key**
`odoo`; the `odoo_mcp` tool pack owns everything vendor-specific, and the core
stays software-neutral.

## Bring it up

Odoo side, once: the Helpdesk models must be exposed through the Odoo "MCP
Server" add-on — `helpdesk.ticket`, `helpdesk.stage`, `helpdesk.team` with read,
create and write. Without this the agent sees no tools for them and cannot work.

oc8 side:

1. **Install and enable** both capas (Capas screen, or
   `POST /api/v1/capas/install-from-disk` with the capa id, then
   `POST /api/v1/capas/{id}/enable`).
2. **Instantiate the department** from `helpdesk_support_agent`
   (`POST /api/v1/capas/{id}/instantiate-department`, e.g. name
   "Kundenservice (Odoo)"). This creates the department, its frame, and the
   agent Sina in state `stopped`.
3. **Set Odoo up for that department** — the `odoo_mcp` setup form, choosing the
   new department. Each department gets its own connection, so this does not
   disturb an existing one. Then run the connection test; the agent needs
   `connected = true`.
4. **Configure the agent**: model config (`PATCH /agents/{id}/model-config`),
   runtime (`PUT /agents/{id}/runtime`, field `runtimePluginId`), the skill
   (`POST /agents/{id}/skills`, field `skillVersionId`), then `start` it.
5. **Create the trigger**: `POST /agents/{id}/triggers` with
   `{"kind": "cron", "cronExpression": "*/3 * * * *", "taskText": "…"}`.
   The `scheduler` service must be running for cron triggers to fire.

## Feeding it work

```
docker compose exec -T backend python - 3 < scripts/seed_helpdesk_tickets.py
```

The argument is how many tickets to create. It runs inside the backend container
so the Odoo password comes from the encrypted secret store rather than a shell
history.

Two further modes, because a first reply is only half of support work:

```
docker compose exec -T backend python - reply 2   < scripts/seed_helpdesk_tickets.py
docker compose exec -T backend python - cleanup   < scripts/seed_helpdesk_tickets.py
```

`reply` makes a customer write back on a ticket the agent has already answered —
the only way to exercise the follow-up path below. `cleanup` parks leftover test
tickets in the cancelled stage so counts mean something again; add `all` to park
conversations still waiting on an answer as well.

## How "handled" is tracked

By the stage, not by a marker of our own: the agent only looks at the first
stage, and moving a ticket out of it is what takes it off the list. That is also
what a human colleague would do, so a ticket a person picks up disappears from
the agent's view for free.

One ticket per run, oldest first. A backlog is worked through at the trigger's
cadence rather than in one long run — a run that touches twenty tickets is one
run that can fail twenty tickets.

## Coming back to a conversation

An answer that ends in a question used to be a dead end: the agent only ever
looked at the inbox, so once it moved a ticket to *In Bearbeitung* it never saw
that ticket again — the customer could reply and nobody would read it.

So a run has two kinds of work, and the inbox comes first:

* **A — a new ticket** in the first stage. First reply, claim it before writing.
* **B — a waiting conversation**, only when the inbox is empty: the ticket whose
  customer has been waiting longest for a follow-up.

Case B is found through Odoo's own stored field
`oldest_unanswered_customer_message_date`: the ticket system sets it when a
customer writes and clears it when the team answers. That matters more than it
looks — it means "who wrote last" never has to be inferred from message authors,
the agent needs one search rather than one per ticket, and nothing has to be
reset afterwards. The last stage (cancelled) is excluded, or an abandoned ticket
with an open question would come back on every single run.

There is no claim step in case B. The ticket is already out of the inbox, and
the answer itself clears the field, so the window in which a second run could
pick up the same conversation is one run long.

## Autonomy, and how to take it away

The department frame grants the `odoo` connection with **no approval
threshold**: Sina answers customers and moves tickets without asking. To require
a human for every outward action instead, set in the frame:

```toml
[plugin.department_template.frame.tools.odoo]
approval_eur = 0
```

€0 means "every send needs approval". A *positive* threshold would never fire
here, because a ticket reply carries no monetary value at all — which is why the
policy engine treats an unreadable value as zero rather than as exempt. That one
line is the whole difference between a pilot that drafts and a pilot that sends.

## Where the rules live, and why

The hard prohibitions — never promise a refund, never claim something is fixed,
never invent facts — sit in the agent's **mission**, not only in its skill.

That is deliberate and was learned the hard way. Skills activate on demand: the
agent gets a catalogue and is told to call the named tool to load the procedure.
So a rule that lives only in a skill applies only if the model chooses to load
it — which makes the model's judgement the gate on the rules meant to constrain
its judgement. Observed live: the agent promised a customer a refund its own
skill forbids, with `active_skill_ids` empty on the run.

The skill keeps the *craft* (how to write a good first reply). Anything that
must never happen belongs in the mission, where it is always in the prompt.

## When a human has to decide

The agent does not leave a note in Odoo hoping somebody finds it. It calls
`request_decision`, and the decision lands in the approvals inbox carrying
everything needed to answer it — the question, the ticket and customer, what the
agent checked, what is at stake, and the alternatives it proposes with the one it
would pick. The point is that nobody has to open Odoo to decide.

**It does not wait.** A queue behind the agent must not stall on a lunch break,
so the tool records and returns, the agent finishes the ticket by telling the
customer a colleague is reviewing it, and moves on.

**The decision becomes the work.** Deciding in the inbox enqueues a NEW run for
the same agent, carrying an instruction that stands on its own — that run starts
a fresh conversation and remembers nothing of having asked. A rejection travels
back too: somebody still has to tell the customer no.

In the inbox an operator can pick one of the agent's options, write a free-text
instruction, or both. The free text wins: a human who writes an instruction means
it more than a button they also pressed.

Two properties worth knowing:

- One decision produces one run, forever (idempotency key), so a double-click
  cannot make the agent act twice on the outside world.
- An option that is not one the agent offered is refused before anything is
  written, so a typo cannot leave a decided approval whose instruction nobody can
  act on.

`request_decision` is the one core tool the tool gateway offers a container-run
agent. The others change the run's lifecycle, which MCP has no vocabulary for;
this one only writes a request inside oc8. It is not frame-filtered either — a
read-only agent must be able to escalate rather than go quiet about something a
person needs to decide.

## Known limits

- **Instruction-following is the weak link, not the plumbing.** With
  `opaas_ai:odoo-gpt` the agent still occasionally states something it cannot
  know ("that can only be changed by phone"). Everything the platform can do is
  done — the rules are always in the prompt and the skill is reachable — so the
  remaining levers are a stronger model or the €0 approval gate above, which puts
  a human in front of every outward message.
- The agent judges for itself whether an answer was complete enough to close the
  ticket. In practice it is conservative and usually moves to "In Bearbeitung";
  that is the mission working as intended, not a defect. It sometimes reaches for
  a stage the mission never mentions ("On Hold").
- Assigning a newer version of a skill does not retire the older assignment, so
  the agent is offered the same skill twice (`skill_x` and `skill_x_2`). Disable
  the old assignment when publishing a new version.
- Attachments and images on a ticket are not read.

## Watching it work

```
./scripts/sina-demo.sh        # 3 random new tickets, then one run, then the proof
./scripts/sina-demo.sh 5      # 5 tickets
./scripts/sina-demo.sh 0      # no new tickets, just work the backlog
```

It seeds tickets, starts a run immediately (rather than waiting for the cron
trigger), polls until it finishes, and then shows what changed in Odoo: which
ticket moved, to which stage, and what the customer was actually told. The Odoo
side is read directly, so the report is not the agent's own account of itself.
