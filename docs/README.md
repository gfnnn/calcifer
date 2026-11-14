# Documentation index

Technical references for building, testing and running Calcifer. For the
feature and command list see [`../README.md`](../README.md); for the codebase
architecture and conventions see [`../CLAUDE.md`](../CLAUDE.md); for the
contribution workflow see [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

| Doc | What it's for |
| --- | --- |
| [`boards-contract.md`](boards-contract.md) | The sticky-post contract: the shared board grammar, each shipped board's and page's markup, colours, limits and update triggers, the `brand.py` voice hooks, the canonical channel layout, the feed routing map and the ping policy. Build boards from this. |
| [`communication.md`](communication.md) | Calcifer's Discord communication palette: markdown, dynamic `<t:…>` timestamps, mentions and `allowed_mentions`, emoji, embeds, Components V2 and message limits. The detail behind `ftabot/brand.py` + `ftabot/publisher.py`. |
| [`voice-usage-examples.md`](voice-usage-examples.md) | Code patterns for using `brand.Voice` in boards, commands, feeds and errors. Pairs with [`../MESSAGING.md`](../MESSAGING.md). |
| [`data-sources.md`](data-sources.md) | What each external API (WoW Audit, Blizzard, Raider.IO, Warcraft Logs) exposes, how auth works, and the local-addon data spine: the detail behind `ftabot/services/`. |
| [`data-spine-validation.md`](data-spine-validation.md) | What the SavedVariables ingestion has and hasn't been validated against, and how to check it on a real dump. |
| [`deploy.md`](deploy.md) | Hosting options, step-by-step setup guides (Oracle Cloud free VM, Fly.io, your own machine), the first-run config checklist, backups and the posting-guardrail workflow. |
| [`verification-runbook.md`](verification-runbook.md) | The credential-gated live checks to run from a fresh session. |
| [`testing-status.md`](testing-status.md) | Which integrations have been verified live versus offline only. |
| [`branching.md`](branching.md) | The GitHub Flow branching and release model. |
| [`maintainer-setup.md`](maintainer-setup.md) | One-time GitHub repository settings (branch protection and friends). |
