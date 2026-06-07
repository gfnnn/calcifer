"""Editable WoW reference data for comp building.

Raid buffs/utility change between patches, so this is intentionally a plain,
hand-editable table rather than anything clever. Each entry maps a raid-wide
effect to the classes that can provide it. Adjust to taste / current tier.
"""

from __future__ import annotations

# Raid-wide buffs and the classes that bring them (The War Within era).
RAID_BUFFS: dict[str, set[str]] = {
    "Intellect (Arcane Intellect)": {"Mage"},
    "Stamina (Power Word: Fortitude)": {"Priest"},
    "Attack Power (Battle Shout)": {"Warrior"},
    "Versatility (Mark of the Wild)": {"Druid"},
    "Skyfury": {"Shaman"},
    "Bloodlust / Heroism": {"Shaman", "Mage", "Hunter", "Evoker"},
    "Battle Resurrection": {"Druid", "Death Knight", "Warlock"},
}


def buff_coverage(class_list: list[str]) -> dict[str, dict]:
    """For the given classes present, report each buff's coverage.

    Returns ``{buff: {"covered": bool, "providers": [classes present]}}``.
    """
    present = set(class_list)
    out: dict[str, dict] = {}
    for buff, providers in RAID_BUFFS.items():
        covering = sorted(present & providers)
        out[buff] = {"covered": bool(covering), "providers": covering}
    return out


def missing_buffs(class_list: list[str]) -> list[str]:
    """Buffs not covered by the given classes."""
    return [b for b, info in buff_coverage(class_list).items() if not info["covered"]]
