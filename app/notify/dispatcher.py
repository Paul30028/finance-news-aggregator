"""
推送通知调度
------------
每轮抓取结束后，若配置启用了 webhook/telegram，会把本轮新入库的文章
（最多 max_items_per_push 条，避免刷屏）分别推送出去。

推送失败只记录日志，绝不能影响主抓取流程；因此这里所有异常都被捕获。
"""
from __future__ import annotations

import logging

import httpx

from app.config import NotifyConfig
from app.processing.cleaner import CleanedArticle

logger = logging.getLogger(__name__)


async def _send_webhook(cfg: NotifyConfig, articles: list[CleanedArticle]) -> None:
    url = cfg.webhook.url
    if not url:
        logger.warning("Webhook 已启用但未配置 URL（环境变量 %s 为空），跳过推送", cfg.webhook.url_env)
        return
    payload = {
        "count": len(articles),
        "items": [
            {
                "title": a.title,
                "link": a.link,
                "source": a.source_name,
                "summary": a.summary,
                "published_at": a.published_at.isoformat(),
            }
            for a in articles
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Webhook 推送失败: %s", exc)


async def _send_telegram(cfg: NotifyConfig, articles: list[CleanedArticle]) -> None:
    token = cfg.telegram.bot_token
    chat_id = cfg.telegram.chat_id
    if not token or not chat_id:
        logger.warning("Telegram 已启用但缺少 Bot Token 或 Chat ID，跳过推送")
        return

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for article in articles:
                text = f"📰 <b>{article.title}</b>\n{article.source_name}\n{article.link}"
                resp = await client.post(
                    api_url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": False,
                    },
                )
                resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Telegram 推送失败: %s", exc)


async def dispatch_new_articles(cfg: NotifyConfig, articles: list[CleanedArticle]) -> None:
    if not articles:
        return
    batch = articles[: cfg.max_items_per_push]

    if cfg.webhook.enabled:
        await _send_webhook(cfg, batch)
    if cfg.telegram.enabled:
        await _send_telegram(cfg, batch)
