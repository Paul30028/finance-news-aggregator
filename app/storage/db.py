"""数据库引擎与会话管理。使用 SQLAlchemy 2.0 异步 API。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.storage.models import Base

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _ensure_sqlite_dir(database_url: str) -> None:
    """如果是 SQLite 文件数据库，提前创建好父目录，避免首次启动报错。"""
    if "sqlite" not in database_url:
        return
    # 形如 sqlite+aiosqlite:///data/news.db
    path_part = database_url.split(":///", 1)[-1]
    if path_part and path_part != ":memory:":
        Path(path_part).parent.mkdir(parents=True, exist_ok=True)


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _ensure_sqlite_dir(settings.storage.database_url)
        _engine = create_async_engine(settings.storage.database_url, echo=False, future=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


async def init_db() -> None:
    """启动时建表（若不存在）。"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def get_session() -> AsyncSession:
    """FastAPI 依赖 / 业务代码通用的会话获取方式：`async with get_session() as s:`"""
    return get_session_factory()()
