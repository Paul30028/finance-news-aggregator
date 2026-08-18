"""
内容清洗模块
------------
把 RawArticle（原始解析结果）清洗为可直接入库的字段：
  - 标题：去除多余空白
  - 摘要：剥离 HTML 标签、压缩空白、截断到配置的最大长度
  - 发布时间：缺失时回退为"当前抓取时间"，保证前端排序不出现 NULL
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from bs4 import BeautifulSoup

from app.crawler.rss_parser import RawArticle


@dataclass
class CleanedArticle:
    title: str
    link: str
    source_name: str
    published_at: datetime
    fetched_at: datetime
    summary: str


def strip_html(raw_html: str) -> str:
    """去除 HTML 标签，仅保留纯文本。"""
    if not raw_html:
        return ""
    text = BeautifulSoup(raw_html, "lxml").get_text(separator=" ", strip=True)
    return " ".join(text.split())


def clean_article(raw: RawArticle, summary_max_length: int = 200) -> CleanedArticle:
    now = datetime.now(timezone.utc)
    summary = strip_html(raw.raw_summary)
    if len(summary) > summary_max_length:
        summary = summary[:summary_max_length].rstrip() + "..."

    return CleanedArticle(
        title=" ".join(raw.title.split()),
        link=raw.link.strip(),
        source_name=raw.source_name,
        published_at=raw.published_at or now,
        fetched_at=now,
        summary=summary,
    )


def is_blocked(title: str, summary: str, block_keywords: list[str]) -> Optional[str]:
    """若标题或摘要命中任一屏蔽词，返回命中的关键词；否则返回 None。"""
    haystack = f"{title} {summary}"
    for kw in block_keywords:
        if kw and kw in haystack:
            return kw
    return None
