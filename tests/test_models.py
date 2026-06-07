from ftabot.models import (
    CLASSES,
    DEFAULT_EMBED_COLOR,
    Member,
    class_color,
    normalize_realm,
)
from ftabot.services.raiderio import _summarize_progression


def test_normalize_realm():
    assert normalize_realm("Hellfire") == "hellfire"
    assert normalize_realm("Argent Dawn") == "argent-dawn"
    assert normalize_realm("Aerie Peak") == "aerie-peak"
    assert normalize_realm("Kil'jaeden") == "kiljaeden"
    assert normalize_realm("  Twisting   Nether ") == "twisting-nether"


def test_class_color_known_and_unknown():
    assert class_color("Mage") == 0x3FC7EB
    assert class_color("mage") == 0x3FC7EB  # case-insensitive
    assert class_color(None) == DEFAULT_EMBED_COLOR
    assert class_color("Tinkerer") == DEFAULT_EMBED_COLOR


def test_member_key_and_display():
    m = Member(character_name="Ashbringer", realm_slug="hellfire", region="eu")
    assert m.key == ("ashbringer", "hellfire", "eu")
    assert m.display() == "Ashbringer-Hellfire"


def test_classes_count():
    # The current WoW class line-up has 13 classes.
    assert len(CLASSES) == 13
    assert "Evoker" in CLASSES


def test_summarize_progression_with_summary():
    prog = {"nerubar-palace": {"summary": "8/8 N, 6/8 H", "total_bosses": 8}}
    assert _summarize_progression(prog) == "Nerubar Palace: 8/8 N, 6/8 H"


def test_summarize_progression_without_summary():
    prog = {
        "liberation-of-undermine": {
            "total_bosses": 8,
            "normal_bosses_killed": 8,
            "heroic_bosses_killed": 4,
            "mythic_bosses_killed": 0,
        }
    }
    out = _summarize_progression(prog)
    assert out == "Liberation Of Undermine: 8/8 N, 4/8 H, 0/8 M"


def test_summarize_progression_empty():
    assert _summarize_progression({}) is None
