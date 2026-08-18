"""
按域名限速器
------------
核心合规组件：确保对同一域名的请求间隔不低于配置的最小间隔；
当目标站点返回 429 (Too Many Requests) 或 403 (Forbidden，常见于反爬拦截) 时，
自动对该域名施加"降速惩罚"，惩罚时间随连续命中次数指数增长（封顶），
命中一次成功请求后逐步恢复正常间隔。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.config import ThrottleConfig

logger = logging.getLogger(__name__)


@dataclass
class _DomainState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_request_at: float = 0.0
    penalty_seconds: float = 0.0
    consecutive_blocks: int = 0


class DomainRateLimiter:
    """一个域名一把锁 + 一个"下次最早可请求时间"状态。"""

    def __init__(self, base_interval_seconds: float, throttle_cfg: ThrottleConfig):
        self._base_interval = base_interval_seconds
        self._throttle_cfg = throttle_cfg
        self._states: dict[str, _DomainState] = {}

    def _state_for(self, url: str) -> _DomainState:
        domain = urlparse(url).netloc
        if domain not in self._states:
            self._states[domain] = _DomainState()
        return self._states[domain]

    async def wait_for_slot(self, url: str, robots_crawl_delay: float | None = None) -> None:
        """在允许发起请求之前阻塞等待，确保满足最小间隔 + 当前惩罚时间。"""
        state = self._state_for(url)
        async with state.lock:
            min_interval = self._base_interval
            if robots_crawl_delay is not None:
                min_interval = max(min_interval, robots_crawl_delay)
            required_interval = min_interval + state.penalty_seconds

            elapsed = time.monotonic() - state.last_request_at
            wait_seconds = required_interval - elapsed
            if wait_seconds > 0:
                logger.debug("域名限速等待 %.2fs (%s)", wait_seconds, urlparse(url).netloc)
                await asyncio.sleep(wait_seconds)
            state.last_request_at = time.monotonic()

    def report_success(self, url: str) -> None:
        """请求成功后，逐步降低惩罚（避免一次恢复后又被立刻打回高频）。"""
        state = self._state_for(url)
        if state.consecutive_blocks > 0:
            state.consecutive_blocks = max(0, state.consecutive_blocks - 1)
        if state.consecutive_blocks == 0:
            state.penalty_seconds = 0.0

    def report_blocked(self, url: str) -> float:
        """收到 429/403 时调用，返回本次施加的总惩罚时间（秒），用于日志展示。"""
        state = self._state_for(url)
        cfg = self._throttle_cfg
        state.consecutive_blocks += 1
        if state.penalty_seconds <= 0:
            state.penalty_seconds = cfg.initial_penalty_seconds
        else:
            state.penalty_seconds = min(
                state.penalty_seconds * cfg.penalty_multiplier, cfg.max_penalty_seconds
            )
        domain = urlparse(url).netloc
        logger.warning(
            "域名 %s 触发 429/403，第 %d 次，降速惩罚提升至 %.1fs",
            domain,
            state.consecutive_blocks,
            state.penalty_seconds,
        )
        return state.penalty_seconds
