<!--
  Keep PRs small and focused — one logical change per PR. The title should read
  as a Conventional Commit, e.g. "feat(feeds): add Wowhead news source".
-->

## What & why

<!-- What does this change do, and why is it needed? Link issues with "Closes #123". -->

## How it was tested

<!-- e.g. "added tests in tests/test_feeds.py; ran `pytest -q` locally" -->

## Checklist

- [ ] Branch is named with a type prefix (`feat/`, `fix/`, `docs/`, …) — see [docs/branching.md](../docs/branching.md)
- [ ] `ruff check ftabot scripts tests` passes
- [ ] `pytest -q` passes (offline; no network in tests)
- [ ] New sync-owned columns added to `_SYNC_COLUMNS` **and** `_MIGRATION_COLUMNS`; manual fields kept out (see CLAUDE.md)
- [ ] `metrics.py` / `router.py` kept pure (no I/O) if touched
- [ ] Docs / `.env.example` updated if config or behaviour changed
