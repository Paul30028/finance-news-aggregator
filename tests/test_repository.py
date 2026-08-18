"""验证数据访问层对权威度分级（source_tier）的持久化与筛选，使用内存 SQLite，不依赖真实文件。"""
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.storage.models import Base
from app.storage.repository import count_articles, insert_article_if_new, list_articles


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _insert(session_factory, *, name, link, source_name, tier, category="宏观"):
    async with session_factory() as session:
        return await insert_article_if_new(
            session,
            content_hash=f"hash-{link}",
            title=name,
            link=link,
            source_name=source_name,
            source_tier=tier,
            category=category,
            summary="摘要",
            published_at=datetime.now(timezone.utc),
            fetched_at=datetime.now(timezone.utc),
        )


@pytest.mark.asyncio
async def test_insert_persists_tier_and_list_filters_by_tier(session_factory):
    await _insert(
        session_factory, name="央行决议", link="https://a.example/1",
        source_name="Fed", tier="official",
    )
    await _insert(
        session_factory, name="市场综述", link="https://b.example/2",
        source_name="BBC", tier="mainstream",
    )
    await _insert(
        session_factory, name="财经速览", link="https://c.example/3",
        source_name="GoogleNews", tier="aggregator",
    )

    async with session_factory() as session:
        official_only = await list_articles(session, tier="official")
        assert [a.title for a in official_only] == ["央行决议"]

        total_official = await count_articles(session, tier="official")
        assert total_official == 1

        all_articles = await list_articles(session)
        assert len(all_articles) == 3
        assert {a.source_tier for a in all_articles} == {"official", "mainstream", "aggregator"}


@pytest.mark.asyncio
async def test_insert_allows_none_tier(session_factory):
    is_new = await _insert(
        session_factory, name="未分级新闻", link="https://d.example/4",
        source_name="Unknown", tier=None,
    )
    assert is_new is True

    async with session_factory() as session:
        articles = await list_articles(session)
        assert articles[0].source_tier is None
