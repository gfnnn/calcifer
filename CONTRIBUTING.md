# Contributing to From the Ashes

Thanks for helping out! This guide covers local setup, the test/lint gates, and
how changes flow into production. For the why-behind-the-code, read
[`CLAUDE.md`](CLAUDE.md) and the technical docs in [`docs/`](docs/).

## 1. Set up

```bash
python -m pip install -r requirements-dev.txt   # runtime + test/lint deps
cp .env.example .env                             # fill in only what you need
```

Python **3.12**. Nothing here needs network access to develop or test — the bot,
posting loops, and sync all share one local SQLite file, and the test suite is fully
offline.

Seed a local database to click around:

```bash
python -m scripts.sync_cli --list   # builds ftabot.db from the live roster
python -m ftabot.serve              # web + bot + sync in one process (:8080)
```

## 2. Pick a branch

We use **GitHub Flow** — branch off `main`, keep it short-lived, open a PR. Full
details (branch types, environments, releases) are in
[`docs/branching.md`](docs/branching.md). The short version:

```bash
git checkout main && git pull
git checkout -b feat/<short-slug>     # feat/ fix/ docs/ chore/ refactor/ ...
```

## 3. Make the change — house rules

These come from [`CLAUDE.md`](CLAUDE.md); CI and reviewers enforce them:

- **No `os.environ` outside `config.py`.** Thread the `Config` object through.
- **Sync vs. manual fields.** A new character attribute the API owns goes in
  `_SYNC_COLUMNS` **and** `_MIGRATION_COLUMNS`; a human-owned field stays out so
  syncs never clobber it.
- **Keep `metrics.py` and `router.py` pure** (no I/O) — that's what makes them
  unit-testable.
- **Tests stay offline.** Never add a test that hits the network; use the `db`
  fixture in `tests/conftest.py` for a real temp-file SQLite database.

## 4. What "done" means (the delivery contract)

The bot is live in a 300-member server, so every new surface lands on a running
bot. A feature is not delivered until it clears all six:

1. **A pure renderer with offline tests.** The scoring, routing or render logic
   is a pure `(db, config) -> payload` function, unit-tested against temp-file
   SQLite with the network mocked (like `metrics.py`, `router.py`,
   `boards/hot20.py`). No surface ships without its test.
2. **A self-enabling, default-off switch.** One env flag turns it on, defaulting
   off, following `feeds_enabled` / `boards_enabled` / `scores_enabled`: a
   `Config` field (read only in `config.py`), an `.env.example` entry and a
   README configuration row, in the same change.
3. **It routes through the posting guardrail.** Every message bound for Discord
   resolves its destination via the `PostGate`; nothing calls `channel.send`
   directly.
4. **It survives a restart.** Every message id, dedup key and baseline it relies
   on persists in the DB, so a redeploy never double-posts and a deleted
   message self-heals (the `boards` / `seen_items` / `feed_outbox` pattern).
5. **Toggle, review in private, promote.** Validated in `test` posting mode
   against live data in the sandbox channel, reviewed in every state, then
   `live` + `/board promote` (boards) or the rule moved off the test channel
   (feeds). Any old bot it replaces is switched off in the same step, so there
   is never a double-posting window.
6. **The docs move in step.** Its README command, route and config rows land
   with it, and `docs/boards-contract.md` gains its entry if it is a board.

## 5. Lint & test before you push

Both run in CI and gate the PR, so run them locally first:

```bash
ruff check ftabot scripts tests     # lint gate (config in ruff.toml)
ruff check --fix ftabot scripts tests   # auto-fix what it can

python -m pytest -q                 # full suite, how CI runs it
```

CI also enforces **≥ 88% coverage** (`--cov-fail-under=88`), so add tests for new
behaviour. Code **formatting** (`ruff format`) is available but *not* enforced —
run it if you like, but it won't fail your PR.

## 6. Commit & open a PR

- Write [Conventional Commit](https://www.conventionalcommits.org/) messages:
  `feat(feeds): add Wowhead source`, `fix(sync): handle 429`.
- Push and open a PR into `main`. Fill in the template (what / why / how-tested).
- Wait for **`lint`** and **`test`** to go green and for a review.
- **Squash-merge**, then delete the branch.

That's it. Small, green, well-described PRs get merged fast.
