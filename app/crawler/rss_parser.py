"""
RSS/Atom 解析模块
-----------------
使用 feedparser 解析已经由 CompliantFetcher 下载好的原始字节内容。
feedparser 本身是同步阻塞的，这里通过 asyncio.to_thread 放入线程池执行，
避免阻塞事件循环（尤其是大 feed 或网络慢时）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import feedparser

logger = logging.getLogger(__name__)


@dataclass
class RawArticle:
    """从源里解析出来的"原始条目"，尚未清洗/分类。"""

    title: str
    link: str
    published_at: Optional[datetime]
    raw_summary: str
    source_name: str


def _parse_entry_time(entry: feedparser.FeedParserDict) -> Optional[datetime]:
    """feedparser 会把可解析的时间转换为 struct_time，统一转成 UTC datetime。"""
    for field in ("published_parsed", "updated_parsed"):
        struct_time = entry.get(field)
        if struct_time:
            try:
                return datetime(*struct_time[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _parse_feed_sync(raw_bytes: bytes) -> feedparser.FeedParserDict:
    return feedparser.parse(raw_bytes)


async def parse_rss(raw_bytes: bytes, source_name: str) -> list[RawArticle]:
    """解析 RSS/Atom 原始字节，返回条目列表。解析失败时返回空列表并记录日志，不抛出异常中断整体抓取。"""
    try:
        feed = await asyncio.to_thread(_parse_feed_sync, raw_bytes)
    except Exception as exc:  # noqa: BLE001 - feedparser 对畸形内容容错性有限，兜底捕获
        logger.error("解析 RSS 失败 [%s]: %s", source_name, exc)
        return []

    if feed.bozo and not feed.entries:
        logger.warning("Feed 格式异常且无可用条目 [%s]: %s", source_name, feed.get("bozo_exception"))
        return []

    articles: list[RawArticle] = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        summary = entry.get("summary") or entry.get("description") or ""
        articles.append(
            RawArticle(
                title=title,
                link=link,
                published_at=_parse_entry_time(entry),
                raw_summary=summary,
                source_name=source_name,
            )
        )
    return articles
