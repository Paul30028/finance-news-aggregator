"""
SQLAlchemy ORM 模型
--------------------
默认使用 SQLite（通过 aiosqlite 异步驱动），后续可通过修改
config/config.yaml 中的 storage.database_url 无缝切换到 PostgreSQL，
无需改动本文件（前提是不使用 SQLite 专属方言特性，这里没有用到）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Article(Base):
    """一条已入库的财经新闻。"""

    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 去重指纹：sha256(归一化标题 | 归一化链接)，唯一索引保证数据库层面也不会重复写入
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    title: Mapped[str] = mapped_column(String(500))
    link: Mapped[str] = mapped_column(String(1000))
    source_name: Mapped[str] = mapped_column(String(200), index=True)
    # 抓取时该源配置的权威度分级快照（official/mainstream/aggregator/None），
    # 冗余存一份是为了能直接按权威度筛选新闻，不必每次都去关联当前的 sources.yaml
    # ——即便之后源的分级被修改甚至源被删除，历史文章的分级记录依然准确。
    source_tier: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(50), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")

    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_articles_category_published", "category", "published_at"),
    )


class SourceStat(Base):
    """每个数据源的抓取统计，用于 Web 界面展示健康状况。"""

    __tablename__ = "source_stats"

    source_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_fetched: Mapped[int] = mapped_column(Integer, default=0)
    total_new: Mapped[int] = mapped_column(Integer, default=0)
