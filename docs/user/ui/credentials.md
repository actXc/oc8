# Credentials

**Route:** `/credentials` · **Settings** section

## What it is

Reusable entries in the **encrypted secret store** — connection metadata and
secrets that capas and tools can reference without pasting keys into chat.

## What it is for

- Store API tokens / client secrets once
- Test credentials
- Keep agents and Copilot from seeing raw secret values

## Where you are in the flow

```text
★ Credentials saved → Capas / connections reference them → agents call tools
```

Different from [Models](models.md) (LLM providers) and from live
[Capas](capas.md) connection wizards (which may create or bind credentials).

## What you do here

1. Create a credential of the right type.
2. Test if the UI offers a test action.
3. Use it from a capa setup form or connection.
4. Rotate / delete when someone leaves or a key leaks.

## Where work goes next

| Goal | Next |
|------|------|
| Wire a tool | [Capas](capas.md) |
| Integration guide | [Integrations](../integrations/index.md) |
| Org-wide secrets panel | Also under [Settings → General](settings-general.md) |

## Related

- [Key concepts — Secret store](../key-concepts.mdx) (platform architecture)
- [Governance](../governance-and-approvals.md)
