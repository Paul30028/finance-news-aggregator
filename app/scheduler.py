"""
调度器
------
即时性设计的核心：每个数据源拥有自己独立的抓取循环，互不等待、互不阻塞。
一个源抓完立刻按自己的 interval_seconds（未设置则用全局默认）休眠、再抓，
不会出现"因为某个慢源/大源拖住了整批处理，导致其他源的新闻延迟报出"的情况。

`sync_sources()` 会对比当前配置与正在运行的任务：
  - 新增或重新启用的源 -> 立即创建任务并马上抓一次（不必等下一个全局周期）
  - 被删除或停用的源 -> 取消其任务
这使得通过 Web/API 动态增删源时，新源能够立刻开始产出即时新闻，而不是排队等待。
"""
from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.crawler.engine import CrawlEngine

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self):
        self._engine: CrawlEngine | None = None
        self._source_tasks: dict[str, asyncio.Task] = {}
        self._stopping = False

    def start(self) -> None:
        self._stopping = False
        if self._engine is None:
            self._engine = CrawlEngine()
        self.sync_sources()
        logger.info("调度器已启动（按源独立调度）")

    def sync_sources(self) -> None:
        """根据最新配置，增删每个源的独立调度任务。可在配置热加载后随时调用。"""
        settings = get_settings()
        enabled_names = {s.name for s in settings.sources if s.enabled}

        for source in settings.sources:
            if not source.enabled:
                continue
            existing = self._source_tasks.get(source.name)
            if existing is None or existing.done():
                self._source_tasks[source.name] = asyncio.create_task(
                    self._source_loop(source.name), name=f"crawl-{source.name}"
                )
                logger.info("已为源 [%s] 启动独立抓取循环", source.name)

        for name in list(self._source_tasks):
            if name not in enabled_names:
                self._source_tasks[name].cancel()
                del self._source_tasks[name]
                logger.info("源 [%s] 已停用/删除，取消其抓取循环", name)

    async def _source_loop(self, source_name: str) -> None:
        assert self._engine is not None
        while not self._stopping:
            settings = get_settings()
            source = next(
                (s for s in settings.sources if s.name == source_name and s.enabled), None
            )
            if source is None:
                # 源已被停用或删除，结束循环；若之后重新启用，sync_sources 会重新创建
                return

            try:
                await self._engine.run_source_once(source)
            except Exception:  # noqa: BLE001 - 单源循环绝不能因异常而终止
                logger.exception("源 [%s] 抓取轮次异常", source_name)

            interval = source.interval_seconds or settings.crawler.interval_seconds
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def stop(self) -> None:
        self._stopping = True
        for task in self._source_tasks.values():
            task.cancel()
        if self._source_tasks:
            await asyncio.gather(*self._source_tasks.values(), return_exceptions=True)
        self._source_tasks.clear()
        if self._engine:
            await self._engine.aclose()
            self._engine = None
        logger.info("调度器已停止")

    async def trigger_now(self) -> int:
        """供 API 手动立即触发全部启用源抓取一次，不影响各自的独立调度节奏。"""
        assert self._engine is not None
        return await self._engine.run_all_once()


scheduler = Scheduler()
