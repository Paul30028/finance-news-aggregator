"""
robots.txt 合规检查模块
-----------------------
在抓取任何 URL 之前，都必须先确认该 URL 允许被我们的 User-Agent 抓取。
本模块对每个域名的 robots.txt 做内存缓存（含 TTL），避免重复请求。

同时会解析 robots.txt 中的 `Crawl-delay` 指令，供限速器（rate_limiter.py）
与配置中的 per_domain_min_interval_seconds 取较大值使用，确保不低于站点声明的下限。
"""
from __future__ import annotations

import logging
import time
import urllib.robotparser as robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


@dataclass
class _CachedRobots:
    parser: robotparser.RobotFileParser
    fetched_at: float
    crawl_delay: float | None


class RobotsChecker:
    """按域名缓存并查询 robots.txt 规则。"""

    def __init__(self, user_agent: str, cache_ttl_seconds: int = 3600):
        self._user_agent = user_agent
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, _CachedRobots] = {}

    async def _fetch_robots(self, client: httpx.AsyncClient, origin: str) -> _CachedRobots:
        robots_url = f"{origin}/robots.txt"
        parser = robotparser.RobotFileParser()
        parser.set_url(robots_url)
        try:
            resp = await client.get(robots_url, timeout=10)
            if resp.status_code == 200:
                parser.parse(resp.text.splitlines())
            else:
                # 404 或其他状态码：视为未声明限制，允许抓取（robots 协议的常规约定）
                parser.parse([])
        except httpx.HTTPError as exc:
            logger.warning("获取 robots.txt 失败 (%s)，按允许处理: %s", robots_url, exc)
            parser.parse([])

        crawl_delay = None
        try:
            cd = parser.crawl_delay(self._user_agent)
            crawl_delay = float(cd) if cd is not None else None
        except Exception:  # noqa: BLE001 - robotparser 在个别实现上可能抛出异常
            crawl_delay = None

        return _CachedRobots(parser=parser, fetched_at=time.monotonic(), crawl_delay=crawl_delay)

    async def is_allowed(self, client: httpx.AsyncClient, url: str) -> bool:
        """检查 url 是否允许被抓取。任何异常都保守地放行，但会记录日志。"""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        cached = self._cache.get(origin)
        if cached is None or (time.monotonic() - cached.fetched_at) > self._cache_ttl:
            async with httpx.AsyncClient(headers={"User-Agent": self._user_agent}) as robots_client:
                cached = await self._fetch_robots(robots_client, origin)
            self._cache[origin] = cached

        try:
            return cached.parser.can_fetch(self._user_agent, url)
        except Exception:  # noqa: BLE001
            return True

    def get_crawl_delay(self, url: str) -> float | None:
        """返回该域名 robots.txt 声明的 Crawl-delay（秒），若无声明或未缓存则返回 None。"""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        cached = self._cache.get(origin)
        return cached.crawl_delay if cached else None
