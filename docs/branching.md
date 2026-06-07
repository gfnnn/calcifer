# Branching & release strategy

This project uses **GitHub Flow** — a single always-deployable trunk (`main`)
with short-lived branches merged through pull requests. It is deliberately
lighter than Git Flow: there is no long-lived `develop` branch, because the team
is small, releases are continuous, and `main` deploys straight to production.

```
                     ┌─ feat/hot20-magnitude ─┐
 main ───●───────────┤                        ├──► (squash-merge) ──●──► main ──► PROD deploy
         │           └─ fix/boot-crash-loop ──┘                     │
         │                                                          └─ tag v1.4.0 ──► GitHub Release
         └─ hotfix/sync-rate-limit ───────────────────────────────────► (fast-track PR)
```

## Branches

| Branch              | Purpose                                              | Lifetime        |
| ------------------- | ---------------------------------------------------- | --------------- |
| `main`              | Always green, always deployable. Protected.          | Permanent       |
| `feat/<slug>`       | A new feature                                        | Hours–days      |
| `fix/<slug>`        | A bug fix                                             | Hours–days      |
| `hotfix/<slug>`     | Urgent production fix, fast-tracked                  | Hours           |
| `docs/<slug>`       | Docs only                                            | Short           |
| `refactor/<slug>`   | Behaviour-preserving restructuring                   | Short           |
| `perf/<slug>`       | Performance work                                     | Short           |
| `test/<slug>`       | Test-only changes                                    | Short           |
| `chore/<slug>` / `ci/<slug>` | Tooling, deps, CI                           | Short           |
| `claude/<...>`      | **Reserved** for Claude Code / agent sessions        | Short, disposable |

`<slug>` is a short kebab-case description, optionally prefixed with an issue
number: `feat/42-wishlist-export`.

**Rules of thumb**

- Branch off the latest `main`. Keep branches short-lived; rebase or merge
  `main` in if they age.
- One logical change per branch/PR. Small PRs review faster and revert cleaner.
- Never commit directly to `main` — even tiny fixes go through a PR so CI runs.

## Environments

| Stage       | Source                | Where                                              |
| ----------- | --------------------- | -------------------------------------------------- |
| Development  | feature branch + `.env` | A contributor's machine (`python -m ftabot.serve`) |
| CI / Test    | every PR & push to `main` | GitHub Actions (`ci.yml`: lint + tests + coverage) |
| Production   | `main`                | Railway / Render auto-deploy (`railway.json`, `render.yaml`) |

There is intentionally no always-on staging server (the free Render/Railway
tiers spin down on idle, which disconnects the bot). Pre-merge confidence comes
from the offline test suite; if a change needs live verification, run it locally
against a throwaway `.env` and a temp SQLite DB before merging.

## Pull requests

1. Push your branch and open a PR into `main`. The template prompts for what /
   why / how-tested and a checklist.
2. CI must be green: **`lint`** (ruff) and **`test`** (pytest + 80% coverage).
3. At least one approving review (CODEOWNERS is auto-requested).
4. **Squash-merge** so `main` keeps one tidy, Conventional-Commit-titled commit
   per change. Delete the branch after merge.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/) — the history
already follows this:

```
feat(feeds): add Wowhead news source
fix(serve): one-shot webhook-sender must not trigger shutdown
docs: document branching strategy
```

Types: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `chore`, `ci`,
`build`, `harden`. The scope (in parens) is optional but helps — common scopes:
`sync`, `feeds`, `web`, `bot`, `db`, `hot20`, `scores`.

## Releases & versioning

Tag releases with [Semantic Versioning](https://semver.org/), `vMAJOR.MINOR.PATCH`:

- **MINOR** — new commands/views/feeds, backwards-compatible.
- **PATCH** — bug fixes only.
- **MAJOR** — incompatible config/DB changes (e.g. a manual-field rename) or a
  breaking API change.

To cut a release from `main`:

```bash
git checkout main && git pull
git tag -a v1.4.0 -m "v1.4.0"
git push origin v1.4.0
```

The `release.yml` workflow then drafts a GitHub Release with auto-generated
notes from the merged PRs since the previous tag.

## Recommended branch protection for `main`

These are configured once in **GitHub → Settings → Branches** (they can't live
in the repo) — see [`maintainer-setup.md`](maintainer-setup.md) for the exact
`gh` command and the rest of the one-time settings. Apply them as the team grows:

- Require a pull request before merging (≥ 1 approval).
- Require status checks to pass: **`lint`** and **`test`**.
- Require branches to be up to date before merging.
- Require linear history (pairs well with squash-merge).
- Dismiss stale approvals on new commits.
- Do not allow direct pushes / force-pushes to `main`.
