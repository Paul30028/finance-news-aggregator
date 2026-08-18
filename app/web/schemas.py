"""Web API 的请求/响应模型（Pydantic）。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class SourceIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    type: str = Field(..., pattern="^(rss|html)$")
    url: str = Field(..., min_length=1)
    enabled: bool = True
    category_hint: Optional[str] = None
    list_selector: Optional[str] = None
    # 该源专属抓取间隔（秒），留空则使用全局 crawler.interval_seconds
    interval_seconds: Optional[int] = Field(default=None, ge=10)
    # 权威度分级：official(官方权威) / mainstream(主流媒体) / aggregator(聚合补充)
    tier: Optional[str] = Field(default=None, pattern="^(official|mainstream|aggregator)$")


class SourceOut(SourceIn):
    pass
