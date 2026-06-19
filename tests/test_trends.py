from ftabot import views
from ftabot.config import Config

CFG = Config()


async def _add(db, **kw):
    await db.add_snapshot(kw)


async def test_trend_series_chronological_and_deltas(db):
    await _add(db, member_count=10, active_count=8, health_score=70, avg_item_level=620)
    await _add(db, member_count=12, active_count=11, health_score=78, avg_item_level=628)

    t = await views.trends(db, CFG)
    assert t["count"] == 2
    # Oldest first.
    assert t["series"]["member_count"] == [10, 12]
    assert t["deltas"]["member_count"] == 2
    assert t["deltas"]["health_score"] == 8
    assert t["latest"]["member_count"] == 12


async def test_trends_embedded_in_gm_report(db):
    await _add(db, member_count=10, health_score=70)
    await _add(db, member_count=11, health_score=72)
    r = await views.gm_report(db, CFG)
    assert "trends" in r
    assert r["trends"]["count"] == 2
