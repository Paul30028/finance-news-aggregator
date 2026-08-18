"""验证策略简报聚合（app/analysis/briefing.py）：利好/利空计数、Top榜单排序、
分类与信号词排行，均使用内存 SQLite，不依赖真实抓取。
"""
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis.briefing import build_briefing
from app.processing.signals import compute_sentiment_score, encode_signal_tags, extract_signals
from app.storage.models import Base
from app.storage.repository import insert_article_if_new


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _insert(session_factory, *, title, summary, source_name, category="宏观", hours_ago=1):
    hits = extract_signals(title, summary)
    async with session_factory() as session:
        await insert_article_if_new(
            session,
            content_hash=f"hash-{title}",
            title=title,
            link=f"https://example.com/{title}",
            source_name=source_name,
            source_tier="mainstream",
            category=category,
            summary=summary,
            published_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
            fetched_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
            sentiment_score=compute_sentiment_score(hits),
            signal_tags=encode_signal_tags(hits) or None,
        )


@pytest.mark.asyncio
async def test_briefing_counts_bullish_bearish_and_neutral(session_factory):
    await _insert(session_factory, title="央行宣布降息", summary="市场普遍看好", source_name="A")
    await _insert(session_factory, title="公司业绩预警", summary="净利润大幅下滑", source_name="B")
    await _insert(session_factory, title="公司召开股东大会", summary="按计划举行", source_name="C")

    async with session_factory() as session:
        briefing = await build_briefing(session, window_hours=24)

    assert briefing.total_articles == 3
    assert briefing.bullish_count == 1
    assert briefing.bearish_count == 1
    assert briefing.neutral_count == 1


@pytest.mark.asyncio
async def test_briefing_top_bullish_sorted_by_sentiment_score_desc(session_factory):
    # 双重利好信号（降息+回购）应排在单一利好信号（仅回购）之前
    await _insert(session_factory, title="公司宣布股票回购", summary="", source_name="A")
    await _insert(session_factory, title="央行降息叠加公司股票回购", summary="", source_name="B")

    async with session_factory() as session:
        briefing = await build_briefing(session, window_hours=24)

    assert briefing.top_bullish[0].source_name == "B"
    assert briefing.top_bullish[0].sentiment_score == 2
    assert briefing.top_bullish[1].source_name == "A"
    assert briefing.top_bullish[1].sentiment_score == 1


@pytest.mark.asyncio
async def test_briefing_excludes_articles_outside_window(session_factory):
    await _insert(session_factory, title="央行降息(窗口内)", summary="", source_name="A", hours_ago=1)
    await _insert(session_factory, title="央行降息(窗口外)", summary="", source_name="B", hours_ago=48)

    async with session_factory() as session:
        briefing = await build_briefing(session, window_hours=24)

    assert briefing.total_articles == 1
    assert briefing.top_bullish[0].source_name == "A"


@pytest.mark.asyncio
async def test_briefing_signal_and_category_counts(session_factory):
    await _insert(session_factory, title="央行降息", summary="", source_name="A", category="宏观")
    await _insert(session_factory, title="又一次降息预期升温", summary="", source_name="B", category="宏观")
    await _insert(session_factory, title="公司业绩预警", summary="", source_name="C", category="公司")

    async with session_factory() as session:
        briefing = await build_briefing(session, window_hours=24)

    category_map = {c.category: c.count for c in briefing.category_counts}
    assert category_map["宏观"] == 2
    assert category_map["公司"] == 1

    signal_map = {s.code: s.count for s in briefing.signal_counts}
    assert signal_map["rate_cut"] == 2
    assert signal_map["earnings_miss"] == 1
