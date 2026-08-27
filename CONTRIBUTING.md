# Contributing to oc8

Thanks for considering a contribution. This document covers the legal and
process basics; for the actual "how do I build and test this" guide, start
at [docs/contributing/index.md](docs/contributing/index.md).

## Code of Conduct

All project participants are expected to follow
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Developer Certificate of Origin

oc8 uses the [Developer Certificate of Origin](https://developercertificate.org/)
(DCO) instead of a Contributor License Agreement. It's the same lightweight
mechanism used by the Linux kernel, Docker, and many other open-source
projects: you certify that you wrote the contribution (or otherwise have
the right to submit it) by adding a `Signed-off-by` line to every commit,
using your real name:

```
Signed-off-by: Jane Doe <jane@example.com>
```

`git commit -s` adds this automatically. A pull request whose commits lack
a sign-off will be asked to add one (`git commit --amend -s` for the last
commit, or `git rebase --signoff <base>` for a whole branch) before it can
be merged.

No copyright is assigned to the project — you keep it. By submitting a
contribution you license it under the same terms as the code it changes:
**LGPL-3.0-or-later** for `backend/` and `frontend/` (see
[LICENSE](LICENSE)), or the license already declared
in a capa's own manifest for a change under `capas/`.

## Opening a pull request

1. Fork or branch, make your change, and follow
   [Coding Guidelines](docs/contributing/coding-guidelines.md) and
   [Git Guidelines](docs/contributing/git-guidelines.md).
2. Sign off every commit (see above).
3. Run the full check locally before opening the PR — lint, type-check,
   and the full test suite (`backend/`: `python -m pytest && python -m
   ruff check src tests`; `frontend/`: `npm run lint && npm run test:unit
   && npm run build`). A PR that doesn't pass locally won't pass CI either.
4. Describe **why**, not just what, in the PR description. If the change
   touches core (`backend/src/oc8/`, `frontend/src/`) rather than a capa,
   say which mechanism it adds or fixes and why it couldn't be a capa
   instead — see the
   [microkernel principle](docs/contributing/architecture.md#the-microkernel-principle).

## Reporting a bug

Open an issue with: what you expected, what happened instead, and the
smallest reproduction you can manage. If the bug is in a specific capa
rather than core, say so — it changes who's likely to pick it up.

## Reporting a security vulnerability

Do not open a public issue. Follow [SECURITY.md](SECURITY.md) instead.

## Building a capa instead of changing core

If you're building a connector, an MCP tool bridge, a runtime adapter, a
model provider, an approval channel, or a data-only bundle, you generally
don't need to touch core at all — see
[Developer documentation](docs/developer/index.md) and
[capas/README.md](capas/README.md).
