"""
数据访问层（Repository）
------------------------
封装所有对 Article / SourceStat 表的读写，业务代码（爬虫引擎、Web 路由）
不直接拼 SQL，统一走这里，便于以后切换数据库或加缓存。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.models import Article, SourceStat


async def article_exists(session: AsyncSession, content_hash: str) -> bool:
    stmt = select(Article.id).where(Article.content_hash == content_hash).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def insert_article_if_new(
    session: AsyncSession,
    *,
    content_hash: str,
    title: str,
    link: str,
    source_name: str,
    source_tier: Optional[str],
    category: str,
    summary: str,
    published_at: datetime,
    fetched_at: datetime,
    sentiment_score: int = 0,
    signal_tags: Optional[str] = None,
) -> bool:
    """插入一篇文章；若 content_hash 已存在则忽略。返回是否为"新入库"。

    使用 SQLite 的 `INSERT ... ON CONFLICT DO NOTHING` 保证并发安全的去重，
    避免"先查后插"之间的竞态导致重复行。
    """
    stmt = (
        sqlite_insert(Article)
        .values(
            content_hash=content_hash,
            title=title[:500],
            link=link[:1000],
            source_name=source_name[:200],
            source_tier=source_tier,
            category=category,
            summary=summary,
            published_at=published_at,
            fetched_at=fetched_at,
            sentiment_score=sentiment_score,
            signal_tags=signal_tags,
        )
        .on_conflict_do_nothing(index_elements=[Article.content_hash])
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount > 0


async def list_articles(
    session: AsyncSession,
    *,
    category: Optional[str] = None,
    source_name: Optional[str] = None,
    tier: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Article]:
    stmt = select(Article).order_by(Article.published_at.desc())
    if category:
        stmt = stmt.where(Article.category == category)
    if source_name:
        stmt = stmt.where(Article.source_name == source_name)
    if tier:
        stmt = stmt.where(Article.source_tier == tier)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(Article.title.like(like) | Article.summary.like(like))
    stmt = stmt.limit(limit).offset(offset)
    result = await session.execute(stmt)
    return result.scalars().all()


async def count_articles(
    session: AsyncSession,
    *,
    category: Optional[str] = None,
    source_name: Optional[str] = None,
    tier: Optional[str] = None,
    keyword: Optional[str] = None,
) -> int:
    stmt = select(func.count(Article.id))
    if category:
        stmt = stmt.where(Article.category == category)
    if source_name:
        stmt = stmt.where(Article.source_name == source_name)
    if tier:
        stmt = stmt.where(Article.source_tier == tier)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(Article.title.like(like) | Article.summary.like(like))
    result = await session.execute(stmt)
    return result.scalar_one()


async def list_articles_since(
    session: AsyncSession,
    *,
    since: datetime,
    limit: int = 1000,
) -> Sequence[Article]:
    """获取 fetched_at 不早于 since 的所有文章，供 /insights 简报页做窗口内聚合统计使用。"""
    stmt = (
        select(Article)
        .where(Article.fetched_at >= since)
        .order_by(Article.published_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def distinct_categories(session: AsyncSession) -> list[str]:
    stmt = select(Article.category).distinct().order_by(Article.category)
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def distinct_sources(session: AsyncSession) -> list[str]:
    stmt = select(Article.source_name).distinct().order_by(Article.source_name)
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def upsert_source_stat(
    session: AsyncSession,
    *,
    source_name: str,
    fetched_count: int,
    new_count: int,
    error: Optional[str] = None,
) -> None:
    now = datetime.utcnow()
    stmt = (
        sqlite_insert(SourceStat)
        .values(
            source_name=source_name,
            last_run_at=now,
            last_success_at=now if error is None else None,
            last_error=error,
            total_fetched=fetched_count,
            total_new=new_count,
        )
        .on_conflict_do_update(
            index_elements=[SourceStat.source_name],
            set_={
                "last_run_at": now,
                "last_success_at": now if error is None else SourceStat.last_success_at,
                "last_error": error,
                "total_fetched": SourceStat.total_fetched + fetched_count,
                "total_new": SourceStat.total_new + new_count,
            },
        )
    )
    await session.execute(stmt)
    await session.commit()


async def list_source_stats(session: AsyncSession) -> Sequence[SourceStat]:
    stmt = select(SourceStat).order_by(SourceStat.source_name)
    result = await session.execute(stmt)
    return result.scalars().all()
