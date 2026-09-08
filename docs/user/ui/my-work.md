# My work

**Route:** `/workspace` · **Sidebar / header:** My work

## What it is

Your **personal, rearrangeable dashboard**: a grid of tiles you pick and
arrange yourself, covering the things a person on your team checks day to
day — pending approvals and clarifications, chat with the assistant,
budget status, reports, and recent activity.

On first visit you choose a starting layout (a template); after that you
can add, remove, resize, and drag tiles freely, and your arrangement is
saved automatically.

The **Approvals** tile is the screen people mean when they say
“approvals”, even though there is no separate Approvals menu.

## What it is for

- Unblock an agent that paused on a risky tool call
- Answer a clarification so the agent can continue
- Chat with the assistant without leaving the page
- Keep an eye on budget, reports, or recent activity alongside your queue

## Where you are in the flow

```text
Agent runs → hits gate / question → ★ My work (Approvals tile) → you decide → agent continues or stops
```

Also reachable from the header badge and notification bell.

## What you do here

1. Open **My work** (sidebar or header).
2. First visit only: pick a starting layout, or start from an empty grid.
3. In the **Approvals** tile, filter by kind (approval / question) or
   department if your queue spans more than one.
4. Click a row to open its detail and read the proposal: tool, summary,
   value, related agent.
5. **Approve**, **Reject**, or **Answer**.
6. Add, remove, resize, or drag other tiles (Chat, Budget, Reports,
   Activity) to shape the dashboard around what you check most.

## Where work goes next

| Your action | Next |
|-------------|------|
| Approve | Run resumes; tool executes; see [Activity](activity.md) |
| Reject | Agent gets an error and may replan or stop |
| Answer clarification | Agent continues with your answer |
| Cross-team package | May also appear under [Handoffs](handoffs.md) |

## Related

- [Governance and approvals](../governance-and-approvals.md)
- [How work moves](how-work-moves.md)
- [Agents](agents.md)
