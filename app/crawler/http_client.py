"""
带重试与限速的 HTTP 请求封装
----------------------------
所有对外请求都应经过 `fetch()`，统一处理：
  1. robots.txt 校验
  2. 域名限速等待
  3. 超时与网络错误重试（指数退避）
  4. 429 / 403 自动降速并重试（不会无限重试，超过 max_retries 后放弃并记录日志）
  5. HTTP 条件请求（ETag / Last-Modified）：这是"轮询间隔可以调短而不失礼"的关键——
     若源端内容自上次抓取以来未变化，会返回 304 Not Modified（空响应体），
     不产生解析/存储开销，也不对目标站点造成额外带宽压力。
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import CrawlerConfig
from app.crawler.rate_limiter import DomainRateLimiter
from app.crawler.robots import RobotsChecker

logger = logging.getLogger(__name__)

NOT_MODIFIED = 304


class FetchBlockedError(Exception):
    """robots.txt 禁止抓取该 URL 时抛出。"""


class FetchFailedError(Exception):
    """重试耗尽后仍失败时抛出。"""


class CompliantFetcher:
    """封装了合规检查、限速、重试的抓取器；所有源共用一个实例。"""

    def __init__(self, cfg: CrawlerConfig):
        self._cfg = cfg
        self._robots = RobotsChecker(cfg.user_agent, cfg.robots_cache_ttl_seconds)
        self._limiter = DomainRateLimiter(cfg.per_domain_min_interval_seconds, cfg.throttle_on_429_403)
        self._client = httpx.AsyncClient(
            headers={"User-Agent": cfg.user_agent},
            timeout=cfg.request_timeout_seconds,
            follow_redirects=True,
        )
        # 按 URL 缓存上一次成功响应的 ETag / Last-Modified，用于下一次的条件请求
        self._conditional_cache: dict[str, dict[str, str]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, url: str) -> httpx.Response:
        """抓取一个 URL，返回响应；内部已完成 robots 检查、限速与重试。"""
        if self._cfg.respect_robots_txt:
            allowed = await self._robots.is_allowed(self._client, url)
            if not allowed:
                logger.info("robots.txt 禁止抓取，跳过: %s", url)
                raise FetchBlockedError(url)

        crawl_delay = self._robots.get_crawl_delay(url) if self._cfg.respect_robots_txt else None

        conditional_headers = {}
        cached_meta = self._conditional_cache.get(url)
        if cached_meta:
            if "etag" in cached_meta:
                conditional_headers["If-None-Match"] = cached_meta["etag"]
            if "last_modified" in cached_meta:
                conditional_headers["If-Modified-Since"] = cached_meta["last_modified"]

        last_exc: Exception | None = None
        for attempt in range(1, self._cfg.max_retries + 1):
            await self._limiter.wait_for_slot(url, crawl_delay)
            try:
                resp = await self._client.get(url, headers=conditional_headers or None)
            except httpx.HTTPError as exc:
                last_exc = exc
                backoff = self._cfg.retry_backoff_base_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "请求异常 (%s/%s) %s: %s，%.1fs 后重试",
                    attempt, self._cfg.max_retries, url, exc, backoff,
                )
                await asyncio.sleep(backoff)
                continue

            if resp.status_code in (429, 403):
                penalty = self._limiter.report_blocked(url)
                last_exc = FetchFailedError(f"HTTP {resp.status_code}")
                if attempt < self._cfg.max_retries:
                    logger.warning(
                        "收到 %s (%s/%s) %s，等待 %.1fs 后重试",
                        resp.status_code, attempt, self._cfg.max_retries, url, penalty,
                    )
                    await asyncio.sleep(min(penalty, 60))  # 单次重试内不无限等待，封顶 60s
                continue

            if resp.status_code >= 500:
                last_exc = FetchFailedError(f"HTTP {resp.status_code}")
                backoff = self._cfg.retry_backoff_base_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "服务端错误 %s (%s/%s) %s，%.1fs 后重试",
                    resp.status_code, attempt, self._cfg.max_retries, url, backoff,
                )
                await asyncio.sleep(backoff)
                continue

            # 2xx/3xx/4xx(非429/403) 均视为"拿到响应"，交由上层判断是否解析成功
            self._limiter.report_success(url)
            if resp.status_code == 200:
                new_meta = {}
                if "etag" in resp.headers:
                    new_meta["etag"] = resp.headers["etag"]
                if "last-modified" in resp.headers:
                    new_meta["last_modified"] = resp.headers["last-modified"]
                if new_meta:
                    self._conditional_cache[url] = new_meta
            return resp

        raise FetchFailedError(f"重试 {self._cfg.max_retries} 次后仍失败: {url} ({last_exc})")
