from ftabot.services.warcraftlogs import pick_current_raid_zone


def _zone(zone_id, expansion_id, name="Raid", frozen=False):
    return {
        "id": zone_id,
        "name": name,
        "frozen": frozen,
        "expansion": {"id": expansion_id},
    }


def test_picks_newest_raid_in_latest_expansion():
    zones = [
        _zone(33, 4, "Older Raid", frozen=True),
        _zone(38, 5, "Previous Tier", frozen=True),
        _zone(42, 5, "Current Tier"),
    ]
    assert pick_current_raid_zone(zones) == 42


def test_ignores_earlier_expansions_even_with_higher_seen_first():
    # A high id in an older expansion must not win over the latest expansion.
    zones = [
        _zone(99, 4, "Ancient Raid"),
        _zone(40, 5, "Current Tier"),
    ]
    assert pick_current_raid_zone(zones) == 40


def test_filters_out_mythic_plus_zones():
    zones = [
        _zone(40, 5, "Current Tier"),
        _zone(45, 5, "Mythic+ Season 3"),
    ]
    assert pick_current_raid_zone(zones) == 40


def test_returns_none_when_no_raid_zones():
    assert pick_current_raid_zone([]) is None
    assert pick_current_raid_zone([_zone(45, 5, "Mythic+ Dungeons")]) is None
