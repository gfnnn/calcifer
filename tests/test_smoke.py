"""Offline tests for the live-API smoke harness.

The per-service network calls can't run in CI, but the result model, the
"which integrations are configured" decision, the table formatting, the exit
code, and the (offline) Raid-Helper readiness check all can.
"""

from __future__ import annotations

import importlib

from ftabot.config import Config
from scripts.smoke_all import (
    FAIL,
    OK,
    SKIP,
    SmokeResult,
    configured,
    exit_code,
    format_results,
)


def test_configured_reflects_credentials():
    bare = configured(Config())
    assert bare["Raider.IO"] is True  # keyless, always checkable
    assert bare["Blizzard"] is False
    assert bare["WoW Audit"] is False
    assert bare["Raid-Helper"] is False

    full = configured(Config(
        wowaudit_api_key="k",
        blizzard_client_id="i", blizzard_client_secret="s",
        warcraftlogs_client_id="i", warcraftlogs_client_secret="s",
        raidhelper_webhook_secret="shh",
    ))
    assert all(full.values())


def test_exit_code_fails_only_on_a_real_failure():
    assert exit_code([SmokeResult("A", OK), SmokeResult("B", SKIP)]) == 0
    assert exit_code([SmokeResult("A", OK), SmokeResult("B", FAIL)]) == 1


def test_format_results_aligns_and_summarises():
    out = format_results([
        SmokeResult("WoW Audit", OK, "team 'X', 5 chars"),
        SmokeResult("Blizzard", FAIL, "HTTP 401"),
        SmokeResult("Raid-Helper", SKIP, "not configured"),
    ])
    assert "✓ ok" in out
    assert "✗ fail" in out
    assert "– skip" in out
    assert "1 ok · 1 failed · 1 skipped" in out


async def test_raidhelper_check_is_offline_and_passes_on_a_sample():
    result = await importlib.import_module("scripts.smoke_all")._check_raidhelper(
        Config(raidhelper_webhook_secret="shh")
    )
    assert result.status == OK
    assert result.name == "Raid-Helper"


async def test_raidhelper_check_fails_when_parser_rejects_the_sample(monkeypatch):
    # If the parser ever stops accepting the canned payload, that's a real failure
    # the smoke check must surface — not a silent pass.
    smoke = importlib.import_module("scripts.smoke_all")
    monkeypatch.setattr("ftabot.services.raidhelper.parse_webhook", lambda payload: None)
    result = await smoke._check_raidhelper(Config(raidhelper_webhook_secret="shh"))
    assert result.status == FAIL
    assert "parser returned None" in result.detail


def test_standalone_check_scripts_import_cleanly():
    # Importing covers each script's module body (the network main() is pragma'd).
    for name in ("check_blizzard", "check_warcraftlogs", "check_raiderio",
                 "check_raidhelper", "check_wowaudit"):
        module = importlib.import_module(f"scripts.{name}")
        assert callable(module.main)
