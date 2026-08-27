# Security policy

## Supported versions

oc8 is currently in active pre-release development. There is no published
supported-version or security-maintenance window yet.

## Reporting a vulnerability

Please do **not** report suspected vulnerabilities in a public GitHub issue,
discussion, pull request or chat channel.

For the future public repository, use GitHub's **private vulnerability
reporting** feature from the repository's *Security* tab when it is enabled.
Maintainers should enable that feature before the first public release and use
it as the canonical private reporting channel.

> **Reporting-channel placeholder:** no public security email address or
> escalation contact has been approved for this pre-release repository. Project
> maintainers must publish a monitored private reporting path before inviting
> external users or accepting public contributions. Do not infer an address from
> package metadata or commit history.

If you are working with the project team under an existing private agreement,
use the private channel specified in that agreement and include enough detail to
reproduce the issue safely.

## What to include

Provide, where possible:

- a concise description of the impact and affected component or version;
- reproducible steps or a minimal proof of concept;
- relevant configuration, deployment topology and logs with secrets removed;
- whether the issue affects core oc8, a plugin or deployment infrastructure;
  and
- suggested mitigations, if known.

Do not include credentials, customer data, private keys, access tokens or
unredacted database backups.

## Disclosure and remediation

The project will acknowledge a report through the private channel once one is
available, investigate it, develop a fix and coordinate disclosure with the
reporter where practical. No response-time or bounty commitment is made by this
pre-release policy. Please allow maintainers time to validate and remediate an
issue before public disclosure.

## Security boundaries

Self-hosters remain responsible for their deployment environment, credentials,
network exposure, backups and plugin trust decisions. In particular, review the
warnings in [docs/DEPLOY.md](docs/DEPLOY.md) before running the reference
Compose stack: its development defaults and Docker-socket mount are not safe for
unreviewed public exposure.
