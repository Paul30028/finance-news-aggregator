"""
配置加载模块
------------
负责读取 config/config.yaml 与 config/sources.yaml，合并为强类型的配置对象。
所有模块都应通过 `get_settings()` 获取配置单例，避免各处散落 open()/yaml.load()。

支持 `reload_settings()` 用于运行时热加载（例如通过 API 修改数据源后立即生效）。
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("APP_CONFIG_PATH", BASE_DIR / "config" / "config.yaml"))
SOURCES_PATH = Path(os.environ.get("APP_SOURCES_PATH", BASE_DIR / "config" / "sources.yaml"))


@dataclass
class SourceConfig:
    """单个新闻源的配置。"""

    name: str
    type: str  # "rss" 或 "html"
    url: str
    enabled: bool = True
    category_hint: Optional[str] = None
    list_selector: Optional[str] = None
    # 该源的独立抓取间隔（秒）；为 None 时使用全局 crawler.interval_seconds。
    # 每个源在调度器中拥有独立的轮询循环，互不等待，因此高即时性需求的源
    # 可以单独设置更短的间隔，而不必拖累/被其他低频源拖累。
    interval_seconds: Optional[int] = None
    # 权威度分级，纯展示/筛选用途，不影响抓取逻辑：
    #   "official"   官方权威源（监管机构、央行、交易所等一手信息发布方）
    #   "mainstream" 主流财经媒体（有采编团队、长期信誉的新闻机构）
    #   "aggregator" 聚合/补充源（如 Google News 检索结果，可能转载自其他媒体）
    # 不填视为未分级，前端会显示"未分级"。
    tier: Optional[str] = None


@dataclass
class ThrottleConfig:
    initial_penalty_seconds: float = 30.0
    max_penalty_seconds: float = 1800.0
    penalty_multiplier: float = 2.0


@dataclass
class CrawlerConfig:
    interval_seconds: int = 60
    max_concurrency: int = 5
    per_domain_min_interval_seconds: float = 3.0
    request_timeout_seconds: float = 15.0
    max_retries: int = 3
    retry_backoff_base_seconds: float = 2.0
    throttle_on_429_403: ThrottleConfig = field(default_factory=ThrottleConfig)
    user_agent: str = "FinanceNewsAggregator/1.0"
    respect_robots_txt: bool = True
    robots_cache_ttl_seconds: int = 3600
    summary_max_length: int = 200


@dataclass
class StorageConfig:
    database_url: str = "sqlite+aiosqlite:///data/news.db"


@dataclass
class ClassificationConfig:
    categories: dict[str, list[str]] = field(default_factory=dict)
    block_keywords: list[str] = field(default_factory=list)


@dataclass
class WebhookNotifyConfig:
    enabled: bool = False
    url_env: str = "WEBHOOK_URL"

    @property
    def url(self) -> Optional[str]:
        return os.environ.get(self.url_env)


@dataclass
class TelegramNotifyConfig:
    enabled: bool = False
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TELEGRAM_CHAT_ID"

    @property
    def bot_token(self) -> Optional[str]:
        return os.environ.get(self.bot_token_env)

    @property
    def chat_id(self) -> Optional[str]:
        return os.environ.get(self.chat_id_env)


@dataclass
class NotifyConfig:
    webhook: WebhookNotifyConfig = field(default_factory=WebhookNotifyConfig)
    telegram: TelegramNotifyConfig = field(default_factory=TelegramNotifyConfig)
    max_items_per_push: int = 10


@dataclass
class AppMeta:
    host: str = "0.0.0.0"
    port: int = 8000
    timezone: str = "Asia/Shanghai"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: Optional[str] = "data/app.log"
    max_bytes: int = 5 * 1024 * 1024
    backup_count: int = 3


@dataclass
class Settings:
    app: AppMeta
    crawler: CrawlerConfig
    storage: StorageConfig
    classification: ClassificationConfig
    notify: NotifyConfig
    logging: LoggingConfig
    sources: list[SourceConfig]


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _build_settings() -> Settings:
    raw_app = _load_yaml(CONFIG_PATH)
    raw_sources = _load_yaml(SOURCES_PATH)

    app_raw = raw_app.get("app", {})
    crawler_raw = raw_app.get("crawler", {})
    throttle_raw = crawler_raw.get("throttle_on_429_403", {})
    storage_raw = raw_app.get("storage", {})
    classification_raw = raw_app.get("classification", {})
    notify_raw = raw_app.get("notify", {})
    logging_raw = raw_app.get("logging", {})

    settings = Settings(
        app=AppMeta(
            host=app_raw.get("host", "0.0.0.0"),
            port=int(app_raw.get("port", 8000)),
            timezone=app_raw.get("timezone", "Asia/Shanghai"),
        ),
        crawler=CrawlerConfig(
            interval_seconds=int(crawler_raw.get("interval_seconds", 60)),
            max_concurrency=int(crawler_raw.get("max_concurrency", 5)),
            per_domain_min_interval_seconds=float(
                crawler_raw.get("per_domain_min_interval_seconds", 3.0)
            ),
            request_timeout_seconds=float(crawler_raw.get("request_timeout_seconds", 15.0)),
            max_retries=int(crawler_raw.get("max_retries", 3)),
            retry_backoff_base_seconds=float(
                crawler_raw.get("retry_backoff_base_seconds", 2.0)
            ),
            throttle_on_429_403=ThrottleConfig(
                initial_penalty_seconds=float(throttle_raw.get("initial_penalty_seconds", 30)),
                max_penalty_seconds=float(throttle_raw.get("max_penalty_seconds", 1800)),
                penalty_multiplier=float(throttle_raw.get("penalty_multiplier", 2.0)),
            ),
            user_agent=crawler_raw.get("user_agent", "FinanceNewsAggregator/1.0"),
            respect_robots_txt=bool(crawler_raw.get("respect_robots_txt", True)),
            robots_cache_ttl_seconds=int(crawler_raw.get("robots_cache_ttl_seconds", 3600)),
            summary_max_length=int(crawler_raw.get("summary_max_length", 200)),
        ),
        storage=StorageConfig(
            database_url=storage_raw.get("database_url", "sqlite+aiosqlite:///data/news.db"),
        ),
        classification=ClassificationConfig(
            categories=classification_raw.get("categories", {}) or {},
            block_keywords=classification_raw.get("block_keywords", []) or [],
        ),
        notify=NotifyConfig(
            webhook=WebhookNotifyConfig(**(notify_raw.get("webhook", {}) or {})),
            telegram=TelegramNotifyConfig(**(notify_raw.get("telegram", {}) or {})),
            max_items_per_push=int(notify_raw.get("max_items_per_push", 10)),
        ),
        logging=LoggingConfig(
            level=logging_raw.get("level", "INFO"),
            file=logging_raw.get("file", "data/app.log"),
            max_bytes=int(logging_raw.get("max_bytes", 5 * 1024 * 1024)),
            backup_count=int(logging_raw.get("backup_count", 3)),
        ),
        sources=[SourceConfig(**s) for s in (raw_sources.get("sources", []) or [])],
    )
    return settings


_lock = threading.Lock()
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """获取配置单例；首次调用时加载。"""
    global _settings
    if _settings is None:
        with _lock:
            if _settings is None:
                _settings = _build_settings()
    return _settings


def reload_settings() -> Settings:
    """强制重新从磁盘加载配置（供热加载 API 使用）。"""
    global _settings
    with _lock:
        _settings = _build_settings()
    return _settings


def save_sources(sources: list[SourceConfig]) -> None:
    """将当前 sources 列表写回 config/sources.yaml，供动态增删源后持久化。"""
    payload = {
        "sources": [
            {
                "name": s.name,
                "type": s.type,
                "url": s.url,
                "enabled": s.enabled,
                "category_hint": s.category_hint,
                "list_selector": s.list_selector,
                "interval_seconds": s.interval_seconds,
                "tier": s.tier,
            }
            for s in sources
        ]
    }
    with SOURCES_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)
