"""The database backup script: a consistent copy plus pruning."""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

from ftabot.models import Member
from scripts import backup_db


async def test_backup_copies_a_live_database(db, tmp_path):
    await db.upsert_synced(Member("Emberlyn", item_level=330.0))
    target = backup_db.backup(db.path, str(tmp_path / "backups"))

    assert target.name.startswith("ftabot-") and target.suffix == ".db"
    copy = sqlite3.connect(target)
    try:
        rows = copy.execute("SELECT character_name, item_level FROM members").fetchall()
    finally:
        copy.close()
    assert rows == [("Emberlyn", 330.0)]


async def test_backup_keeps_only_the_newest_copies(db, tmp_path):
    dest = tmp_path / "backups"
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    for day in range(5):
        backup_db.backup(db.path, str(dest), keep=3, now=start + timedelta(days=day))
    names = sorted(p.name for p in dest.iterdir())
    assert names == [
        "ftabot-20261103-000000.db",
        "ftabot-20261104-000000.db",
        "ftabot-20261105-000000.db",
    ]


def test_backup_refuses_a_missing_database(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_db.backup(str(tmp_path / "nope.db"), str(tmp_path / "backups"))
    assert not (tmp_path / "backups").exists()  # nothing was created


async def test_backup_cli_uses_the_configured_database(db, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_PATH", db.path)
    monkeypatch.setattr(
        sys, "argv", ["backup_db", "--dest", str(tmp_path / "out"), "--keep", "2"]
    )
    backup_db.main()
    assert "Backed up to" in capsys.readouterr().out
    assert len(list((tmp_path / "out").glob("ftabot-*.db"))) == 1
