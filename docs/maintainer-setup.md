# Maintainer setup — one-time GitHub settings

A few pieces of the development/testing workflow live in **GitHub repository
settings**, not in the repo, so they can't be committed and must be applied once
by an admin. This file is the checklist of what's left after merging the
branching/CI changes. See [`branching.md`](branching.md) for the rationale.

Status legend: ☐ = to do, ☑ = done.

## ☐ Branch protection for `main`

The CI gate (`lint` + `test`) and PR review flow are only *enforced* once `main`
is protected. The `lint`/`test` checks only become selectable after CI has run
at least once (PR #22's run makes them appear).

**Apply via `gh` (fastest):**

```bash
gh api -X PUT repos/gfnnn/Fta/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": { "strict": true, "contexts": ["lint", "test"] },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

**Or via the UI:** Settings → Branches → add a rule/ruleset for `main`:

- Require a pull request before merging → 1 approval + require Code Owner review
- Require status checks to pass → `lint`, `test` + require branches up to date
- Require linear history
- Dismiss stale approvals on new push
- Block force pushes & deletions

## ☐ Merge settings (Settings → General → Pull Requests)

These make the squash-merge + linear-history model in `branching.md` the default:

- Allow merge commits: **off**
- Allow squash merging: **on** (set default message to "PR title and description")
- Allow rebase merging: off (optional)
- **Automatically delete head branches**: on

## ☐ CODEOWNERS access

`.github/CODEOWNERS` assigns `@gfnnn` as the owner. Code-owner review enforcement
only works if that account has **write** access to the repo (it's the owner here,
so this should already be satisfied — confirm if more reviewers are added later).

## ☐ Actions secrets for richer sync (Settings → Secrets and variables → Actions)

`ci.yml` needs none. The scheduled `sync.yml` works with zero secrets (Raider.IO
is keyless); add these only for richer data:

- `WOWAUDIT_API_KEY`
- `BLIZZARD_CLIENT_ID`
- `BLIZZARD_CLIENT_SECRET`

## ☑ Already in the repo (no action needed)

- CI lint + test + 80% coverage gate (`.github/workflows/ci.yml`)
- Release-on-tag workflow (`.github/workflows/release.yml`)
- Lint config (`ruff.toml`), PR/issue templates, `CONTRIBUTING.md`, `branching.md`

## Optional follow-ups (deferred by design)

- **`ruff format`** is intentionally *not* enforced (a one-time `ruff format .`
  would touch ~55 files). Adopt it later as its own PR if the team wants it.
- **Staging environment** is intentionally omitted — free Render/Railway tiers
  spin down on idle and disconnect the bot. Add one only if a paid always-on
  pre-prod becomes worthwhile.
