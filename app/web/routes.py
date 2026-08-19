"""
Web 路由
--------
提供两类接口：
  1. HTML 页面（Jinja2 + HTMX）：/ 首页展示新闻列表，支持分类/来源/关键词筛选，
     列表区域通过 HTMX 定时轮询 /partials/news-list 实现"近实时"刷新，无需手写 JS。
  2. JSON API：供程序化访问，以及前端源管理表单调用。
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.analysis.briefing import build_briefing
from app.config import SourceConfig, get_settings, reload_settings, save_sources
from app.processing.signals import conclusion_for_codes, decode_signal_tags, is_alert_score
from app.scheduler import scheduler
from app.storage.db import get_session
from app.storage.repository import (
    count_articles,
    distinct_categories,
    distinct_sources,
    list_articles,
    list_source_stats,
)
from app.web.events import broadcaster
from app.web.schemas import SourceIn

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

PAGE_SIZE = 30

# NEW 徽标的时间窗口：fetched_at 在这个时间内的文章视为"刚抓到"
RECENT_THRESHOLD_SECONDS = 300


def _as_aware_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _relative_time(dt: datetime) -> str:
    """把时间戳格式化为"刚刚/N分钟前/N小时前/N天前"，用于强化即时性的直观呈现。"""
    now = datetime.now(timezone.utc)
    seconds = (now - _as_aware_utc(dt)).total_seconds()
    if seconds < 60:
        return "刚刚"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}分钟前"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours}小时前"
    days = int(hours // 24)
    return f"{days}天前"


def _is_recent(dt: datetime, threshold_seconds: int = RECENT_THRESHOLD_SECONDS) -> bool:
    now = datetime.now(timezone.utc)
    return (now - _as_aware_utc(dt)).total_seconds() < threshold_seconds


# 权威度分级的展示顺序与中文标签，官方权威源排在最前，体现"优先呈现权威来源"
TIER_LABELS = {
    "official": "官方权威",
    "mainstream": "主流媒体",
    "aggregator": "聚合补充",
}


def _tier_label(tier: str | None) -> str:
    return TIER_LABELS.get(tier or "", "未分级")


def _article_is_alert(sentiment_score: int) -> bool:
    return is_alert_score(sentiment_score, get_settings().signals.alert_threshold)


def _article_conclusion(decoded_signals: list[tuple[str, str, int]]) -> tuple[str, str]:
    return conclusion_for_codes([code for code, _, _ in decoded_signals])


templates.env.filters["relative_time"] = _relative_time
templates.env.filters["is_recent"] = _is_recent
templates.env.filters["tier_label"] = _tier_label
templates.env.filters["signals"] = decode_signal_tags
templates.env.filters["is_alert_score"] = _article_is_alert
templates.env.filters["conclusion"] = _article_conclusion


async def _fetch_page(
    category: str | None, source: str | None, tier: str | None, keyword: str | None, page: int
):
    offset = (page - 1) * PAGE_SIZE
    async with get_session() as session:
        articles = await list_articles(
            session, category=category, source_name=source, tier=tier, keyword=keyword,
            limit=PAGE_SIZE, offset=offset,
        )
        total = await count_articles(
            session, category=category, source_name=source, tier=tier, keyword=keyword
        )
        categories = await distinct_categories(session)
        sources = await distinct_sources(session)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    return articles, total, categories, sources, total_pages


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    category: str | None = Query(default=None),
    source: str | None = Query(default=None),
    tier: str | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
):
    articles, total, categories, sources, total_pages = await _fetch_page(
        category, source, tier, q, page
    )
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "articles": articles,
            "total": total,
            "categories": categories,
            "sources": sources,
            "tiers": TIER_LABELS,
            "page": page,
            "total_pages": total_pages,
            "current_category": category or "",
            "current_source": source or "",
            "current_tier": tier or "",
            "current_q": q or "",
            "refresh_seconds": get_settings().crawler.interval_seconds,
        },
    )


@router.get("/partials/news-list", response_class=HTMLResponse)
async def partial_news_list(
    request: Request,
    category: str | None = Query(default=None),
    source: str | None = Query(default=None),
    tier: str | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
):
    articles, total, _categories, _sources, total_pages = await _fetch_page(
        category, source, tier, q, page
    )
    return templates.TemplateResponse(
        "partials/news_list.html",
        {
            "request": request,
            "articles": articles,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "current_category": category or "",
            "current_source": source or "",
            "current_tier": tier or "",
            "current_q": q or "",
        },
    )


@router.get("/insights", response_class=HTMLResponse)
async def insights_page(request: Request, window: int = Query(default=24, ge=1, le=168)):
    """策略简报页：基于关键词信号规则对窗口内新闻做聚合统计，仅供参考、不构成投资建议。"""
    async with get_session() as session:
        briefing = await build_briefing(session, window_hours=window)
    return templates.TemplateResponse(
        "insights.html",
        {"request": request, "briefing": briefing, "window": window},
    )


@router.get("/api/insights")
async def api_insights(window: int = Query(default=24, ge=1, le=168)):
    async with get_session() as session:
        briefing = await build_briefing(session, window_hours=window)

    def _article_dict(a):
        return {
            "id": a.id,
            "title": a.title,
            "link": a.link,
            "source": a.source_name,
            "tier": a.source_tier,
            "category": a.category,
            "sentiment_score": a.sentiment_score,
            "signals": [{"code": c, "label": l, "polarity": p} for c, l, p in a.signals],
            "watch_note": a.watch_note,
            "confidence": a.confidence,
            "is_alert": a.is_alert,
            "published_at": a.published_at.isoformat(),
        }

    return {
        "window_hours": briefing.window_hours,
        "generated_at": briefing.generated_at.isoformat(),
        "total_articles": briefing.total_articles,
        "bullish_count": briefing.bullish_count,
        "bearish_count": briefing.bearish_count,
        "neutral_count": briefing.neutral_count,
        "top_bullish": [_article_dict(a) for a in briefing.top_bullish],
        "top_bearish": [_article_dict(a) for a in briefing.top_bearish],
        "alerts": [_article_dict(a) for a in briefing.alerts],
        "category_counts": [{"category": c.category, "count": c.count} for c in briefing.category_counts],
        "signal_counts": [
            {"code": s.code, "label": s.label, "polarity": s.polarity, "count": s.count}
            for s in briefing.signal_counts
        ],
        "disclaimer": "以上内容基于公开新闻标题/摘要的关键词规则匹配自动生成，仅供参考，不构成任何投资建议。",
    }


@router.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    settings = get_settings()
    async with get_session() as session:
        stats = {s.source_name: s for s in await list_source_stats(session)}
    return templates.TemplateResponse(
        "sources.html",
        {"request": request, "sources": settings.sources, "stats": stats},
    )


# ------------------------------- JSON API -------------------------------


@router.get("/api/articles")
async def api_articles(
    category: str | None = None,
    source: str | None = None,
    tier: str | None = None,
    q: str | None = None,
    page: int = 1,
):
    articles, total, _c, _s, total_pages = await _fetch_page(category, source, tier, q, page)
    return {
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "items": [
            {
                "id": a.id,
                "title": a.title,
                "link": a.link,
                "source": a.source_name,
                "tier": a.source_tier,
                "category": a.category,
                "summary": a.summary,
                "published_at": a.published_at.isoformat(),
                "fetched_at": a.fetched_at.isoformat(),
            }
            for a in articles
        ],
    }


@router.get("/api/sources")
async def api_list_sources():
    settings = get_settings()
    async with get_session() as session:
        stats = {s.source_name: s for s in await list_source_stats(session)}
    return [
        {
            "name": s.name,
            "type": s.type,
            "url": s.url,
            "enabled": s.enabled,
            "category_hint": s.category_hint,
            "list_selector": s.list_selector,
            "interval_seconds": s.interval_seconds,
            "tier": s.tier,
            "last_run_at": stats[s.name].last_run_at.isoformat() if s.name in stats and stats[s.name].last_run_at else None,
            "last_error": stats[s.name].last_error if s.name in stats else None,
            "total_new": stats[s.name].total_new if s.name in stats else 0,
        }
        for s in settings.sources
    ]


@router.post("/api/sources")
async def api_add_source(source_in: SourceIn):
    settings = get_settings()
    existing = [s for s in settings.sources if s.name != source_in.name]
    existing.append(SourceConfig(**source_in.model_dump()))
    save_sources(existing)
    reload_settings()
    # 新源立即拥有自己的独立抓取循环并马上抓一次，无需等待重启或下一个全局周期
    scheduler.sync_sources()
    return JSONResponse({"ok": True, "message": f"源 '{source_in.name}' 已保存，正在立即抓取"})


@router.delete("/api/sources/{name}")
async def api_delete_source(name: str):
    settings = get_settings()
    remaining = [s for s in settings.sources if s.name != name]
    save_sources(remaining)
    reload_settings()
    scheduler.sync_sources()
    return JSONResponse({"ok": True, "message": f"源 '{name}' 已删除"})


@router.post("/api/sources/{name}/toggle")
async def api_toggle_source(name: str):
    settings = get_settings()
    updated = []
    found = False
    for s in settings.sources:
        if s.name == name:
            s.enabled = not s.enabled
            found = True
        updated.append(s)
    if not found:
        return JSONResponse({"ok": False, "message": "源不存在"}, status_code=404)
    save_sources(updated)
    reload_settings()
    scheduler.sync_sources()
    return JSONResponse({"ok": True})


@router.post("/api/reload")
async def api_reload():
    """重新从磁盘加载 config.yaml / sources.yaml，并同步调度器（新源立即开始抓取）。"""
    reload_settings()
    scheduler.sync_sources()
    return JSONResponse({"ok": True, "message": "配置已重新加载"})


@router.post("/api/crawl-now")
async def api_crawl_now():
    """手动立即触发全部源抓取一次，不影响各自的独立调度节奏。"""
    new_count = await scheduler.trigger_now()
    return JSONResponse({"ok": True, "new_articles": new_count})


@router.get("/events/stream")
async def events_stream(request: Request):
    """SSE 实时推送：新文章一入库就立即推给所有打开着页面的浏览器，无需等待轮询。"""
    queue = broadcaster.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"  # 保活注释行，防止中间代理断开空闲连接
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
