# Documentation index

> **The vision is now in one place:** [`vision.md`](vision.md). It is the single,
> canonical statement of what From the Ashes is building and how: both pillars
> (a community Discord bot for the guild, and a roster/team-management tool for
> management) on one codebase. The scattered per-session brainstorming docs
> (`calcifer-vision`, `community-experience-plan`, `discord-design-system`,
> `integration-hub-plan`, `views-plan`) have been merged into it.

The vision, plus four references, no overlap:

| Doc | What it's for |
| --- | --- |
| [`vision.md`](vision.md) | **Start here.** The whole vision, the architecture approach, the design system, the feature goals, the one roadmap (Phases 0–5 plus the parallel data-spine track, with ✅ for what has shipped) and each in-development feature's path to delivery, and the locked decisions. |
| [`data-sources.md`](data-sources.md) | Reference: what each external API (WoW Audit, Blizzard, Raider.IO, Warcraft Logs) exposes, how auth works, and the local-addon data spine: the detail behind `ftabot/services/`. |
| [`communication.md`](communication.md) | Reference: Calcifer's full Discord communication palette (text markdown, dynamic `<t:…>` timestamps, mentions/`allowed_mentions`, emoji, embeds, Components V2, and message limits), framed around the house rules, the detail behind `ftabot/brand.py` + `ftabot/publisher.py`. |
| [`deploy.md`](deploy.md) | Reference: hosting options compared, step-by-step setup guides (Oracle Cloud free VM, Fly.io, your own machine), first run, backups, and the posting-guardrail workflow. |
| [`discord-bot-learnings.md`](discord-bot-learnings.md) | Reference: learnings from the popular Discord bots (MEE6, Dyno, Carl-bot, Raid-Helper, …), with a keep/borrow/skip verdict for a single-guild bot. Defers to `vision.md`; surfaces findings and a few prioritised gap fixes, it does not change the roadmap. |

For the user-facing feature/command list see [`../README.md`](../README.md); for
the codebase architecture (working on it) see [`../CLAUDE.md`](../CLAUDE.md).

**Keep ideas in one place.** New ideas, decisions and roadmap changes go into
[`vision.md`](vision.md), tied to the central two-pillar vision, not into new
side documents. That is what keeps the project's direction coherent across
sessions.
