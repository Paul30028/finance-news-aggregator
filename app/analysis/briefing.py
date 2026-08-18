"""
策略简报聚合
------------
把某个时间窗口内已入库的文章，按 `app/processing/signals.py` 打好的信号标签
和情绪分做聚合统计，生成 `/insights` 页面展示用的"策略简报"：
  - 窗口内利好 / 利空 / 中性文章数量
  - 情绪分最高（最利好）与最低（最利空）的若干条文章
  - 分类热度排行（哪个板块新闻最多）
  - 信号词命中排行（哪类事件本轮窗口内出现最频繁）

再次强调设计边界：这里只是对"已经命中了哪些预定义关键词规则"做统计汇总，
是对新闻数据本身的客观整理，不是对市场走势的预测，更不是投资建议。
所有数字都可以追溯到具体文章和具体命中的关键词，没有任何"黑箱模型输出"。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.processing.signals import decode_signal_tags
from app.storage.models import Article
from app.storage.repository import list_articles_since


@dataclass
class ArticleBrief:
    id: int
    title: str
    link: str
    source_name: str
    source_tier: str | None
    category: str
    summary: str
    published_at: datetime
    sentiment_score: int
    signals: list[tuple[str, str, int]]  # (code, label, polarity)


@dataclass
class SignalCount:
    code: str
    label: str
    polarity: int
    count: int


@dataclass
class CategoryCount:
    category: str
    count: int


@dataclass
class BriefingResult:
    window_hours: int
    generated_at: datetime
    total_articles: int
    bullish_count: int
    bearish_count: int
    neutral_count: int
    top_bullish: list[ArticleBrief] = field(default_factory=list)
    top_bearish: list[ArticleBrief] = field(default_factory=list)
    category_counts: list[CategoryCount] = field(default_factory=list)
    signal_counts: list[SignalCount] = field(default_factory=list)


def _to_brief(article: Article) -> ArticleBrief:
    return ArticleBrief(
        id=article.id,
        title=article.title,
        link=article.link,
        source_name=article.source_name,
        source_tier=article.source_tier,
        category=article.category,
        summary=article.summary,
        published_at=article.published_at,
        sentiment_score=article.sentiment_score,
        signals=decode_signal_tags(article.signal_tags),
    )


async def build_briefing(
    session: AsyncSession, *, window_hours: int = 24, top_n: int = 8
) -> BriefingResult:
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    articles = await list_articles_since(session, since=since, limit=2000)

    bullish = [a for a in articles if a.sentiment_score > 0]
    bearish = [a for a in articles if a.sentiment_score < 0]
    neutral_count = len(articles) - len(bullish) - len(bearish)

    top_bullish = sorted(bullish, key=lambda a: (a.sentiment_score, a.published_at), reverse=True)[:top_n]
    top_bearish = sorted(bearish, key=lambda a: (a.sentiment_score, -a.published_at.timestamp()))[:top_n]

    category_counter = Counter(a.category for a in articles)
    category_counts = [
        CategoryCount(category=c, count=n) for c, n in category_counter.most_common()
    ]

    signal_counter: Counter[str] = Counter()
    signal_meta: dict[str, tuple[str, int]] = {}
    for a in articles:
        for code, label, polarity in decode_signal_tags(a.signal_tags):
            signal_counter[code] += 1
            signal_meta[code] = (label, polarity)
    signal_counts = [
        SignalCount(code=code, label=signal_meta[code][0], polarity=signal_meta[code][1], count=n)
        for code, n in signal_counter.most_common()
    ]

    return BriefingResult(
        window_hours=window_hours,
        generated_at=datetime.now(timezone.utc),
        total_articles=len(articles),
        bullish_count=len(bullish),
        bearish_count=len(bearish),
        neutral_count=neutral_count,
        top_bullish=[_to_brief(a) for a in top_bullish],
        top_bearish=[_to_brief(a) for a in top_bearish],
        category_counts=category_counts,
        signal_counts=signal_counts,
    )
