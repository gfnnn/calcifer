# Contributing to From the Ashes

Thanks for helping out! This guide covers local setup, the test/lint gates, and
how changes flow into production. For the why-behind-the-code, read
[`CLAUDE.md`](CLAUDE.md) and the design docs in [`docs/`](docs/).

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

## 4. Lint & test before you push

Both run in CI and gate the PR, so run them locally first:

```bash
ruff check ftabot scripts tests     # lint gate (config in ruff.toml)
ruff check --fix ftabot scripts tests   # auto-fix what it can

python -m pytest -q                 # full suite, how CI runs it
```

CI also enforces **≥ 88% coverage** (`--cov-fail-under=88`), so add tests for new
behaviour. Code **formatting** (`ruff format`) is available but *not* enforced —
run it if you like, but it won't fail your PR.

## 5. Commit & open a PR

- Write [Conventional Commit](https://www.conventionalcommits.org/) messages:
  `feat(feeds): add Wowhead source`, `fix(sync): handle 429`.
- Push and open a PR into `main`. Fill in the template (what / why / how-tested).
- Wait for **`lint`** and **`test`** to go green and for a review.
- **Squash-merge**, then delete the branch.

That's it. Small, green, well-described PRs get merged fast.
