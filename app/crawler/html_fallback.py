"""
网页兜底抓取模块（补充手段，非首选）
------------------------------------
仅在源配置中 type=html 时使用。设计原则：
  - 只做"列表页 -> 标题+链接"级别的通用抽取，不针对特定站点写死解析逻辑，
    具体的 CSS 选择器由 sources.yaml 中的 list_selector 指定。
  - 不做正文全文抓取/存储，只尝试获取一段摘要文本（可选，失败则留空），
    避免对目标站点产生不必要的额外请求压力，也规避版权风险。
  - 同样经过 CompliantFetcher，因此天然享有 robots.txt 检查与限速。
"""
from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from app.crawler.rss_parser import RawArticle

logger = logging.getLogger(__name__)


def extract_list_items(html_text: str, list_selector: str, base_url: str, source_name: str) -> list[RawArticle]:
    """从列表页 HTML 中，按 CSS 选择器提取 (标题, 链接) 对。"""
    from urllib.parse import urljoin

    soup = BeautifulSoup(html_text, "lxml")
    items: list[RawArticle] = []
    for a_tag in soup.select(list_selector):
        title = a_tag.get_text(strip=True)
        href = a_tag.get("href")
        if not title or not href:
            continue
        link = urljoin(base_url, href)
        items.append(
            RawArticle(
                title=title,
                link=link,
                published_at=None,  # 列表页通常不带精确时间，清洗阶段会回退为抓取时间
                raw_summary="",
                source_name=source_name,
            )
        )
    return items


def extract_summary(html_text: str, max_length: int = 200) -> str:
    """从详情页 HTML 中做一个非常轻量的摘要提取：取正文区域前 N 段文本。

    这里刻意不引入复杂的正文抽取算法（如 readability），
    以保持依赖精简；如需更精准的正文抽取，可在此函数中替换实现。
    """
    soup = BeautifulSoup(html_text, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]
    text = " ".join(p for p in paragraphs if p)
    text = " ".join(text.split())
    if len(text) > max_length:
        text = text[:max_length].rstrip() + "..."
    return text
