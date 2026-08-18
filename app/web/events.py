"""
实时推送广播器（Server-Sent Events）
------------------------------------
新闻的"即时性"不仅体现在抓取频率上，也体现在"抓到之后多快展示给用户"。
本模块提供一个进程内的发布/订阅广播器：抓取引擎每处理完一个源、
有新文章入库，就立即 `publish()` 出去；每个打开着新闻页面的浏览器
通过 `/events/stream`（SSE）订阅，新文章会在几乎零延迟内推送到前端，
而不必等待前端的下一次轮询周期。

设计上刻意保持极简：进程内内存队列即可，不引入 Redis/消息队列等外部依赖；
多worker部署场景下每个worker各自广播，足够单实例/单容器场景使用。
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class NewsBroadcaster:
    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def publish(self, payload: list[dict]) -> None:
        """向所有已连接的订阅者广播新文章（列表形式，通常是一个源本轮的新增结果）。"""
        if not payload:
            return
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # 订阅者消费不及时，丢弃最旧的一条腾出空间，保证不阻塞抓取主流程
                logger.warning("SSE 订阅者队列已满，丢弃最旧的一条推送")
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except asyncio.QueueEmpty:
                    pass


broadcaster = NewsBroadcaster()
