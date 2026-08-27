# Activity

**Route:** `/activity`

## What it is

The **operational feed**: recent actions across agents and runs — success,
warning, error, info — with timestamps.

This is the day-to-day “what just happened?” view. For compliance / tamper
evidence, use [Audit](audit.md).

## What it is for

- Debug a failed or weird run quickly
- Filter by agent or status
- Spot patterns (many errors after a capa change)

## Where you are in the flow

```text
Work happens (agents / handoffs) → ★ Activity (observe) → drill into Agent / My work
```

You are **watching**, not deciding. Decisions: [My work](my-work.md).

## What you do here

1. Open **Activity**.
2. Filter agent / status.
3. Open related agent or run when something looks wrong.
4. If a run is stuck on approval, switch to My work.

## Where work goes next

| Finding | Next |
|---------|------|
| Agent error | [Agents](agents.md) Live Log / Configuration |
| Waiting on human | [My work](my-work.md) |
| Need compliance trail | [Audit](audit.md) |
| Spend spike | [Costs](costs.md) |

## Related

- [Audit](audit.md)
- [How work moves](how-work-moves.md)
