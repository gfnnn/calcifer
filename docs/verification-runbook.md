# Verification runbook (fresh session)

The credential- and data-gated checks that can only run from a **fresh** session
booted with the relevant secrets/files present. A SessionStart hook
(`.claude/hooks/session-start.sh`) installs dependencies automatically, so the
commands below work immediately — no manual `pip install`.

> Why "fresh session": secrets are injected into the container at startup, so a
> session that began *before* a secret was added can't see it. Add the secret to
> the environment settings, start a new session, then run the check.

## 1. Live-API smoke checks

One shot — runs every check the injected credentials allow and prints a table:

```bash
python -m scripts.smoke_all
```

Or per integration (verbose):

```bash
python -m scripts.check_blizzard       # BLIZZARD_CLIENT_ID / _SECRET
python -m scripts.check_warcraftlogs   # WARCRAFTLOGS_CLIENT_ID / _SECRET
python -m scripts.check_raiderio       # keyless — no secret needed
python -m scripts.check_raidhelper     # RAIDHELPER_WEBHOOK_SECRET
python -m scripts.check_wowaudit       # WOWAUDIT_API_KEY
```

On a ✓, tick the matching box in [`testing-status.md`](testing-status.md) with
the date.

## 2. Confirm the data-spine shapes against a real dump

When the GM provides a real `Guild_Roster_Manager.lua` / `RCLootCouncil.lua`
(put them in a scratch dir — **do not commit the raw files**):

```bash
# a) See the real structure; check it against the assumptions in the doc.
python -m scripts.inspect_savedvars /path/to/Guild_Roster_Manager.lua

# b) Fix any aliases/shape in ftabot/services/grm.py or rclootcouncil.py if they differ.

# c) Redact to a committable fixture (names -> pseudonyms, GUIDs hashed, notes dropped).
python -m scripts.redact_savedvars /path/to/Guild_Roster_Manager.lua \
    > tests/fixtures/grm_sample.lua

# d) Add a regression test that parses the fixture and asserts the confirmed shape,
#    then tick the boxes in docs/data-spine-validation.md.
```

See [`data-spine-validation.md`](data-spine-validation.md) for the full checklist
of shapes to confirm (GRM is the highest-risk).

## 3. Standard checks (no secrets needed)

```bash
ruff check ftabot scripts tests
python -m pytest -q
```
