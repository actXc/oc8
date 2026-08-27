# Capas

**Route:** `/capas`  
*(Old menus App Store / Integrations / Plugins all redirect here.)*

## What it is

**Installable capabilities**: tool packs, connectors, agent templates, skills,
runtimes, approval channels. Capas are how oc8 grows without changing core.

**MCP connections** (links to Odoo, M365, …) live here too — there is no separate
Connections menu.

## What it is for

- Add tools and templates your organisation needs
- Finish setup (OAuth, URLs, secrets) until a connection is live
- See what is installed vs available

## Where you are in the flow

```text
★ Capas install/enable/configure → Department allows tools → Agents use them
```

Capas are **platform setup**. Day-to-day decisions stay in My work / Handoffs.

## Tabs

| Tab | Purpose |
|-----|---------|
| **Installed** | What this tenant has; MCP connection status |
| **Available** | Discover and install (needs manage permission) |

## What you do here

1. Open **Available**; install a capa.
2. **Enable** it and accept permission consent.
3. Complete connection setup (credentials / OAuth).
4. Go to [Departments](departments.md) → Integrations & Permissions and allow the tools.
5. Optionally hire agents from templates on the same screen.

## Where work goes next

| After Capas | Next |
|-------------|------|
| Tools ready | Department frame → Agent access |
| Need secrets reusable elsewhere | [Credentials](credentials.md) |
| Integration how-to | [Integrations](../integrations/index.md) (capa overview + guides) |

## Related

- [Key concepts — Capa](../key-concepts.mdx#capa)
- [Developer documentation](../../developer/index.md) (building capas)
