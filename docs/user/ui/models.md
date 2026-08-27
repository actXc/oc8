# Models

**Route:** `/models` · **Settings** section

## What it is

Where you register **LLM providers** and model configs agents can use
(Anthropic, OpenAI, Ollama, …), including pricing metadata for [Costs](costs.md).

## What it is for

- Register LLM providers and model configs agents can use
- Usually done **once** — often already handled in the [Welcome wizard](welcome.md)
- Adjust later for cost ([Costs](costs.md)) or different agents

You only need this screen before an agent **runs** — not to install oc8 itself.

## Where you are in the flow

```text
★ Models configured → assign on Agents → runs consume tokens → Costs
```

Without a model, agents cannot usefully run. This is early setup (also linked
from the welcome / pilot checklist).

## What you do here

1. Add a provider (API key via env or UI, depending on deployment).
2. Declare / select models.
3. On each [Agent](agents.md), pick the model config.
4. Prefer cheaper models for pilots; tighten later.

## Where work goes next

| Goal | Next |
|------|------|
| Run an agent | [Agents](agents.md) |
| Local model without cloud keys | Ollama profile — [Quickstart](../quickstart.md) |
| Watch spend | [Costs](costs.md) |

## Related

- [Credentials](credentials.md) (secrets for tools — different from model keys)
- [Quickstart](../quickstart.md)
