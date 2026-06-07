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
- `spine.py` + `web.py` upload route — ingestion, auth, error codes, the roster
  alt↔main overlay, the identity read endpoint. ~13 tests.
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
- [ ] **Auxiliary tables** — `GRM_PlayerListOfAlts_Save` is **not** consumed yet;
      grouping relies solely on per-member `alts`/`isMain`/`mainName`.

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
- [ ] **End-to-end uploader → running server** — only the request building is
      tested (mocked opener). The actual `scripts/upload_savedvars.py` POST
      against a live `web.py` has not been exercised.
- [ ] **Character → Discord link** — out of scope by design; GRM holds no
      reliable Discord signal, so that hop stays a manual/officer link.

## Validation plan (when a live `.lua` arrives)

1. Drop a real (redacted is fine) `RCLootCouncil.lua` and
   `Guild_Roster_Manager.lua` into a scratch dir (do **not** commit guild data).
2. Run the parsers over them and eyeball the output:
   ```bash
   python -c "from ftabot.services.grm import parse_identities; \
       import pprint, pathlib; \
       pprint.pp(parse_identities(pathlib.Path('Guild_Roster_Manager.lua').read_text())[:5])"
   python -c "from ftabot.services.rclootcouncil import parse_loot; \
       import pathlib; print(len(parse_loot(pathlib.Path('RCLootCouncil.lua').read_text())))"
   ```
3. For each unchecked box above, confirm or fix the aliases/shape in the mapper,
   then **add a sanitised regression fixture** to the test suite so the real
   shape is locked in.
4. Tick the boxes here as they're confirmed.
