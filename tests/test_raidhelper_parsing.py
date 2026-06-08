"""Edge-case coverage for the Raid-Helper webhook parser.

The happy path (epoch start times) is exercised in the phase/feeds tests; this
pins down the tolerant fallbacks — ISO start strings, unparseable values, and
the missing-id short-circuit — since the payload shape isn't documented and the
parser is deliberately forgiving.
"""

from __future__ import annotations

from ftabot.services.raidhelper import (
    _as_int,
    _format_start,
    _to_epoch,
    parse_webhook,
)

# --- _to_epoch --------------------------------------------------------------


def test_to_epoch_accepts_epoch_and_iso():
    assert _to_epoch(1700000000) == 1700000000
    assert _to_epoch("1700000000") == 1700000000  # numeric string
    iso = _to_epoch("2026-06-06T20:00:00+00:00")
    assert isinstance(iso, int) and iso > 0
    assert _to_epoch("2026-06-06T20:00:00Z") == iso  # Z normalised to +00:00


def test_to_epoch_returns_none_for_none_and_garbage():
    assert _to_epoch(None) is None
    assert _to_epoch("not a date") is None


# --- _as_int ----------------------------------------------------------------


def test_as_int():
    assert _as_int(5) == 5
    assert _as_int("12") == 12
    assert _as_int(None) is None
    assert _as_int("lots") is None  # unparseable -> None, not a crash


# --- _format_start ----------------------------------------------------------


def test_format_start_variants():
    assert _format_start(None) is None
    stamp = _format_start(1700000000)
    assert stamp.startswith("<t:1700000000:F>") and ":R>" in stamp
    # A value that is neither epoch nor ISO falls back to its string form.
    assert _format_start("Friday night") == "Friday night"


# --- parse_webhook ----------------------------------------------------------


def test_parse_webhook_without_event_id_is_dropped():
    assert parse_webhook({"event": {"title": "No id here"}}) is None


def test_parse_webhook_with_iso_start_populates_structured_fields():
    ev = parse_webhook(
        {
            "type": "create",
            "event": {
                "id": "evt-1",
                "title": "Mythic Night",
                "startTime": "2026-06-06T20:00:00+00:00",
                "leaderName": "Calcifer",
                "signUpCount": 18,
                "url": "https://raid-helper.dev/event/evt-1",
            },
        }
    )
    assert ev is not None
    assert ev.event_type == "raidhelper.created"
    assert ev.external_id == "raidhelper-evt-1-created"
    assert "Mythic Night" in ev.title
    assert "🕑" in ev.summary and "Calcifer" in ev.summary and "18 signed up" in ev.summary
    assert ev.extra["start_epoch"] == _to_epoch("2026-06-06T20:00:00+00:00")
    assert ev.extra["signup_count"] == 18
    assert ev.severity == "info"


def test_parse_webhook_deleted_is_a_warning_with_string_start():
    ev = parse_webhook(
        {"event": {"id": "evt-2", "name": "Cancelled Raid", "time": "whenever"}},
        action="delete",
    )
    assert ev.event_type == "raidhelper.deleted"
    assert ev.severity == "warning"
    assert "cancelled" in ev.title.lower()
    # Unparseable start -> rendered verbatim, no epoch.
    assert "whenever" in ev.summary
    assert ev.extra["start_epoch"] is None
