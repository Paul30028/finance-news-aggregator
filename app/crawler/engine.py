"""
抓取引擎
--------
负责单个源的完整处理流程：
  抓取(合规限速+重试+条件请求) -> 解析(RSS/HTML) -> 清洗 -> 屏蔽词过滤 -> 分类 -> 去重入库
  -> 立即 SSE 广播 + 推送通知（即时性的关键：不等整轮抓取结束，处理完一个源就报出一个源）

`run_source_once()` 是核心方法，供两种调用方使用：
  1. Scheduler 中每个源自己的独立调度循环（常规、周期性）
  2. Web API `/api/crawl-now`（手动触发全部源立即抓取一次）

单个源抛出的任何异常都会被捕获并记录到 SourceStat，不会影响其他源。
"""
from __future__ import annotations

import asyncio
import logging

from app.config import SourceConfig, get_settings
from app.crawler.dedup import compute_content_hash
from app.crawler.html_fallback import extract_list_items, extract_summary
from app.crawler.http_client import NOT_MODIFIED, CompliantFetcher, FetchBlockedError, FetchFailedError
from app.crawler.rss_parser import RawArticle, parse_rss
from app.notify.dispatcher import SignalAlert, dispatch_new_articles, dispatch_signal_alerts
from app.processing.classifier import classify
from app.processing.cleaner import CleanedArticle, clean_article, is_blocked
from app.processing.signals import (
    compute_sentiment_score,
    conclusion_for_codes,
    encode_signal_tags,
    extract_signals,
    is_alert_score,
)
from app.storage.db import get_session
from app.storage.repository import insert_article_if_new, upsert_source_stat
from app.web.events import broadcaster

logger = logging.getLogger(__name__)


class CrawlEngine:
    """长期存活的引擎实例：内部持有的 CompliantFetcher 会跨多轮抓取复用连接与条件请求缓存。"""

    def __init__(self):
        settings = get_settings()
        self._fetcher = CompliantFetcher(settings.crawler)
        self._semaphore = asyncio.Semaphore(settings.crawler.max_concurrency)

    async def aclose(self) -> None:
        await self._fetcher.aclose()

    async def _fetch_source_articles(self, source: SourceConfig) -> list[RawArticle]:
        """抓取单个源，返回原始条目（未清洗）。命中 304 Not Modified 时返回空列表。"""
        if source.type == "rss":
            resp = await self._fetcher.fetch(source.url)
            if resp.status_code == NOT_MODIFIED:
                logger.debug("源 [%s] 内容未变化 (304)，跳过解析", source.name)
                return []
            return await parse_rss(resp.content, source.name)

        if source.type == "html":
            if not source.list_selector:
                logger.error("网页源 [%s] 未配置 list_selector，跳过", source.name)
                return []
            resp = await self._fetcher.fetch(source.url)
            if resp.status_code == NOT_MODIFIED:
                logger.debug("源 [%s] 列表页未变化 (304)，跳过解析", source.name)
                return []
            items = extract_list_items(resp.text, source.list_selector, source.url, source.name)

            # 尝试为每条列表项补充摘要：额外请求详情页，失败不影响主流程
            for item in items[:20]:  # 限制单轮详情页请求数量，避免对目标站点造成压力
                try:
                    detail_resp = await self._fetcher.fetch(item.link)
                    if detail_resp.status_code != NOT_MODIFIED:
                        item.raw_summary = extract_summary(detail_resp.text)
                except (FetchBlockedError, FetchFailedError) as exc:
                    logger.debug("详情页摘要抓取失败 [%s]: %s", item.link, exc)
            return items

        logger.error("未知的源类型 [%s]: %s", source.name, source.type)
        return []

    async def run_source_once(self, source: SourceConfig) -> list[CleanedArticle]:
        """处理单个源：抓取 -> 清洗 -> 分类 -> 去重入库 -> 立即广播/推送。

        返回本次新增的 CleanedArticle 列表。这是即时性设计的核心：
        每个源各自独立完成"抓到 -> 报出"，不等待其他源，也不等待固定的整轮周期。
        """
        settings = get_settings()
        new_articles: list[CleanedArticle] = []
        broadcast_payload: list[dict] = []
        signal_alerts: list[SignalAlert] = []
        fetched_count = 0
        error_msg = None

        async with self._semaphore:
            try:
                raw_articles = await self._fetch_source_articles(source)
                fetched_count = len(raw_articles)

                async with get_session() as session:
                    for raw in raw_articles:
                        cleaned = clean_article(raw, settings.crawler.summary_max_length)

                        blocked_kw = is_blocked(
                            cleaned.title, cleaned.summary, settings.classification.block_keywords
                        )
                        if blocked_kw:
                            logger.info("命中屏蔽词 '%s'，丢弃: %s", blocked_kw, cleaned.title)
                            continue

                        category = classify(
                            cleaned.title,
                            cleaned.summary,
                            settings.classification.categories,
                            source.category_hint,
                        )
                        content_hash = compute_content_hash(cleaned.title, cleaned.link)

                        signal_hits = extract_signals(cleaned.title, cleaned.summary)
                        sentiment_score = compute_sentiment_score(signal_hits)
                        signal_tags = encode_signal_tags(signal_hits)

                        is_new = await insert_article_if_new(
                            session,
                            content_hash=content_hash,
                            title=cleaned.title,
                            link=cleaned.link,
                            source_name=cleaned.source_name,
                            source_tier=source.tier,
                            category=category,
                            summary=cleaned.summary,
                            published_at=cleaned.published_at,
                            fetched_at=cleaned.fetched_at,
                            sentiment_score=sentiment_score,
                            signal_tags=signal_tags or None,
                        )
                        if is_new:
                            new_articles.append(cleaned)
                            watch_note, confidence = conclusion_for_codes(
                                [h.code for h in signal_hits]
                            )
                            is_alert = is_alert_score(sentiment_score, settings.signals.alert_threshold)
                            broadcast_payload.append(
                                {
                                    "title": cleaned.title,
                                    "link": cleaned.link,
                                    "source": cleaned.source_name,
                                    "tier": source.tier,
                                    "category": category,
                                    "summary": cleaned.summary,
                                    "published_at": cleaned.published_at.isoformat(),
                                    "sentiment_score": sentiment_score,
                                    "signals": [h.label for h in signal_hits],
                                    "watch_note": watch_note,
                                    "confidence": confidence,
                                    "is_alert": is_alert,
                                }
                            )
                            if is_alert:
                                signal_alerts.append(
                                    SignalAlert(
                                        title=cleaned.title,
                                        link=cleaned.link,
                                        source_name=cleaned.source_name,
                                        sentiment_score=sentiment_score,
                                        signal_labels=[h.label for h in signal_hits],
                                        watch_note=watch_note,
                                        confidence=confidence,
                                    )
                                )

            except FetchBlockedError:
                error_msg = "robots.txt 禁止抓取"
                logger.info("源 [%s] 被 robots.txt 禁止，跳过", source.name)
            except FetchFailedError as exc:
                error_msg = str(exc)
                logger.error("源 [%s] 抓取失败: %s", source.name, exc)
            except Exception as exc:  # noqa: BLE001 - 单源异常不能拖垮整体调度
                error_msg = str(exc)
                logger.exception("源 [%s] 处理时发生未预期异常", source.name)

        async with get_session() as session:
            await upsert_source_stat(
                session,
                source_name=source.name,
                fetched_count=fetched_count,
                new_count=len(new_articles),
                error=error_msg,
            )

        if new_articles:
            logger.info("源 [%s] 新增 %d 篇文章，立即推送", source.name, len(new_articles))
            # 广播与 Webhook/Telegram 推送并发进行，且不等待彼此，最大化即时性；
            # 单独 catch 异常，避免推送失败影响下一个源的抓取
            try:
                await broadcaster.publish(broadcast_payload)
            except Exception:  # noqa: BLE001
                logger.exception("SSE 广播新文章时发生异常")
            try:
                await dispatch_new_articles(settings.notify, new_articles)
            except Exception:  # noqa: BLE001
                logger.exception("推送新文章时发生异常")

        if signal_alerts and settings.signals.push_alerts:
            logger.info("源 [%s] 命中 %d 条重点信号，发送独立提醒", source.name, len(signal_alerts))
            try:
                await dispatch_signal_alerts(settings.notify, signal_alerts)
            except Exception:  # noqa: BLE001
                logger.exception("推送重点信号提醒时发生异常")

        return new_articles

    async def run_all_once(self) -> int:
        """立即并发抓取所有已启用的源一次（供 /api/crawl-now 手动触发使用）。"""
        settings = get_settings()
        enabled_sources = [s for s in settings.sources if s.enabled]
        if not enabled_sources:
            logger.warning("没有已启用的数据源，跳过本轮抓取")
            return 0

        logger.info("手动触发抓取，共 %d 个启用源", len(enabled_sources))
        results = await asyncio.gather(
            *(self.run_source_once(s) for s in enabled_sources), return_exceptions=False
        )
        total_new = sum(len(r) for r in results)
        logger.info("手动抓取完成，新增 %d 篇文章", total_new)
        return total_new
