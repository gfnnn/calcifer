"""Raid-tier knowledge (``ftabot/raids.py``): catalog parsing, picking the
current raid, labelling, and flattening a guild's progress into rows.

The shapes below mirror Raider.IO's live payloads (confirmed November 2026),
with invented raid names. The scenario is the real one that motivated the
module: an ended tier and a one-boss side raid both come *before* the live
main raid in the ``raid_progression`` dict.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ftabot import raids

NOW = datetime(2026, 11, 13, tzinfo=timezone.utc)


def _static(*, region: str = "eu") -> dict:
    def raid(slug, name, bosses, starts, ends):
        return {
            "slug": slug,
            "name": name,
            "short_name": name[:2].upper(),
            "starts": {region: starts},
            "ends": {region: ends},
            "encounters": [{"slug": f"boss-{i}"} for i in range(bosses)],
        }

    return {
        "raids": [
            raid("old-tier", "The Old Tier", 9, "2026-03-18T04:00:00Z", "2026-08-19T04:00:00Z"),
            raid("side-raid", "The Side Raid", 1, "2026-10-14T04:00:00Z", "2099-01-01T00:00:00Z"),
            raid("main-raid", "The Main Raid", 8, "2026-08-19T04:00:00Z", "2099-01-01T00:00:00Z"),
            raid("future-raid", "The Future Raid", 10, "2098-02-01T04:00:00Z", "2099-01-01T00:00:00Z"),
        ]
    }


def _progress(total, n=0, h=0, m=0, summary=None) -> dict:
    out = {
        "total_bosses": total,
        "normal_bosses_killed": n,
        "heroic_bosses_killed": h,
        "mythic_bosses_killed": m,
    }
    if summary:
        out["summary"] = summary
    return out


# Dict order as Raider.IO returned it: ended tier first, then the side raid.
PROGRESSION = {
    "old-tier": _progress(9, 9, 8, 4, "4/9 M"),
    "side-raid": _progress(1, 0, 1, 0, "1/1 H"),
    "main-raid": _progress(8, 8, 8, 0, "8/8 H"),
}


def test_parse_catalog_reads_names_boss_counts_and_dates():
    catalog = raids.parse_catalog(_static(), "EU")
    main = next(r for r in catalog if r.slug == "main-raid")
    assert main.name == "The Main Raid"
    assert main.total_bosses == 8
    assert main.starts_at == datetime(2026, 8, 19, 4, tzinfo=timezone.utc)
    assert len(catalog) == 4


def test_parse_catalog_is_defensive_about_shape():
    assert raids.parse_catalog(None, "eu") == []
    assert raids.parse_catalog({"raids": "nope"}, "eu") == []
    # Entries that are not raids are skipped; missing fields get safe defaults.
    catalog = raids.parse_catalog(
        {"raids": ["junk", {"name": "No slug"}, {"slug": "bare-raid", "starts": "x"}]},
        "eu",
    )
    assert [r.slug for r in catalog] == ["bare-raid"]
    bare = catalog[0]
    assert bare.name == "Bare Raid" and bare.total_bosses == 0
    assert bare.starts_at is None and bare.is_active(NOW)  # unknown dates = open


def test_parse_catalog_ignores_an_unparseable_or_other_region_date():
    data = {"raids": [{"slug": "r", "starts": {"us": "2026-01-01T00:00:00Z", "eu": "soon"}}]}
    assert raids.parse_catalog(data, "eu")[0].starts_at is None
    # A naive timestamp is read as UTC rather than rejected.
    data = {"raids": [{"slug": "r", "starts": {"eu": "2026-01-01T00:00:00"}}]}
    assert raids.parse_catalog(data, "eu")[0].starts_at.tzinfo is not None


def test_current_raids_are_the_open_ones_biggest_first():
    catalog = raids.parse_catalog(_static(), "eu")
    assert [r.slug for r in raids.current_raids(catalog, NOW)] == ["main-raid", "side-raid"]
    assert raids.current_raids(None, NOW) == []


def test_headline_is_the_biggest_open_raid_not_the_first_key():
    catalog = raids.parse_catalog(_static(), "eu")
    assert raids.headline_slug(PROGRESSION, catalog, NOW) == "main-raid"


def test_headline_falls_back_to_the_first_key_without_a_catalog():
    assert raids.headline_slug(PROGRESSION) == "old-tier"
    assert raids.headline_slug({}) is None
    # ...and when no open raid appears in the payload at all.
    catalog = raids.parse_catalog(_static(), "eu")
    assert raids.headline_slug({"old-tier": _progress(9)}, catalog, NOW) == "old-tier"
    # A non-dict entry is never picked.
    assert raids.headline_slug({"junk": 5, "real": _progress(3)}) == "real"


def test_summarize_labels_the_current_raid_with_its_real_name():
    catalog = raids.parse_catalog(_static(), "eu")
    assert raids.summarize(PROGRESSION, catalog, NOW) == "The Main Raid: 8/8 H"


def test_summarize_without_a_summary_spells_out_each_difficulty():
    out = raids.summarize({"some-raid": _progress(8, 8, 6, 2)})
    assert out == "Some Raid: 8/8 N, 6/8 H, 2/8 M"
    assert raids.summarize({}) is None


def test_order_progression_puts_open_raids_first():
    catalog = raids.parse_catalog(_static(), "eu")
    ordered = raids.order_progression(PROGRESSION, catalog, NOW)
    assert list(ordered) == ["main-raid", "side-raid", "old-tier"]
    assert list(raids.order_progression(PROGRESSION)) == list(PROGRESSION)


def test_raid_name_prefers_the_catalog():
    catalog = raids.parse_catalog(_static(), "eu")
    assert raids.raid_name("main-raid", catalog) == "The Main Raid"
    assert raids.raid_name("unknown-raid", catalog) == "Unknown Raid"


def test_kills_and_best_difficulty_tolerate_bad_values():
    assert raids.kills({"heroic_bosses_killed": "3"}, "heroic") == 3
    assert raids.kills({"heroic_bosses_killed": "many"}, "heroic") == 0
    assert raids.best_difficulty(_progress(8, 8, 3, 0)) == ("heroic", "H")
    assert raids.best_difficulty(_progress(8)) is None


def test_guild_raid_rows_flatten_progress_and_standings():
    catalog = raids.parse_catalog(_static(), "eu")
    overview = {
        "raid_progression": PROGRESSION,
        "raid_rankings": {
            "main-raid": {"heroic": {"world": 6313, "region": 2398, "realm": 17}},
            "side-raid": "garbage",
        },
    }
    rows = raids.guild_raid_rows(overview, catalog, NOW)
    assert [r["raid_slug"] for r in rows] == ["main-raid", "side-raid", "old-tier"]
    main, side, old = rows
    assert main["name"] == "The Main Raid" and main["is_current"] is True
    assert (main["normal_killed"], main["heroic_killed"], main["mythic_killed"]) == (8, 8, 0)
    assert main["ranks"]["heroic"]["realm"] == 17
    assert side["ranks"] == {}  # malformed standings are dropped, not raised
    assert old["is_current"] is False and old["mythic_killed"] == 4


def test_guild_raid_rows_without_a_catalog_treat_everything_as_current():
    rows = raids.guild_raid_rows({"raid_progression": PROGRESSION})
    assert all(r["is_current"] for r in rows)
    assert rows[0]["name"] == "Old Tier"
    assert raids.guild_raid_rows({"raid_progression": None}) == []
    assert raids.guild_raid_rows({"raid_progression": {"junk": 1}}) == []
