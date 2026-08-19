"""
推送通知调度
------------
两类独立的推送：

1. `dispatch_new_articles` —— 常规推送，每轮抓取结束后把本轮新入库的文章
   （最多 max_items_per_push 条，避免刷屏）原样推送出去。
2. `dispatch_signal_alerts` —— "重点信号"推送，只针对情绪分绝对值达到
   `signals.alert_threshold` 的文章，内容更完整（附带关注建议与免责声明），
   与常规推送走同样的 webhook/telegram 通道但格式不同，方便接收方区分优先级。

推送失败只记录日志，绝不能影响主抓取流程；因此这里所有异常都被捕获。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.config import NotifyConfig
from app.processing.cleaner import CleanedArticle

logger = logging.getLogger(__name__)


@dataclass
class SignalAlert:
    """一条"重点信号"提醒的展示内容。"""

    title: str
    link: str
    source_name: str
    sentiment_score: int
    signal_labels: list[str]
    watch_note: str
    confidence: str


DISCLAIMER = "以上内容基于关键词规则匹配自动生成，仅供参考，不构成投资建议。"


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


async def _send_webhook_alerts(cfg: NotifyConfig, alerts: list[SignalAlert]) -> None:
    url = cfg.webhook.url
    if not url:
        logger.warning("Webhook 已启用但未配置 URL，跳过重点信号推送")
        return
    payload = {
        "type": "signal_alert",
        "count": len(alerts),
        "disclaimer": DISCLAIMER,
        "items": [
            {
                "title": a.title,
                "link": a.link,
                "source": a.source_name,
                "sentiment_score": a.sentiment_score,
                "signals": a.signal_labels,
                "watch_note": a.watch_note,
                "confidence": a.confidence,
            }
            for a in alerts
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Webhook 重点信号推送失败: %s", exc)


async def _send_telegram_alerts(cfg: NotifyConfig, alerts: list[SignalAlert]) -> None:
    token = cfg.telegram.bot_token
    chat_id = cfg.telegram.chat_id
    if not token or not chat_id:
        logger.warning("Telegram 已启用但缺少 Bot Token 或 Chat ID，跳过重点信号推送")
        return

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for a in alerts:
                direction = "利好" if a.sentiment_score > 0 else "利空"
                signals_text = "、".join(a.signal_labels) if a.signal_labels else "-"
                text = (
                    f"🔔 <b>重点信号 [{direction}]</b> {a.title}\n"
                    f"来源: {a.source_name}\n"
                    f"命中信号: {signals_text}\n"
                    f"{('建议关注: ' + a.watch_note) if a.watch_note else ''}\n"
                    f"{a.link}\n"
                    f"<i>{DISCLAIMER}</i>"
                )
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
        logger.error("Telegram 重点信号推送失败: %s", exc)


async def dispatch_signal_alerts(cfg: NotifyConfig, alerts: list[SignalAlert]) -> None:
    """发送"重点信号"独立提醒。仅在 push_alerts 开启且对应通道启用时才会调用（见 engine.py）。"""
    if not alerts:
        return
    batch = alerts[: cfg.max_items_per_push]

    if cfg.webhook.enabled:
        await _send_webhook_alerts(cfg, batch)
    if cfg.telegram.enabled:
        await _send_telegram_alerts(cfg, batch)
