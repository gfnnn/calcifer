# Data spine — validation status & known testing gaps

> **Read this before trusting the spine on live data.** The ingestion pipeline
> (parser → mappers → identity graph → DB) is **fully covered by offline unit
> tests**, but those tests run on **synthetic fixtures hand-built from the
> addon source + community docs — not from a real guild dump.** The shapes
> below are *inferred*, not *confirmed*. This file tracks what still needs a
> real-world check.

Related: [`data-sources.md` §5](data-sources.md#5-local-desktop-addon-databases--the-identity--loot-ground-truth)
· [`vision.md` §5c](vision.md#5c-the-data-spine--the-long-term-core).

## What *is* tested (offline)

- `services/savedvars.py` — the Lua parser: scalars, strings (quoted + long
  bracket), escapes, nested/mixed/keyed tables, comments, whole-file parsing,
  error paths. ~17 tests.
- `services/rclootcouncil.py` — loot mapping, both date formats, item links,
  difficulty, dedup, malformed-award skipping. ~8 tests.
- `services/grm.py` — member-record extraction, positional + named alts, join
  dates, shape-agnostic walking, dedup, non-member tables ignored. ~7 tests.
- `identity.py` — account grouping / main selection / transitive links. ~7 tests.
- `spine.py` + the `ingest.py` upload route — ingestion, auth, error codes, the
  roster alt↔main overlay, the stored identity record. ~13 tests.
- `scripts/upload_savedvars.py` — request building + exit codes with a faked
  opener. ~5 tests.

Full suite green at ~83% coverage. **None of this proves the fixtures match
reality.**

## What is *not* yet verified against real data

### GRM (`services/grm.py`) — highest risk
The parser is deliberately shape-agnostic, but these are all **assumptions** until
checked against a real `Guild_Roster_Manager.lua`:

- [ ] **Top-level nesting** — assumed `GRM_GuildMemberHistory_Save[faction][guild][members]`.
      Faction may be `[1]/[2]` or `"Horde"/"Alliance"`; guild key may be
      `"Guild"` or `"Guild-Realm"`. (We walk it generically, so this *should*
      survive, but it is unconfirmed.)
- [ ] **Members container** — array (`[1],[2],…`) vs. name-keyed (`["Name-Realm"]`).
      Both are handled; neither is confirmed.
- [ ] **Field names / aliases** — `name`, `class`, `GUID`, `rankName` vs `rank`,
      `isMain`, `mainName` vs `main`, `customNote` vs `note`, `officerNote`.
      The `_STRONG_MARKERS` and alias tuples in `grm.py` are the first place to
      adjust if a field is named differently in the live release.
- [ ] **Join date** — `joinDateUnix` (epoch) assumed primary; `joinDate` string
      and `joinDateHist` tuple are fallbacks built from guesses about their shape.
- [ ] **Alts structure** — assumed `alts = { {name,class}, … }` (named or
      positional `[1]=name`). Real GRM may store alt GUIDs / richer records.
- [ ] **Auxiliary tables** — `GRM_PlayerListOfAlts_Save` is now consumed
      (`grm.alt_groups_from_savedvars` → extra union links in `build_accounts`),
      but its exact nesting is unconfirmed. The walker is shape-agnostic and
      **purely additive** (it only links characters; main selection still comes
      from per-member `isMain`/`mainName`), so a wrong shape yields no groups
      rather than bad grouping. Confirm the `[guild?][main] = { alts }` shape and
      whether alt entries store names or GUIDs against a real dump.

### RCLootCouncil (`services/rclootcouncil.py`) — lower risk (schema is stable)
- [ ] **`RCLootCouncilLootDB` top-level shape** — assumed `{ ["Name-Realm"]: [awards] }`.
      Confirm there's no extra realm/profile wrapping in the guild's dump.
- [ ] **`difficultyID` values** — the map covers common raid ids; confirm the
      ones that actually appear.
- [ ] **Field presence** — `awarded_by` / item level / slot are not stored by
      the addon in this layer, so they're left `NULL`; confirm nothing richer is
      available before building features that need them.

### Pipeline / operational
- [ ] **Real file size & performance** — tested on tiny fixtures only. A full
      guild history can be multi-MB; the parser is O(n) but unprofiled at scale.
- [x] **End-to-end uploader → running server** — `scripts/upload_savedvars.py`
      was run against a locally started `python -m ftabot.serve` (ingest
      listener) and imported a dump. Not yet exercised against the deployed host.
- [ ] **Character → Discord link** — out of scope by design; GRM holds no
      reliable Discord signal, so that hop stays a manual/officer link.

## Tooling (built; ready for a live `.lua`)

Two scripts turn "a real dump arrives" into a quick confirm-and-tick:

- **`python -m scripts.inspect_savedvars <file.lua>`** — prints which globals /
  addons are present, the top-level table shape (types only, no data), and a few
  parsed sample records, so you can confirm or fix the assumptions above fast.
- **`python -m scripts.redact_savedvars <file.lua> > fixture.lua`** — rewrites
  character names to stable pseudonyms, GUIDs to hashes, and drops notes, while
  preserving the table shape, so the output is **committable** (no guild PII) and
  still valid Lua the parsers ingest. It warns on stderr if any original name
  survives. Both are covered offline in `tests/test_redact_savedvars.py`.

## Validation plan (when a live `.lua` arrives)

1. Drop a real `RCLootCouncil.lua` / `Guild_Roster_Manager.lua` into a scratch
   dir (do **not** commit the raw file).
2. `python -m scripts.inspect_savedvars <file.lua>` and check each box above
   against the reported shape; fix the aliases/shape in the mapper if they differ.
3. `python -m scripts.redact_savedvars <file.lua> > tests/fixtures/<addon>.lua`
   and add a regression test that parses the fixture and asserts the confirmed
   shape (counts, a known main↔alts group, date parsing) — locking the real
   shape into the suite.
4. Tick the boxes here as they're confirmed.
